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
from threading import Lock

from jobtracker.filters import Classification, Criteria, classify, score

# entry_level_only -> its scored_jobs() result. Not keyed on anything
# that identifies *which* database or criteria produced it, because
# there's only ever one of each in a running process — the GUI opens a
# fresh connection per request but always against the same db_path,
# and Criteria.load() always reads the same config.yaml. Correctness
# rests entirely on every write path calling invalidate_cache(): the
# poller (new/closed jobs), and add_boards/remove_board/apply_editable
# (criteria changes) — see gui.py's call sites.
_cache: dict[bool, dict[int, int]] = {}

# Guards the whole compute-and-store section, not just the dict access:
# the app's startup pre-warms this cache in a background thread so the
# window's first real request doesn't pay for it, but a fast machine
# can have that first request arrive before pre-warming finishes. With
# no lock, both threads would run the same ~250ms computation at once
# and, under the GIL, take turns rather than actually run in parallel —
# slower than either running alone, the opposite of the point. With the
# lock, the second caller blocks and then gets the first caller's
# now-cached result for free instead of redoing the work.
_cache_lock = Lock()


def invalidate_cache() -> None:
    """
    Drop the cached scored_jobs() result.

    Call this after anything that changes which postings are open or
    how they'd score: a poll finishing, or a config.yaml write (adding/
    removing a board, or saving Settings). Skipping this after a real
    change means the queue silently keeps showing stale scores — wrong
    in a way nothing would visibly error on, so every write path that
    can affect scoring calls this rather than a subset of them.

    Takes the same lock scored_jobs() computes under, so this can't
    land in the middle of a compute-and-store and get silently undone
    by that in-flight call writing its (now-stale-relative-to-whatever-
    triggered-this-invalidation) result right after the clear.
    """
    with _cache_lock:
        _cache.clear()


def scored_jobs(
    conn: sqlite3.Connection, criteria: Criteria, *, entry_level_only: bool = False
) -> dict[int, int]:
    """
    Score every open posting that classifies as a coding role.

    Returns job_id -> score for those at or above min_score.

    Cached across calls: this was the queue page's dominant cost
    (~250ms of it is extract_years() regex-scanning every surviving
    posting's description — reducing which postings need scoring at
    all was the first fix, but re-running that scan on every single
    page view, when open postings only change once per poll, was the
    remaining one). Invalidated explicitly rather than on a computed
    key, since there's no cheap "has anything changed" signal short of
    re-running the query this is meant to avoid.

    Two passes rather than one query, on purpose: classify() only looks
    at the title, but description averages ~8KB and only a fraction of
    postings pass the title filter at all (on the real database, about
    1 in 7) — pulling every open posting's description up front means
    SQLite hands Python tens of megabytes of text that gets thrown away
    unread. Filtering on the cheap columns first and fetching
    description only for survivors was the actual fix for a queue page
    that took 1.4s to render; confirmed by timing both versions against
    the real database, not by assumption.

    entry_level_only additionally requires the seniority dimension to
    have scored positively — i.e. the title matched one of
    seniority.preferred (junior, new grad, "I", early career, ...) in
    config.yaml. This reuses that list rather than a separate one, so
    tuning what counts as "entry level" is still just editing config.yaml
    and takes effect immediately, with no second list to keep in sync.
    """
    if entry_level_only in _cache:
        return _cache[entry_level_only]

    with _cache_lock:
        # Re-check: another thread (startup's pre-warm, most likely)
        # may have finished computing this exact key while this call
        # was waiting for the lock — if so, use that instead of
        # redoing ~250ms of work.
        if entry_level_only in _cache:
            return _cache[entry_level_only]

        candidates = conn.execute(
            "SELECT id, title, location FROM jobs WHERE closed_at IS NULL"
        ).fetchall()
        survivor_ids = [
            row["id"] for row in candidates
            if classify(row["title"], criteria) is Classification.MATCH
        ]
        if not survivor_ids:
            _cache[entry_level_only] = {}
            return {}

        by_id = {row["id"]: row for row in candidates}
        placeholders = ",".join("?" * len(survivor_ids))
        descriptions = {
            r["id"]: r["description"]
            for r in conn.execute(
                f"SELECT id, description FROM jobs WHERE id IN ({placeholders})",
                survivor_ids,
            )
        }

        result: dict[int, int] = {}
        for job_id in survivor_ids:
            row = by_id[job_id]
            breakdown = score(row["title"], row["location"], descriptions[job_id], criteria)
            if breakdown.total < criteria.min_score:
                continue
            if entry_level_only and breakdown.seniority <= 0:
                continue
            result[job_id] = breakdown.total
        _cache[entry_level_only] = result
        return result
