"""
Poller — orchestration layer.

Ties fetch -> parse -> persist together and records every execution in
the runs table. A run is only permitted to close missing postings if it
reached the end without error; see db.jobs.close_missing_jobs.

Usage:
    python -m jobtracker.poller stripe figma
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from jobtracker.ats import greenhouse
from jobtracker.ats.greenhouse import RawJob
from jobtracker.db import jobs as jobs_repo
from jobtracker.db.connection import session


def _start_run(conn: sqlite3.Connection, ats: str, slug: str) -> int:
    """Open a runs row in 'running' state and return its id."""
    cursor = conn.execute(
        "INSERT INTO runs (ats, company, started_at, status) VALUES (?, ?, ?, 'running')",
        (ats, slug, jobs_repo.utc_now()),
    )
    return cursor.lastrowid


def _finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    seen: int = 0,
    new: int = 0,
    error: str | None = None,
) -> None:
    """Close out a runs row with its terminal status and counters."""
    conn.execute(
        """
        UPDATE runs
           SET finished_at = ?, status = ?, jobs_seen = ?, jobs_new = ?, error = ?
         WHERE id = ?
        """,
        (jobs_repo.utc_now(), status, seen, new, error, run_id),
    )


def _display_name(parsed: list[RawJob], slug: str) -> str:
    """
    Greenhouse carries company_name per posting rather than at the board
    level, so it is read from the first record. Falls back to the slug for
    an empty board or a payload without the field.
    """
    if not parsed:
        return slug
    try:
        return json.loads(parsed[0].raw_payload).get("company_name") or slug
    except (json.JSONDecodeError, AttributeError):
        return slug


def poll_board(conn: sqlite3.Connection, slug: str) -> None:
    """
    Poll a single Greenhouse board end to end.

    Failures are recorded against the run and reported on stderr rather
    than propagating: one dead board must not abort a batch.
    """
    run_id = _start_run(conn, greenhouse.ATS_NAME, slug)

    try:
        payload = greenhouse.fetch(slug)
        parsed = greenhouse.parse(payload, slug)

        company_id = jobs_repo.get_or_create_company(
            conn, _display_name(parsed, slug), greenhouse.ATS_NAME, slug
        )

        seen, new = jobs_repo.upsert_jobs(conn, company_id, parsed)
        closed = jobs_repo.close_missing_jobs(
            conn, company_id, [j.global_id for j in parsed]
        )

        _finish_run(conn, run_id, "success", seen, new)
        conn.commit()

        print(f"{slug}: {seen} seen, {new} new, {closed} closed")

    except greenhouse.GreenhouseError as exc:
        _finish_run(conn, run_id, "failed", error=str(exc))
        conn.commit()
        print(f"{slug}: FAILED — {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll Greenhouse job boards.")
    parser.add_argument("slugs", nargs="+", help="Greenhouse board tokens")
    args = parser.parse_args()

    with session() as conn:
        for slug in args.slugs:
            poll_board(conn, slug)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())