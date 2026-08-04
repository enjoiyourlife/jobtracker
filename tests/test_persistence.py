"""
Persistence tests.

These cover the properties that make unattended polling safe:
idempotency, write-once fields, and the guards on closure. A silent
failure here corrupts history rather than raising, which is why each
invariant is asserted directly rather than inferred from row counts
alone.
"""

from __future__ import annotations

from jobtracker.ats.base import RawJob
from jobtracker.db import jobs as repo


def make_job(job_id: str, title: str = "Software Engineer", **kw) -> RawJob:
    """Minimal RawJob for tests; overrides via kwargs."""
    defaults = dict(
        global_id=f"greenhouse:acme:{job_id}",
        ats_job_id=job_id,
        title=title,
        location="Seattle, WA",
        absolute_url=f"https://example.com/{job_id}",
        description="We require 2 years of experience.",
        updated_at="2026-08-01T00:00:00Z",
        raw_payload="{}",
    )
    defaults.update(kw)
    return RawJob(**defaults)


class TestCompanies:
    def test_creates_once(self, db):
        a = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        b = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        assert a == b
        assert db.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1

    def test_same_slug_different_ats_is_distinct(self, db):
        a = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        b = repo.get_or_create_company(db, "Acme", "lever", "acme")
        assert a != b


class TestUpsert:
    def test_first_run_inserts_all(self, db):
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        seen, new = repo.upsert_jobs(db, cid, [make_job("1"), make_job("2")])
        assert (seen, new) == (2, 2)

    def test_second_run_inserts_nothing(self, db):
        """The core idempotency guarantee: reruns must not duplicate."""
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        jobs = [make_job("1"), make_job("2")]

        repo.upsert_jobs(db, cid, jobs)
        seen, new = repo.upsert_jobs(db, cid, jobs)

        assert (seen, new) == (2, 0)
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2

    def test_first_seen_is_write_once(self, db):
        """Overwriting first_seen would destroy the discovery record."""
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1")])
        original = db.execute("SELECT first_seen FROM jobs").fetchone()["first_seen"]

        repo.upsert_jobs(db, cid, [make_job("1", title="Changed")])
        after = db.execute("SELECT first_seen FROM jobs").fetchone()["first_seen"]

        assert after == original

    def test_mutable_fields_refresh(self, db):
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1", title="SWE I")])
        repo.upsert_jobs(db, cid, [make_job("1", title="SWE II")])

        row = db.execute("SELECT title FROM jobs").fetchone()
        assert row["title"] == "SWE II"

    def test_partial_batch_counts_only_new(self, db):
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1")])
        seen, new = repo.upsert_jobs(db, cid, [make_job("1"), make_job("2")])
        assert (seen, new) == (2, 1)


class TestClosure:
    def test_absent_posting_marked_closed(self, db):
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1"), make_job("2")])

        closed = repo.close_missing_jobs(db, cid, ["greenhouse:acme:1"])

        assert closed == 1
        row = db.execute(
            "SELECT closed_at FROM jobs WHERE global_id = 'greenhouse:acme:2'"
        ).fetchone()
        assert row["closed_at"] is not None

    def test_present_posting_stays_open(self, db):
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1")])
        repo.close_missing_jobs(db, cid, ["greenhouse:acme:1"])

        row = db.execute("SELECT closed_at FROM jobs").fetchone()
        assert row["closed_at"] is None

    def test_empty_result_closes_nothing(self, db):
        """
        The guard that matters most: an empty board almost always means a
        broken slug or an outage, and closing everything on that signal
        would silently destroy the company's entire history.
        """
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1"), make_job("2")])

        assert repo.close_missing_jobs(db, cid, []) == 0
        open_count = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL"
        ).fetchone()[0]
        assert open_count == 2

    def test_reappearance_reopens(self, db):
        """A reposted role should not stay marked closed."""
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1")])
        repo.close_missing_jobs(db, cid, ["greenhouse:acme:999"])
        assert db.execute("SELECT closed_at FROM jobs").fetchone()["closed_at"]

        repo.upsert_jobs(db, cid, [make_job("1")])
        assert db.execute("SELECT closed_at FROM jobs").fetchone()["closed_at"] is None

    def test_closure_scoped_to_company(self, db):
        """One company's run must never close another company's postings."""
        acme = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        other = repo.get_or_create_company(db, "Other", "greenhouse", "other")

        repo.upsert_jobs(db, acme, [make_job("1")])
        repo.upsert_jobs(
            db, other, [make_job("2", global_id="greenhouse:other:2")]
        )

        repo.close_missing_jobs(db, acme, ["greenhouse:acme:999"])

        row = db.execute(
            "SELECT closed_at FROM jobs WHERE global_id = 'greenhouse:other:2'"
        ).fetchone()
        assert row["closed_at"] is None