"""
Poller — orchestration layer.

Ties fetch -> parse -> persist together and records every execution in
the runs table. Clients are resolved through the ATS registry, so this
module has no knowledge of which sources exist; adding one requires no
change here.

A run may only close missing postings if it reached the end without
error; see db.jobs.close_missing_jobs.

Usage:
    python -m jobtracker.poller                       # every board in config
    python -m jobtracker.poller --ats greenhouse stripe figma
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from jobtracker.ats import get_client
from jobtracker.ats.base import ATSError
from jobtracker.db import jobs as jobs_repo
from jobtracker.db.connection import session
from jobtracker.filters import Criteria


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


def poll_board(conn: sqlite3.Connection, ats: str, slug: str) -> None:
    """
    Poll a single board end to end.

    Failures are recorded against the run and reported on stderr rather
    than propagating: one dead board must not abort a batch of two
    hundred.
    """
    run_id = _start_run(conn, ats, slug)

    try:
        client = get_client(ats)
        payload = client.fetch(slug)
        parsed = client.parse(payload, slug)

        company_id = jobs_repo.get_or_create_company(
            conn, client.company_name(parsed, slug), ats, slug
        )

        seen, new = jobs_repo.upsert_jobs(conn, company_id, parsed)
        closed = jobs_repo.close_missing_jobs(
            conn, company_id, [j.global_id for j in parsed]
        )

        _finish_run(conn, run_id, "success", seen, new)
        conn.commit()

        print(f"{ats}:{slug}: {seen} seen, {new} new, {closed} closed")

    except ATSError as exc:
        _finish_run(conn, run_id, "failed", error=str(exc))
        conn.commit()
        print(f"{ats}:{slug}: FAILED — {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll ATS job boards.")
    parser.add_argument(
        "slugs", nargs="*", help="Board slugs; requires --ats. Omit to use config.yaml"
    )
    parser.add_argument("--ats", help="ATS name for slugs given on the command line")
    args = parser.parse_args()

    if args.slugs and not args.ats:
        parser.error("--ats is required when slugs are given")

    if args.slugs:
        targets: list[tuple[str, str]] = [(args.ats, s) for s in args.slugs]
    else:
        criteria = Criteria.load()
        targets = [
            (ats, slug) for ats, slugs in criteria.boards.items() for slug in slugs
        ]
        if not targets:
            parser.error("no boards configured in config.yaml")

    with session() as conn:
        for ats, slug in targets:
            poll_board(conn, ats, slug)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())