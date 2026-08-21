"""
Shared pytest fixtures.

Fixture JSON captured from live boards lives in tests/fixtures and is
committed deliberately: it is the record of what each ATS actually
returned, so a parser can be verified without network access and a
breaking upstream change shows up as a failing test rather than as
silently missing rows.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from jobtracker.db.connection import connect
from jobtracker.queries import invalidate_cache

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_scored_jobs_cache():
    """
    queries.scored_jobs() caches its result in a module-level dict keyed
    only on entry_level_only, not on which database produced it — correct
    for the real app (one process, one db), but every test gets its own
    tmp_path db, so without clearing this between tests, one test's
    cached result would leak into the next test that happens to call
    scored_jobs() with the same entry_level_only value against a
    different db entirely.
    """
    invalidate_cache()
    yield
    invalidate_cache()


def _load(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture(scope="session")
def greenhouse_payload() -> dict[str, Any]:
    return _load("greenhouse_stripe.json")


@pytest.fixture(scope="session")
def lever_payload() -> dict[str, Any]:
    """Lever returns a bare list; fetch() wraps it, so tests wrap it too."""
    return {"jobs": _load("lever_test.json")}


@pytest.fixture(scope="session")
def ashby_payload() -> dict[str, Any]:
    return _load("ashby_ramp.json")


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """
    A real SQLite database on a temp path, torn down after each test.

    A file rather than :memory: so the schema, PRAGMAs, and WAL mode
    exercised here are the same ones production runs against.
    """
    conn = connect(tmp_path / "test.db")
    yield conn
    conn.close()