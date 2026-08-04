"""
Job persistence.

Upsert semantics, keyed on jobs.global_id:
  - unseen posting        -> INSERT, first_seen = last_seen = now
  - posting seen again    -> UPDATE mutable fields, bump last_seen,
                             clear closed_at (a reappearance means reopened)
  - previously-seen posting
    absent from a SUCCESSFUL run -> stamp closed_at

That last rule is why closures are only ever applied from a run we
know completed. A crashed poller sees nothing; that is not evidence
a job closed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from jobtracker.ats.greenhouse import RawJob


def utc_now() -> str:
    """ISO-8601 UTC timestamp. Sorts lexicographically; readable in the CLI."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_or_create_company(
    conn: sqlite3.Connection, name: str, ats: str, slug: str
) -> int:
    """Return the companies.id for (ats, slug), inserting if absent."""
    row = conn.execute(
        "SELECT id FROM companies WHERE ats = ? AND slug = ?", (ats, slug)
    ).fetchone()
    if row is not None:
        return row["id"]

    cursor = conn.execute(
        "INSERT INTO companies (name, ats, slug, created_at) VALUES (?, ?, ?, ?)",
        (name, ats, slug, utc_now()),
    )
    return cursor.lastrowid


def upsert_jobs(
    conn: sqlite3.Connection, company_id: int, jobs: list[RawJob]
) -> tuple[int, int]:
    """
    Insert new postings and refresh existing ones.

    Novelty is determined by reading the company's known global_ids up
    front rather than inspecting cursor.rowcount: SQLite reports 1 for
    both an INSERT and an ON CONFLICT UPDATE, so rowcount cannot
    distinguish them. One query plus a set membership test is both
    correct and cheaper than per-row inspection.

    first_seen and company_id are deliberately absent from the UPDATE
    clause — they are write-once facts, and overwriting first_seen would
    destroy the only record of when we discovered a posting.

    Returns:
        (seen, new) — total processed, and how many were first-time inserts.
    """
    now = utc_now()
    new_count = 0

    existing = {
        row["global_id"]
        for row in conn.execute(
            "SELECT global_id FROM jobs WHERE company_id = ?", (company_id,)
        )
    }

    for job in jobs:
        if job.global_id not in existing:
            new_count += 1

        conn.execute(
            """
            INSERT INTO jobs (
                global_id, company_id, ats_job_id, title, location,
                absolute_url, description, raw_payload, updated_at,
                first_seen, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(global_id) DO UPDATE SET
                title        = excluded.title,
                location     = excluded.location,
                absolute_url = excluded.absolute_url,
                description  = excluded.description,
                raw_payload  = excluded.raw_payload,
                updated_at   = excluded.updated_at,
                last_seen    = excluded.last_seen,
                closed_at    = NULL
            """,
            (
                job.global_id, company_id, job.ats_job_id, job.title,
                job.location, job.absolute_url, job.description,
                job.raw_payload, job.updated_at, now, now,
            ),
        )

    return len(jobs), new_count


def close_missing_jobs(
    conn: sqlite3.Connection, company_id: int, seen_global_ids: list[str]
) -> int:
    """
    Mark open postings absent from this run as closed.

    Only call after a run has completed successfully. Guarded against an
    empty result set — zero postings almost always means a broken board
    or a bad slug, not that every role closed at once.

    Returns:
        Number of postings newly marked closed.
    """
    if not seen_global_ids:
        return 0

    placeholders = ",".join("?" * len(seen_global_ids))
    cursor = conn.execute(
        f"""
        UPDATE jobs
           SET closed_at = ?
         WHERE company_id = ?
           AND closed_at IS NULL
           AND global_id NOT IN ({placeholders})
        """,
        (utc_now(), company_id, *seen_global_ids),
    )
    return cursor.rowcount