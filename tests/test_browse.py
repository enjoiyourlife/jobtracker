"""
Interactive-queue action tests.

apply_selected/skip_selected are the only parts of browse.py that
carry logic; the curses event loop itself is verified by running the
program, not asserted here — see browse.py's module docstring for why.
"""

from __future__ import annotations

from jobtracker.ats.base import RawJob
from jobtracker.browse import apply_selected, skip_selected
from jobtracker.db import applications as apps
from jobtracker.db import jobs as repo


def make_job(job_id: str, **kw) -> RawJob:
    defaults = dict(
        global_id=f"greenhouse:acme:{job_id}",
        ats_job_id=job_id,
        title="Software Engineer",
        location="Seattle, WA",
        absolute_url=f"https://example.com/{job_id}",
        description=None,
        updated_at="2026-08-01T00:00:00Z",
        raw_payload="{}",
    )
    defaults.update(kw)
    return RawJob(**defaults)


def make_entry(job_id: int, **kw) -> apps.QueueEntry:
    defaults = dict(
        job_id=job_id,
        global_id=f"greenhouse:acme:{job_id}",
        company="Acme",
        title="Software Engineer",
        location="Seattle, WA",
        url=f"https://example.com/{job_id}",
        score=50,
        posted_at=None,
    )
    defaults.update(kw)
    return apps.QueueEntry(**defaults)


def _job_id(db) -> int:
    cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
    repo.upsert_jobs(db, cid, [make_job("1")])
    return db.execute("SELECT id FROM jobs").fetchone()["id"]


class _StubSettings:
    browser = "system"


class TestApplySelected:
    def test_records_queued_application(self, db, monkeypatch):
        monkeypatch.setattr("jobtracker.browse.load_editable", lambda: _StubSettings())
        monkeypatch.setattr("jobtracker.browse.browser_launcher.open_url", lambda url, browser: None)
        job_id = _job_id(db)

        apply_selected(db, make_entry(job_id, url="https://example.com/1"))

        row = db.execute(
            "SELECT status, score FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert row["status"] == "queued"
        assert row["score"] == 50

    def test_opens_the_configured_browser(self, db, monkeypatch):
        opened = []
        monkeypatch.setattr("jobtracker.browse.load_editable", lambda: _StubSettings())
        monkeypatch.setattr(
            "jobtracker.browse.browser_launcher.open_url",
            lambda url, browser: opened.append((url, browser)),
        )
        job_id = _job_id(db)

        apply_selected(db, make_entry(job_id, url="https://example.com/1"))

        assert opened == [("https://example.com/1", "system")]


class TestSkipSelected:
    def test_records_skipped_status(self, db):
        job_id = _job_id(db)

        skip_selected(db, make_entry(job_id))

        row = db.execute(
            "SELECT status FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert row["status"] == "skipped"

    def test_does_not_open_browser(self, db, monkeypatch):
        opened = []
        monkeypatch.setattr(
            "jobtracker.browse.browser_launcher.open_url",
            lambda url, browser: opened.append(url),
        )

        skip_selected(db, make_entry(_job_id(db)))

        assert opened == []
