"""
queries.scored_jobs tests.

This is where `queue`/`browse`/the GUI's --entry-level filter lives —
one shared function, covered once here rather than per caller.
"""

from __future__ import annotations

import dataclasses

from jobtracker.ats.base import RawJob
from jobtracker.db import jobs as repo
from jobtracker.filters import Criteria
from jobtracker.queries import scored_jobs

CONFIG = """
role:
  include: [software engineer]

seniority:
  preferred: [junior, new grad, "i"]
  penalized: [senior]

location:
  tiers:
    - score: 40
      match: [seattle]
  disallow: []

experience:
  max_years: 2
  penalty_per_year: 15

min_score: 0
"""


def make_job(job_id: str, title: str, **kw) -> RawJob:
    defaults = dict(
        global_id=f"greenhouse:acme:{job_id}",
        ats_job_id=job_id,
        title=title,
        location="Seattle, WA",
        absolute_url=f"https://example.com/{job_id}",
        description=None,
        updated_at=None,
        raw_payload="{}",
    )
    defaults.update(kw)
    return RawJob(**defaults)


def _criteria(tmp_path) -> Criteria:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return Criteria.load(path)


class TestScoredJobsEntryLevel:
    def test_default_includes_every_match_regardless_of_seniority(self, db, tmp_path):
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [
            make_job("1", "Software Engineer, New Grad"),
            make_job("2", "Senior Software Engineer"),
        ])
        result = scored_jobs(db, _criteria(tmp_path))
        assert len(result) == 2

    def test_entry_level_only_excludes_neutral_seniority(self, db, tmp_path):
        """
        A title with no seniority marker at all (score 0) must be
        excluded under --entry-level, not just penalized titles — it
        passes the default min_score bar on location alone but isn't
        actually tagged as junior/new-grad/entry anything.
        """
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [
            make_job("1", "Software Engineer, New Grad"),
            make_job("2", "Software Engineer"),
        ])
        result = scored_jobs(db, _criteria(tmp_path), entry_level_only=True)

        new_grad_id = db.execute(
            "SELECT id FROM jobs WHERE title LIKE '%New Grad%'"
        ).fetchone()["id"]
        assert set(result) == {new_grad_id}

    def test_entry_level_only_excludes_penalized_seniority(self, db, tmp_path):
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1", "Senior Software Engineer")])

        assert scored_jobs(db, _criteria(tmp_path), entry_level_only=True) == {}

    def test_entry_level_only_still_respects_min_score(self, db, tmp_path):
        """entry_level_only narrows the pool; it doesn't bypass the
        min_score floor the default queue already enforces."""
        cid = repo.get_or_create_company(db, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(db, cid, [make_job("1", "Software Engineer, New Grad")])

        strict = dataclasses.replace(_criteria(tmp_path), min_score=1000)
        assert scored_jobs(db, strict, entry_level_only=True) == {}
