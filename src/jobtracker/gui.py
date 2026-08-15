"""
Local web GUI — `jobtracker gui`.

Every route is a thin wrapper around the exact same functions the CLI
calls: apps.queue/add/set_status, queries.scored_jobs, Criteria.load,
config_editor. No decision logic lives here twice — this module is
presentation only, same principle as browse.py's curses loop calling
apply_selected()/skip_selected() rather than reimplementing them.

Each request opens its own connection via db.connection.session(),
mirroring how every CLI command does — no global connection shared
across requests, no extra state to reason about.
"""

from __future__ import annotations

import webbrowser
from threading import Lock, Thread, Timer

from flask import Flask, flash, redirect, render_template, request, url_for

from jobtracker import browser_launcher, paths
from jobtracker import settings_presets as presets
from jobtracker.config_editor import (
    EditableSettings,
    Tier,
    add_boards,
    apply_editable,
    load_editable,
    remove_board,
)
from jobtracker.db import applications as apps
from jobtracker.db.applications import GHOST_THRESHOLD_DAYS
from jobtracker.db.connection import session
from jobtracker.filters import Criteria
from jobtracker.poller import poll_all
from jobtracker.queries import scored_jobs
from jobtracker.resolver import resolve_all
from jobtracker.us_cities import US_TECH_CITIES

PIPELINE_ORDER = [
    "queued", "skipped", "submitted", "acknowledged",
    "screening", "interview", "offer", "rejected",
]

# Polling can take from seconds to a couple minutes depending on how
# many boards are configured — too long to hold a request open, so it
# runs in a background thread and this is the only shared state this
# module has. A lock instead of just checking the flag: two rapid
# clicks on "Poll now" starting two overlapping poll runs would be a
# real, if minor, bug (double-counted board fetches racing on the same
# connection-per-board pattern), not just wasted work.
_poll_lock = Lock()
_poll_state = {"running": False, "boards_done": 0, "boards_total": 0}


def _start_poll() -> bool:
    """Kick off a background poll. Returns False if one's already running."""
    with _poll_lock:
        if _poll_state["running"]:
            return False
        _poll_state["running"] = True
        _poll_state["boards_done"] = 0
        _poll_state["boards_total"] = 0

    def _board_done(ats: str, slug: str) -> None:
        _poll_state["boards_done"] += 1

    def worker() -> None:
        try:
            with session() as conn:
                criteria = Criteria.load()
                _poll_state["boards_total"] = sum(len(s) for s in criteria.boards.values())
                poll_all(conn, on_board_done=_board_done)
        finally:
            _poll_state["running"] = False

    Thread(target=worker, daemon=True).start()
    return True


# Same background-thread-plus-lock pattern as polling, for the same
# reason: resolving a company can take several seconds (up to ~12
# probes across 3 ATSes, each with a courtesy delay), and a list of
# them would block a request far too long.
#
# Results go in this dict rather than through flash() — flash() needs
# an active request/session to write its cookie back through, which a
# background thread finishing after its triggering request already
# returned doesn't have. Settings reads found/missing directly instead,
# the same way it already reads polling_in_progress.
_resolve_lock = Lock()
_resolve_state: dict = {"running": False, "found": [], "missing": []}


def _start_company_resolve(names: list[str]) -> bool:
    """Kick off a background resolve-and-add. Returns False if one's already running."""
    with _resolve_lock:
        if _resolve_state["running"]:
            return False
        _resolve_state["running"] = True
        _resolve_state["found"] = []
        _resolve_state["missing"] = []

    def worker() -> None:
        try:
            results = resolve_all(names)
            found = [r for r in results if r.resolved]
            add_boards([(r.ats, r.slug) for r in found])
            _resolve_state["found"] = [r.name for r in found]
            _resolve_state["missing"] = [r.name for r in results if not r.resolved]
        finally:
            _resolve_state["running"] = False
        # Chained on purpose: newly added companies have zero postings
        # until something actually polls them, so "add companies" that
        # doesn't lead into fetching their jobs would just relocate the
        # "now what?" moment instead of resolving it.
        if found:
            _start_poll()

    Thread(target=worker, daemon=True).start()
    return True


# Explicit template_folder rather than Flask's automatic package-relative
# resolution — that guessing works from a normal source checkout, but
# a PyInstaller-frozen app's module locations don't follow the same
# layout, so this points at the one true source of truth (paths.py)
# instead of hoping Flask's default guess still lines up.
app = Flask(__name__, template_folder=str(paths.bundled_resource("templates")))
app.secret_key = "jobtracker-local"  # local-only tool; no real session security needed


