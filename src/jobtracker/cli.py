"""
Command-line interface.

Reads the database and drives the application workflow. Scoring happens
here at query time rather than being persisted, so editing config.yaml
re-ranks everything on the next command with no migration.

Commands:
    queue    ranked postings awaiting a decision
    apply    open a posting in the browser and queue it
    mark     advance an application's status
    status   pipeline counts and ghosted submissions
    review   postings the classifier could not categorize
    tailor   answer-bank responses retargeted to one posting
    browse   interactive queue — arrow keys, a to apply, s to skip
    schedule manage the daily-poll cron entry
    gui      desktop GUI — queue, status, and settings in their own window
"""

from __future__ import annotations

import argparse
import os
import sqlite3

import anthropic
from dotenv import load_dotenv

from jobtracker import browser_launcher
from jobtracker import schedule as schedule_mod
from jobtracker.browse import browse
from jobtracker.config_editor import load_editable
from jobtracker.db import applications as apps
from jobtracker.db.connection import session
from jobtracker.filters import Classification, Criteria, classify, score
from jobtracker.queries import scored_jobs
from jobtracker.tailor import Profile, ProfileError, TailorError, select_variant, tailor_answer


def cmd_queue(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    criteria = Criteria.load()
    scored = scored_jobs(conn, criteria, entry_level_only=args.entry_level)
    entries = apps.queue(conn, scored, limit=args.limit)
    apps.save_snapshot(conn, entries)

    if not entries:
        print("Queue empty. Poll for new postings or lower min_score.")
        return 0

    for i, e in enumerate(entries, 1):
        location = e.location or "—"
        print(f"{i:>3}. [{e.score:>3}] {e.title}")
        print(f"      {e.company} · {location}")
        print(f"      {e.url}")
    print(f"\n{len(entries)} awaiting decision")
    return 0


def cmd_apply(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """
    Open a posting and queue it.

    Takes a queue position, not a job_id — resolved against the snapshot
    the last `queue` call saved, so it acts on exactly what was printed
    rather than a freshly recomputed ranking. `mark`, by contrast, still
    takes job_id: applications are acted on again days or weeks later,
    long after any queue snapshot that produced a position number is
    gone, so job_id is the only reference still meaningful by then. This
    command prints that job_id for the follow-up `mark` call.

    The browser is opened rather than the form submitted: every ATS puts
    its own questions, uploads, and anti-bot checks on the apply page,
    and submitting programmatically would violate their terms and risk
    the account. This is the handoff point between automation and you.
    """
    job_id = apps.resolve_position(conn, args.position)
    if job_id is None:
        print(f"No position {args.position} in the last queue. Run `jobtracker queue` again.")
        return 1

    row = conn.execute(
        """
        SELECT j.absolute_url, j.title, c.name AS company
          FROM jobs j JOIN companies c ON c.id = j.company_id
         WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()

    apps.add(conn, job_id, score=args.score)
    conn.commit()

    print(f"Queued: {row['title']} at {row['company']}")
    print(f"Opening {row['absolute_url']}")
    print("Once submitted:  jobtracker mark", job_id, "submitted")
    browser_launcher.open_url(row["absolute_url"], browser=load_editable().browser)
    return 0


def cmd_mark(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    try:
        apps.add(conn, args.job_id)          # no-op if already tracked
        apps.set_status(conn, args.job_id, args.status)
        conn.commit()
    except apps.TransitionError as exc:
        print(f"Error: {exc}")
        return 1

    print(f"job_id {args.job_id} -> {args.status}")
    return 0


def cmd_status(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    counts = apps.pipeline(conn)
    if not counts:
        print("No applications yet.")
        return 0

    order = [
        "queued", "skipped", "submitted", "acknowledged",
        "screening", "interview", "offer", "rejected",
    ]
    print("Pipeline")
    for status in order:
        if status in counts:
            print(f"  {status:<14} {counts[status]}")

    submitted = sum(
        counts.get(s, 0)
        for s in ("submitted", "acknowledged", "screening", "interview", "offer")
    )
    responded = sum(
        counts.get(s, 0)
        for s in ("acknowledged", "screening", "interview", "offer")
    )
    if submitted:
        print(f"\nResponse rate: {responded}/{submitted} ({responded / submitted:.0%})")

    stale = apps.ghosted(conn)
    if stale:
        print(f"\nNo response in {apps.GHOST_THRESHOLD_DAYS}+ days ({len(stale)})")
        for s in stale[:10]:
            print(f"  {s['days_out']:>3}d  {s['title']} · {s['company']}")
    return 0


def cmd_review(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """
    Titles the classifier could not place.

    This bucket exists so an unusual but real title ("Member of Technical
    Staff") surfaces for review instead of being silently discarded.
    Anything genuinely relevant here belongs in config.yaml's include list.
    """
    criteria = Criteria.load()
    rows = conn.execute(
        "SELECT DISTINCT title FROM jobs WHERE closed_at IS NULL ORDER BY title"
    ).fetchall()

    unknown = [
        r["title"]
        for r in rows
        if classify(r["title"], criteria) is Classification.UNCLASSIFIED
    ]
    for title in unknown[: args.limit]:
        print(title)
    print(f"\n{len(unknown)} unclassified titles")
    return 0


def cmd_tailor(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """
    Answer-bank responses retargeted to one posting.

    Takes a queue position, same contract as `apply` and for the same
    reason: resolved against the last `queue` snapshot rather than a
    fresh query, so it acts on exactly what you looked at.

    Prints to stdout rather than writing a file or filling a form —
    every line here is a Claude rewrite of your own words, and needs a
    human read before it goes anywhere near a real application.
    """
    job_id = apps.resolve_position(conn, args.position)
    if job_id is None:
        print(f"No position {args.position} in the last queue. Run `jobtracker queue` again.")
        return 1

    row = conn.execute(
        """
        SELECT j.title, j.description, c.name AS company
          FROM jobs j JOIN companies c ON c.id = j.company_id
         WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()

    try:
        profile = Profile.load()
    except ProfileError as exc:
        print(f"Error: {exc}")
        return 1

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set. Add it to .env and try again.")
        return 1

    client = anthropic.Anthropic(api_key=api_key)

    print(f"{row['title']} at {row['company']}\n")

    for key, value in profile.boilerplate.items():
        print(f"[{key}]")
        print(value)
        print()

    for question in profile.questions:
        variant = select_variant(question, row["title"], row["description"])
        project = profile.projects.get(variant.project_ref) if variant.project_ref else None
        try:
            answer = tailor_answer(
                variant, project,
                company=row["company"], title=row["title"], description=row["description"],
                client=client,
            )
        except TailorError as exc:
            print(f"[{question.id}] tailoring failed: {exc}\n")
            continue

        prompt_hint = question.prompts[0] if question.prompts else question.id
        print(f"[{question.id}]  ({prompt_hint})")
        print(answer)
        print()

    return 0


def cmd_browse(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """
    Interactive queue — no position numbers, no job_ids.

    Reuses the same ranking `queue` prints; the difference is entirely
    in how you act on it. Applying or skipping updates the database
    immediately (not batched at exit), so quitting early never loses a
    decision already made.
    """
    criteria = Criteria.load()
    scored = scored_jobs(conn, criteria, entry_level_only=args.entry_level)
    entries = apps.queue(conn, scored, limit=args.limit)
    if not entries:
        print("Queue empty. Poll for new postings or lower min_score.")
        return 0

    applied, skipped = browse(conn, entries)
    print(f"Applied to {applied}, skipped {skipped}.")
    return 0


def cmd_schedule(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """
    Manage jobtracker's own daily-poll cron entry.

    Doesn't touch the database — conn is accepted only to match the
    dispatch signature every other command uses.
    """
    if args.schedule_action == "install":
        hour, _, minute = args.time.partition(":")
        try:
            schedule_mod.install(hour=int(hour), minute=int(minute or 0))
        except (ValueError, schedule_mod.ScheduleError) as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Scheduled: {schedule_mod.status()}")
        return 0

    if args.schedule_action == "uninstall":
        removed = schedule_mod.uninstall()
        print("Removed." if removed else "Nothing scheduled.")
        return 0

    # status (also the default with no subcommand)
    current = schedule_mod.status()
    print(current or "Not scheduled. Run `jobtracker schedule install`.")
    return 0


def cmd_gui(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """
    Local web GUI. conn is unused — gui.run() opens its own connection
    per request, same as every CLI command opens one per invocation.
    """
    from jobtracker.gui import run

    run(port=args.port, native_window=not args.browser_tab)
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="jobtracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_queue = sub.add_parser("queue", help="ranked postings awaiting a decision")
    p_queue.add_argument("--limit", type=int, default=25)
    p_queue.add_argument(
        "--entry-level", action="store_true",
        help="only postings matching seniority.preferred in config.yaml "
             "(junior, new grad, \"I\", early career, ...)",
    )
    p_queue.set_defaults(func=cmd_queue)

    p_apply = sub.add_parser("apply", help="open a posting and queue it")
    p_apply.add_argument("position", type=int, help="position from the last `queue` listing")
    p_apply.add_argument("--score", type=int, default=None)
    p_apply.set_defaults(func=cmd_apply)

    p_mark = sub.add_parser("mark", help="advance an application's status")
    p_mark.add_argument("job_id", type=int)
    p_mark.add_argument(
        "status",
        choices=[
            "queued", "skipped", "submitted", "acknowledged",
            "screening", "interview", "offer", "rejected",
        ],
    )
    p_mark.set_defaults(func=cmd_mark)

    p_status = sub.add_parser("status", help="pipeline counts and ghosted submissions")
    p_status.set_defaults(func=cmd_status)

    p_review = sub.add_parser("review", help="titles the classifier could not place")
    p_review.add_argument("--limit", type=int, default=50)
    p_review.set_defaults(func=cmd_review)

    p_tailor = sub.add_parser("tailor", help="answer-bank responses retargeted to one posting")
    p_tailor.add_argument("position", type=int, help="position from the last `queue` listing")
    p_tailor.set_defaults(func=cmd_tailor)

    p_browse = sub.add_parser(
        "browse", help="interactive queue — arrow keys, a to apply, s to skip"
    )
    p_browse.add_argument("--limit", type=int, default=100)
    p_browse.add_argument(
        "--entry-level", action="store_true",
        help="only postings matching seniority.preferred in config.yaml",
    )
    p_browse.set_defaults(func=cmd_browse)

    p_schedule = sub.add_parser("schedule", help="manage the daily-poll cron entry")
    schedule_sub = p_schedule.add_subparsers(
        dest="schedule_action", required=False
    )

    p_sched_install = schedule_sub.add_parser("install", help="install or replace the cron entry")
    p_sched_install.add_argument(
        "--time", default="07:00", help="24h local time, HH:MM (default 07:00)"
    )
    p_sched_install.set_defaults(func=cmd_schedule, schedule_action="install")

    p_sched_status = schedule_sub.add_parser("status", help="show the current cron entry, if any")
    p_sched_status.set_defaults(func=cmd_schedule, schedule_action="status")

    p_sched_uninstall = schedule_sub.add_parser("uninstall", help="remove the cron entry")
    p_sched_uninstall.set_defaults(func=cmd_schedule, schedule_action="uninstall")

    p_schedule.set_defaults(func=cmd_schedule, schedule_action="status")

    p_gui = sub.add_parser("gui", help="desktop GUI — queue, status, and settings in their own window")
    p_gui.add_argument("--port", type=int, default=8765)
    p_gui.add_argument(
        "--browser-tab", action="store_true",
        help="open in your default browser instead of a native window",
    )
    p_gui.set_defaults(func=cmd_gui)

    args = parser.parse_args()
    with session() as conn:
        return args.func(conn, args)


if __name__ == "__main__":
    raise SystemExit(main())