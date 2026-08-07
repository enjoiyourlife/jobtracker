"""
Query layer joining filters.py's pure scoring to the database.

filters.py stays pure (title/location/description in, a score out) so
its tests run with no database at all. Something still has to pull
open postings from SQLite and run them through classify()/score() —
that's this module, kept separate so both cli.py and gui.py can share
one implementation instead of one entry point importing from the
other, or duplicating the same query twice.
"""

from __future__ import annotations

import sqlite3

from jobtracker.filters import Classification, Criteria, classify, score


def scored_jobs(
    conn: sqlite3.Connection, criteria: Criteria, *, entry_level_only: bool = False
) -> dict[int, int]:
    """
    Score every open posting that classifies as a coding role.

    Returns job_id -> score for those at or above min_score.

    entry_level_only additionally requires the seniority dimension to
    have scored positively — i.e. the title matched one of
    seniority.preferred (junior, new grad, "I", early career, ...) in
    config.yaml. This reuses that list rather than a separate one, so
    tuning what counts as "entry level" is still just editing config.yaml
    and takes effect immediately, with no second list to keep in sync.
    """
    rows = conn.execute(
        "SELECT id, title, location, description FROM jobs WHERE closed_at IS NULL"
    ).fetchall()

    result: dict[int, int] = {}
    for row in rows:
        if classify(row["title"], criteria) is not Classification.MATCH:
            continue
        breakdown = score(
            row["title"], row["location"], row["description"], criteria
        )
        if breakdown.total < criteria.min_score:
            continue
        if entry_level_only and breakdown.seniority <= 0:
            continue
        result[row["id"]] = breakdown.total
    return result