@app.context_processor
def inject_poll_status():
    return {
        "polling_in_progress": _poll_state["running"],
        "poll_boards_done": _poll_state["boards_done"],
        "poll_boards_total": _poll_state["boards_total"],
        "resolving_companies": _resolve_state["running"],
    }


@app.route("/settings/companies", methods=["POST"])
def add_companies():
    names = [n.strip() for n in request.form.get("company_names", "").splitlines() if n.strip()]
    if not names:
        flash("Type at least one company name first.")
    elif not _start_company_resolve(names):
        flash("Already resolving companies — hang tight.")
    else:
        flash(f"Looking up {len(names)} compan{'y' if len(names) == 1 else 'ies'}… this can take a minute.")
    return redirect(url_for("settings"))


@app.route("/settings/companies/remove", methods=["POST"])
def remove_company():
    ats = request.form.get("ats", "")
    slug = request.form.get("slug", "")
    if ats and slug:
        remove_board(ats, slug)
        flash(f"Removed {slug}.")
    return redirect(url_for("settings"))


@app.route("/poll", methods=["POST"])
def poll():
    if not _start_poll():
        flash("Already polling — hang tight.")
    else:
        flash("Polling started in the background. This can take a minute or two for a lot of boards — new postings will show up here as it finishes.")
    return redirect(request.referrer or url_for("index"))


def _current_queue(entry_level: bool):
    """Shared by the page route and the JSON endpoint — one query, two renderings."""
    with session() as conn:
        criteria = Criteria.load()
        scored = scored_jobs(conn, criteria, entry_level_only=entry_level)
        entries = apps.queue(conn, scored, limit=200)
        apps.save_snapshot(conn, entries)
    has_boards = any(criteria.boards.values())
    return entries, has_boards


@app.route("/")
def index():
    entry_level = request.args.get("entry_level") == "1"
    entries, has_boards = _current_queue(entry_level)
    return render_template(
        "queue.html", entries=entries, entry_level=entry_level, has_boards=has_boards, active="queue"
    )


@app.route("/api/queue")
def queue_data():
    """
    JSON form of the queue, for the page's own incremental-refresh JS —
    not a public API, just a way to fetch fresh entries without a full
    page reload. Each entry carries pre-rendered card HTML from the
    same _posting_card.html partial the page itself uses, so a newly
    fetched card is pixel-identical to one rendered on load rather than
    a second, JS-side copy of the markup that could drift out of sync.
    """
    entry_level = request.args.get("entry_level") == "1"
    entries, _ = _current_queue(entry_level)
    return {
        "count": len(entries),
        "entries": [
            {"job_id": e.job_id, "html": render_template("_posting_card.html", e=e)}
            for e in entries
        ],
    }


@app.route("/api/poll-status")
def poll_status():
    """JSON status the base template's JS polls to update the banner and trigger a queue refresh."""
    return {
        "polling": _poll_state["running"],
        "poll_done": _poll_state["boards_done"],
        "poll_total": _poll_state["boards_total"],
        "resolving": _resolve_state["running"],
    }


