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
from threading import Timer

from flask import Flask, redirect, render_template, request, url_for

from jobtracker import settings_presets as presets
from jobtracker.config_editor import EditableSettings, Tier, apply_editable, load_editable
from jobtracker.db import applications as apps
from jobtracker.db.applications import GHOST_THRESHOLD_DAYS
from jobtracker.db.connection import session
from jobtracker.filters import Criteria
from jobtracker.queries import scored_jobs

PIPELINE_ORDER = [
    "queued", "skipped", "submitted", "acknowledged",
    "screening", "interview", "offer", "rejected",
]

app = Flask(__name__)
app.secret_key = "jobtracker-local"  # local-only tool; no real session security needed


@app.route("/")
def index():
    entry_level = request.args.get("entry_level") == "1"
    with session() as conn:
        criteria = Criteria.load()
        scored = scored_jobs(conn, criteria, entry_level_only=entry_level)
        entries = apps.queue(conn, scored, limit=200)
        apps.save_snapshot(conn, entries)
    return render_template("queue.html", entries=entries, entry_level=entry_level, active="queue")


@app.route("/apply/<int:job_id>")
def apply(job_id: int):
    with session() as conn:
        row = conn.execute(
            "SELECT absolute_url FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return redirect(url_for("index"))
        apps.add(conn, job_id)
    return redirect(row["absolute_url"])


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
        )
        apply_editable(updated)
        return redirect(url_for("settings"))

    current = load_editable()
    city_tiers, remote_on = presets.split_remote(current.tiers)
    return render_template(
        "settings.html",
        settings=current,
        city_tiers=[
            {"cities": t.cities, "priority": presets.closest_priority(t.score)} for t in city_tiers
        ],
        remote_on=remote_on,
        priority_levels=presets.PRIORITY_LEVELS,
        experience_level=presets.guess_experience_level(
            current.seniority_preferred, current.seniority_penalized
        ),
        experience_labels=presets.EXPERIENCE_LABELS,
        strictness=presets.closest_strictness(current.min_score),
        strictness_labels=presets.STRICTNESS_LABELS,
        active="settings",
    )


def _lines(text: str) -> list[str]:
    """Textarea contents -> one entry per non-blank line."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    url = f"http://{host}:{port}"
    if open_browser:
        Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"jobtracker GUI running at {url} (Ctrl+C to stop)")
    app.run(host=host, port=port, debug=False, use_reloader=False)
