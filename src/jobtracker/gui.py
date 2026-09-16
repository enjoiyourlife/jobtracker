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

import csv
import io
import re
import webbrowser
from datetime import datetime, timezone
from threading import Lock, Thread, Timer

from flask import Flask, Response, flash, redirect, render_template, request, url_for

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
from jobtracker.db import jobs as jobs_repo
from jobtracker.db.applications import GHOST_THRESHOLD_DAYS
from jobtracker.db.connection import session
from jobtracker.filters import Criteria
from jobtracker.poller import poll_all
from jobtracker.queries import invalidate_cache as invalidate_scored_cache
from jobtracker.queries import scored_jobs
from jobtracker.resolver import resolve_all
from jobtracker.company_directory import COMPANY_DIRECTORY
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
        except Exception:
            # Per-board failures are already caught inside poll_board;
            # reaching here means something broke outside that (a
            # malformed config.yaml, a DB connection failure) — rare,
            # but a daemon thread dying with an unhandled traceback to
            # a log nobody's watching isn't meaningfully "handled"
            # either way. At minimum the button un-disables so a retry
            # is possible instead of looking permanently stuck.
            pass
        finally:
            _poll_state["running"] = False
            # Unconditional, even after a partial/failed poll: whatever
            # jobs did land before the failure make the cached scores
            # stale either way, and recomputing once is cheap next to
            # silently showing wrong results.
            invalidate_scored_cache()

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
        found: list = []
        try:
            results = resolve_all(names)
            found = [r for r in results if r.resolved]
            add_boards([(r.ats, r.slug) for r in found])
            invalidate_scored_cache()
            _resolve_state["found"] = [r.name for r in found]
            _resolve_state["missing"] = [r.name for r in results if not r.resolved]
        except Exception:
            # A daemon thread that raises doesn't crash the app — Python
            # just prints the traceback to stderr and moves on — but
            # "silently prints to a log nobody's watching" isn't the
            # same as handling it. Treating every requested name as
            # unresolved means the user still sees *something* went
            # wrong on Settings instead of the request just vanishing.
            _resolve_state["found"] = []
            _resolve_state["missing"] = names
        finally:
            _resolve_state["running"] = False
            # Chained on purpose: newly added companies have zero
            # postings until something actually polls them, so "add
            # companies" that doesn't lead into fetching their jobs
            # would just relocate the "now what?" moment instead of
            # resolving it. Inside finally (not after) so a mid-resolve
            # exception can't skip past it with `found` still empty —
            # `found` is seeded to [] up front for exactly that case.
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


@app.template_filter("company_name")
def _company_name(name: str) -> str:
    """
    Display-cased company name.

    Greenhouse's API returns a real company_name ("Jane Street",
    "Stripe") that this leaves untouched. Ashby and Lever expose no
    such field, so their client modules fall back to the raw board
    slug verbatim ("sentry", "hinge-health") — title-casing after
    swapping separators for spaces turns that into "Sentry" and "Hinge
    Health" without needing to know which ATS a given posting came
    from. Not perfect (a squashed slug like "moderntreasury" has no
    separator to recover "Modern Treasury" from), but strictly better
    than showing a raw slug next to properly-cased names in the same list.
    """
    if not name:
        return name
    return re.sub(r"[-_]+", " ", name).title()