@app.route("/apply/<int:job_id>")
def apply(job_id: int):
    """
    Record the decision and open the posting in a real browser.

    Deliberately not a redirect: this GUI runs inside a WKWebView-
    backed native window, which has none of your saved passwords,
    autofill, or extensions — redirecting *that* window to the
    application page would trade all of that away, and would also
    navigate the app itself off the queue. browser_launcher opens a
    separate, real browser process instead, and the app stays put.
    """
    with session() as conn:
        row = conn.execute(
            "SELECT absolute_url FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return redirect(url_for("index"))
        apps.add(conn, job_id)

    browser_launcher.open_url(row["absolute_url"], browser=load_editable().browser)
    return redirect(url_for("index", entry_level=request.args.get("entry_level")))


@app.route("/skip/<int:job_id>")
def skip(job_id: int):
    with session() as conn:
        apps.add(conn, job_id, status="skipped")
    return redirect(url_for("index", entry_level=request.args.get("entry_level")))


@app.route("/status")
def status():
    with session() as conn:
        pipeline = apps.pipeline(conn)
        queued = apps.list_by_status(conn, "queued")
        ghosted = apps.ghosted(conn)

    submitted = sum(
        pipeline.get(s, 0)
        for s in ("submitted", "acknowledged", "screening", "interview", "offer")
    )
    responded = sum(
        pipeline.get(s, 0) for s in ("acknowledged", "screening", "interview", "offer")
    )
    response_rate = round(responded / submitted * 100) if submitted else None

    return render_template(
        "status.html",
        pipeline=pipeline,
        pipeline_order=PIPELINE_ORDER,
        queued=queued,
        ghosted=ghosted,
        ghost_threshold=GHOST_THRESHOLD_DAYS,
        submitted=submitted,
        responded=responded,
        response_rate=response_rate,
        active="status",
    )


@app.route("/mark-submitted/<int:job_id>", methods=["POST"])
def mark_submitted(job_id: int):
    with session() as conn:
        try:
            apps.set_status(conn, job_id, "submitted")
        except apps.TransitionError:
            pass  # already moved on; the status page will just reflect current state
    return redirect(url_for("status"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        city_tiers = [
            Tier(score=presets.PRIORITY_SCORE[priority], cities=cities)
            for priority, cities in zip(
                request.form.getlist("tier_priority"), request.form.getlist("tier_cities")
            )
            if cities.strip()
        ]
        tiers = presets.with_remote(city_tiers, remote_on=request.form.get("remote_ok") == "on")
        tiers = presets.with_hybrid(tiers, hybrid_on=request.form.get("hybrid_ok") == "on")

        level = request.form.get("experience_level", "custom")
        if level == "custom":
            preferred = _lines(request.form.get("seniority_preferred", ""))
            penalized = _lines(request.form.get("seniority_penalized", ""))
        else:
            preferred, penalized = presets.EXPERIENCE_LEVELS[level]

        updated = EditableSettings(
            tiers=tiers,
            role_include=_lines(request.form.get("role_include", "")),
            role_exclude=_lines(request.form.get("role_exclude", "")),
            seniority_preferred=preferred,
            seniority_penalized=penalized,
            max_years=int(request.form.get("max_years", 2)),
            penalty_per_year=int(request.form.get("penalty_per_year", 15)),
            min_score=presets.STRICTNESS_LEVELS[request.form.get("strictness", "balanced")],
            browser=request.form.get("browser", browser_launcher.SYSTEM_DEFAULT),
        )
        apply_editable(updated)
        return redirect(url_for("settings"))

    current = load_editable()
    city_tiers, remote_on = presets.split_remote(current.tiers)
    city_tiers, hybrid_on = presets.split_hybrid(city_tiers)
    return render_template(
        "settings.html",
        settings=current,
        city_tiers=[
            {"cities": t.cities, "priority": presets.closest_priority(t.score)} for t in city_tiers
        ],
        remote_on=remote_on,
        hybrid_on=hybrid_on,
        priority_levels=presets.PRIORITY_LEVELS,
        experience_level=presets.guess_experience_level(
            current.seniority_preferred, current.seniority_penalized
        ),
        experience_labels=presets.EXPERIENCE_LABELS,
        strictness=presets.closest_strictness(current.min_score),
        strictness_labels=presets.STRICTNESS_LABELS,
        available_browsers=browser_launcher.available_browsers(),
        us_cities=US_TECH_CITIES,
        company_count=sum(len(s) for s in current.boards.values()),
        resolve_found=_resolve_state["found"],
        resolve_missing=_resolve_state["missing"],
        active="settings",
    )


def _lines(text: str) -> list[str]:
    """Textarea contents -> one entry per non-blank line."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def run(host: str = "127.0.0.1", port: int = 8765, native_window: bool = True) -> None:
    """
    Start the local server and present it.

    native_window=True (the default) wraps the app in an actual OS
    window via pywebview — WKWebView on macOS — instead of opening a
    tab in whichever browser happens to be the system default: its own
    resizable window, no tabs or address bar, closes independently of
    anything else you have open. Flask runs in a background thread
    because pywebview's event loop has to own the main thread.

    native_window=False falls back to the plain browser-tab behavior,
    for environments without a display server (or anyone who just
    prefers a normal tab).
    """
    url = f"http://{host}:{port}"

    if not native_window:
        Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"jobtracker GUI running at {url} (Ctrl+C to stop)")
        app.run(host=host, port=port, debug=False, use_reloader=False)
        return

    import webview

    server = Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    server.start()

    webview.create_window("jobtracker", url, width=1050, height=800, min_size=(700, 500))
    webview.start()
