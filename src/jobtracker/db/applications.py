"""
Application tracking.

An applications row records a decision. Its absence means "not yet
decided", which is what makes the queue query work: candidates are open,
high-scoring postings with no row here. Skipping writes a 'skipped' row
so the posting stops resurfacing without silently discarding it.

Status transitions are validated rather than free-form. An invalid
transition is a bug in the caller, and catching it here keeps the
history coherent — an application cannot go from 'rejected' back to
'queued' by accident.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from jobtracker.db.jobs import utc_now

# Terminal states have no outgoing transitions.
_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"submitted", "skipped"}),
    "skipped": frozenset({"queued"}),
    "submitted": frozenset({"acknowledged", "screening", "rejected"}),
    "acknowledged": frozenset({"screening", "rejected"}),
    "screening": frozenset({"interview", "rejected"}),
    "interview": frozenset({"offer", "rejected", "screening"}),
    "offer": frozenset({"rejected"}),
    "rejected": frozenset(),
}

# Days without inbound contact before a submission is treated as ghosted.
GHOST_THRESHOLD_DAYS = 21


class TransitionError(ValueError):
    """Raised on an invalid status transition."""


@dataclass(frozen=True)
class QueueEntry:
    """A ranked posting awaiting a decision, joined with its company."""
    job_id: int
    global_id: str
    company: str
    title: str
    location: str | None
    url: str
    score: int
    posted_at: str | None


def queue(
    conn: sqlite3.Connection, scored: dict[int, int], limit: int = 50
) -> list[QueueEntry]:
    """
    Ranked postings with no application row yet.

    Scores are computed in the filter layer and passed in rather than
    stored, so re-tuning config.yaml re-ranks the queue immediately
    without a migration or a backfill.

    Args:
        scored: job_id -> score, for postings that passed classification.
        limit: maximum entries to return.
    """
    if not scored:
        return []

    placeholders = ",".join("?" * len(scored))
    rows = conn.execute(
        f"""
        SELECT j.id, j.global_id, c.name AS company, j.title, j.location,
               j.absolute_url, j.updated_at
          FROM jobs j
          JOIN companies c ON c.id = j.company_id
          LEFT JOIN applications a ON a.job_id = j.id
         WHERE j.id IN ({placeholders})
           AND j.closed_at IS NULL
           AND a.id IS NULL
        """,
        tuple(scored),
    ).fetchall()

    entries = [
        QueueEntry(
            job_id=r["id"],
            global_id=r["global_id"],
            company=r["company"],
            title=r["title"],
            location=r["location"],
            url=r["absolute_url"],
            score=scored[r["id"]],
            posted_at=r["updated_at"],
        )
        for r in rows
    ]

    # Score first, then recency: among equally good fits, the newest
    # posting is the one worth applying to first.
    entries.sort(key=lambda e: (e.score, e.posted_at or ""), reverse=True)
    return entries[:limit]


def save_snapshot(conn: sqlite3.Connection, entries: list[QueueEntry]) -> None:
    """
    Persist the exact listing `queue` just printed.

    `apply` resolves position numbers against this table instead of
    recomputing the ranking, so a number always means "the Nth thing you
    just looked at" rather than "the Nth thing right now" — those two
    can differ if new postings landed or an earlier position was already
    applied to (which removes it from the pool) between the print and
    the next command.
    """
    now = utc_now()
    conn.execute("DELETE FROM queue_snapshot")
    conn.executemany(
        "INSERT INTO queue_snapshot (position, job_id, created_at) VALUES (?, ?, ?)",
        [(i, e.job_id, now) for i, e in enumerate(entries, 1)],
    )


def resolve_position(conn: sqlite3.Connection, position: int) -> int | None:
    """job_id for a position in the most recently printed queue, or None."""
    row = conn.execute(
        "SELECT job_id FROM queue_snapshot WHERE position = ?", (position,)
    ).fetchone()
    return row["job_id"] if row is not None else None


def add(
    conn: sqlite3.Connection, job_id: int, score: int | None = None,
    status: str = "queued",
) -> int:
    """
    Record a decision on a posting.

    Idempotent: re-adding an existing application leaves it untouched
    and returns the existing id, so a double-click cannot reset history.
    """
    existing = conn.execute(
        "SELECT id FROM applications WHERE job_id = ?", (job_id,)
    ).fetchone()
    if existing is not None:
        return existing["id"]

    now = utc_now()
    cursor = conn.execute(
        """
        INSERT INTO applications
            (job_id, status, score, queued_at, last_status_at, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, status, score, now, now, now if status == "submitted" else None),
    )
    return cursor.lastrowid


def set_status(conn: sqlite3.Connection, job_id: int, new_status: str) -> None:
    """
    Advance an application's status.

    Raises:
        TransitionError: if no application exists, or the transition is
                         not permitted from the current status.
    """
    row = conn.execute(
        "SELECT id, status, submitted_at FROM applications WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise TransitionError(f"No application for job_id {job_id}")

    current = row["status"]
    if new_status == current:
        return
    if new_status not in _TRANSITIONS.get(current, frozenset()):
        allowed = ", ".join(sorted(_TRANSITIONS.get(current, []))) or "none"
        raise TransitionError(
            f"Cannot move from '{current}' to '{new_status}'. Allowed: {allowed}"
        )

    now = utc_now()
    # submitted_at is write-once: it records when the application was
    # actually sent, not the most recent time it passed through the state.
    submitted_at = row["submitted_at"] or (now if new_status == "submitted" else None)

    conn.execute(
        """
        UPDATE applications
           SET status = ?, last_status_at = ?, submitted_at = ?
         WHERE job_id = ?
        """,
        (new_status, now, submitted_at, job_id),
    )


def pipeline(conn: sqlite3.Connection) -> dict[str, int]:
    """Count of applications by status — the funnel, at a glance."""
    return {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
        )
    }


def ghosted(conn: sqlite3.Connection, days: int = GHOST_THRESHOLD_DAYS) -> list[dict]:
    """
    Submissions with no movement past the threshold.

    Computed rather than stored: ghosting is the absence of an event, so
    deriving it from submitted_at keeps it accurate without a nightly job
    to maintain a flag.
    """
    rows = conn.execute(
        """
        SELECT j.title, c.name AS company, a.submitted_at, j.absolute_url,
               CAST(julianday('now') - julianday(a.submitted_at) AS INTEGER) AS days_out
          FROM applications a
          JOIN jobs j ON j.id = a.job_id
          JOIN companies c ON c.id = j.company_id
         WHERE a.status = 'submitted'
           AND a.submitted_at IS NOT NULL
           AND julianday('now') - julianday(a.submitted_at) >= ?
         ORDER BY a.submitted_at
        """,
        (days,),
    ).fetchall()
    return [dict(r) for r in rows]