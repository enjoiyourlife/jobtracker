"""
Database connection management.

Owns the SQLite connection lifecycle and applies the schema on open.
Every statement in schema.sql is idempotent (IF NOT EXISTS), so this
runs safely on every startup — a fresh clone builds its own database.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

# Default location. Gitignored — this is local state, not source.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "jobtracker.db"


def _load_schema() -> str:
    """Read schema.sql from the package, not from a relative path."""
    return resources.files(__package__).joinpath("schema.sql").read_text()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """
    Open a connection and ensure the schema is applied.

    Args:
        db_path: Database file. Defaults to data/jobtracker.db.
                 Pass ":memory:" as a Path for tests.

    Returns:
        An open connection with row access by column name.
    """
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)

    # Rows behave like dicts: row["title"] instead of row[3].
    conn.row_factory = sqlite3.Row

    # Enforce foreign keys — SQLite leaves them off by default.
    conn.execute("PRAGMA foreign_keys = ON")

    # Write-ahead logging: readers don't block the writer.
    conn.execute("PRAGMA journal_mode = WAL")

    conn.executescript(_load_schema())
    conn.commit()

    return conn


@contextmanager
def session(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """
    Connection as a context manager. Commits on success, rolls back
    on exception, always closes.

    Usage:
        with session() as conn:
            conn.execute("INSERT INTO ...")
    """
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()