@app.template_filter("short_date")
def _short_date(value: str | None) -> str:
    """ISO timestamp -> 'Aug 20, 2026' for table display; blank if unset."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%b %-d, %Y")
    except ValueError:
        return value


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


@app.route("/settings/reset-queue", methods=["POST"])
def reset_queue():
    """
    Close every open, undecided posting — Settings' "Reset queue"
    button. Doesn't touch Applications/Status history (those already
    have an application row and were never part of this pool); doesn't
    permanently lose anything still genuinely live either, since the
    next poll reopens anything it still sees. See
    jobs_repo.close_all_undecided's docstring for why that's safe.
    """
    with session() as conn:
        closed = jobs_repo.close_all_undecided(conn)
    invalidate_scored_cache()
    flash(f"Cleared {closed} posting{'s' if closed != 1 else ''} from the queue.")
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

    The launch itself runs in a background thread: `open -a <Browser>`
    (or webbrowser.open's equivalent) measured at ~110ms on its own —
    that's Launch Services handoff, not this code, so there's nothing
    to make faster, only something to stop blocking on. This is the
    single most-clicked button in the app; the page should redirect the
    instant the decision is actually recorded, with the browser tab
    catching up moments later rather than holding the click hostage.
    """
    with session() as conn:
        row = conn.execute(
            "SELECT absolute_url FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return redirect(url_for("index"))
        apps.add(conn, job_id)

    def open_in_background() -> None:
        browser_launcher.open_url(row["absolute_url"], browser=load_editable().browser)

    Thread(target=open_in_background, daemon=True).start()
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


@app.route("/applications/<int:job_id>/remove", methods=["POST"])
def remove_application(job_id: int):
    """
    Delete an application record — from the Applications table or
    Status's queued/ghosted lists, wherever the request came from.
    request.referrer rather than a fixed redirect target so this one
    route serves all three without needing to know which page called
    it, same pattern /poll already uses.
    """
    with session() as conn:
        removed = apps.remove(conn, job_id)
    flash("Removed." if removed else "Already gone.")
    return redirect(request.referrer or url_for("applications"))


@app.route("/applications")
def applications():
    with session() as conn:
        rows = apps.all_applications(conn)
    return render_template("applications.html", applications=rows, active="applications")


@app.route("/applications.csv")
def applications_csv():
    """
    Same rows the Applications tab shows, as a download.

    Built from all_applications() rather than re-querying — the page
    and the export can't drift apart if there's only one query. A
    StringIO buffer (not a generator streamed straight to the client)
    is fine here: even years of applications is a few thousand rows,
    nowhere near where buffering the whole thing in memory matters.
    """
    with session() as conn:
        rows = apps.all_applications(conn)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Company", "Title", "Status", "Location",
        "Queued", "Submitted", "Last Update", "URL",
    ])
    for r in rows:
        writer.writerow([
            _company_name(r["company"]), r["title"], r["status"], r["location"] or "",
            r["queued_at"], r["submitted_at"] or "", r["last_status_at"], r["absolute_url"],
        ])

    filename = f"jobtracker_applications_{datetime.now(timezone.utc):%Y-%m-%d}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        remote_on = request.form.get("remote_ok") == "on"
        hybrid_on = request.form.get("hybrid_ok") == "on"
        in_person_on = request.form.get("in_person_ok") == "on"
        tiers = presets.with_remote(city_tiers, remote_on=remote_on)
        tiers = presets.with_hybrid(tiers, hybrid_on=hybrid_on)

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
            max_years=_safe_int(request.form.get("max_years"), default=2),
            penalty_per_year=_safe_int(request.form.get("penalty_per_year"), default=15),
            min_score=presets.STRICTNESS_LEVELS[request.form.get("strictness", "balanced")],
            browser=request.form.get("browser", browser_launcher.SYSTEM_DEFAULT),
            show_remote=remote_on,
            show_hybrid=hybrid_on,
            show_in_person=in_person_on,
        )
        apply_editable(updated)
        invalidate_scored_cache()
        return redirect(url_for("settings"))

    current = load_editable()
    # The remote/hybrid *tiers* still only exist to give those postings
    # a scoring bonus (with_remote/with_hybrid above) — stripped out
    # here purely so they don't also show up as rows in the city-
    # priority list. Whether each mode is shown at all is a separate
    # question, answered by show_remote/show_hybrid/show_in_person
    # (from location.work_modes), not by tier presence.
    city_tiers, _ = presets.split_remote(current.tiers)
    city_tiers, _ = presets.split_hybrid(city_tiers)
    return render_template(
        "settings.html",
        settings=current,
        city_tiers=[
            {"cities": t.cities, "priority": presets.closest_priority(t.score)} for t in city_tiers
        ],
        remote_on=current.show_remote,
        hybrid_on=current.show_hybrid,
        in_person_on=current.show_in_person,
        priority_levels=presets.PRIORITY_LEVELS,
        experience_level=presets.guess_experience_level(
            current.seniority_preferred, current.seniority_penalized
        ),
        experience_labels=presets.EXPERIENCE_LABELS,
        strictness=presets.closest_strictness(current.min_score),
        strictness_labels=presets.STRICTNESS_LABELS,
        available_browsers=browser_launcher.available_browsers(),
        us_cities=US_TECH_CITIES,
        company_directory=COMPANY_DIRECTORY,
        company_count=sum(len(s) for s in current.boards.values()),
        resolve_found=_resolve_state["found"],
        resolve_missing=_resolve_state["missing"],
        active="settings",
    )


def _lines(text: str) -> list[str]:
    """Textarea contents -> one entry per non-blank line."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _safe_int(value: str | None, *, default: int) -> int:
    """
    Parse a form number field, falling back to `default` rather than
    raising — request.form.get(key, default) only returns the default
    when the key is absent, not when it's present but empty, and a
    cleared <input type=number> still submits as "". Without this,
    clearing that field and clicking Save 500s instead of saving.
    """
    try:
        return int(value) if value else default
    except ValueError:
        return default


def _prewarm_scored_jobs_cache() -> None:
    """
    Compute the queue's score cache in the background at startup,
    before the window even finishes opening, instead of on the first
    real request.

    On the real database this computation takes ~1.1s the first time
    (SQLite reading a 300+MB file cold) — with nothing to overlap it
    with, that lands as the very first thing the user sees on every
    launch. Running it here happens concurrently with the webview
    window actually opening, which reliably takes at least that long
    on its own, so by the time the window is up and the page requests
    it, the cache is usually already warm. Best-effort: if this fails
    or loses the race, the first real request just computes it the
    normal way, exactly as it did before this existed.
    """
    try:
        with session() as conn:
            scored_jobs(conn, Criteria.load())
    except Exception:
        pass


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
    Thread(target=_prewarm_scored_jobs_cache, daemon=True).start()
    # Every launch starts a poll automatically — otherwise the queue
    # only ever reflects whenever someone last remembered to click the
    # button, and a relaunch just shows the exact same postings as
    # last time even if plenty have closed since. Already
    # non-blocking and already guarded against double-polling
    # (_poll_state["running"]), so this is exactly the same call the
    # button itself makes, just fired once up front.
    _start_poll()
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
