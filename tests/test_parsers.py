"""
Parser tests.

Parsers are pure, so every case here runs offline against committed
fixtures. The point is not to prove the parsers work today — they
demonstrably do — but to fail loudly when an ATS changes its response
shape, which is otherwise invisible until the data is already wrong.
"""

from __future__ import annotations

import pytest

from jobtracker.ats import ashby, greenhouse, lever
from jobtracker.ats.base import ATSError, RawJob


class TestGreenhouse:
    def test_parses_all_postings(self, greenhouse_payload):
        jobs = greenhouse.parse(greenhouse_payload, "stripe")
        assert len(jobs) == len(greenhouse_payload["jobs"])
        assert all(isinstance(j, RawJob) for j in jobs)

    def test_global_id_format(self, greenhouse_payload):
        job = greenhouse.parse(greenhouse_payload, "stripe")[0]
        assert job.global_id == f"greenhouse:stripe:{job.ats_job_id}"

    def test_global_ids_unique(self, greenhouse_payload):
        jobs = greenhouse.parse(greenhouse_payload, "stripe")
        assert len({j.global_id for j in jobs}) == len(jobs)

    def test_location_unwrapped_from_nested_object(self, greenhouse_payload):
        jobs = greenhouse.parse(greenhouse_payload, "stripe")
        assert any(isinstance(j.location, str) and j.location for j in jobs)

    def test_description_html_fully_unescaped(self, greenhouse_payload):
        """Greenhouse double-escapes content; one pass leaves '&lt;' behind."""
        jobs = greenhouse.parse(greenhouse_payload, "stripe")
        described = [j for j in jobs if j.description]
        assert described, "fixture has no descriptions to check"
        assert not any("\\u0026lt;" in j.description for j in described)
        assert not any("&lt;" in j.description for j in described)

    def test_raw_payload_is_round_trippable(self, greenhouse_payload):
        import json

        job = greenhouse.parse(greenhouse_payload, "stripe")[0]
        assert json.loads(job.raw_payload)["id"] == int(job.ats_job_id)

    def test_missing_jobs_key_raises(self):
        with pytest.raises(ATSError):
            greenhouse.parse({"not_jobs": []}, "stripe")

    def test_malformed_posting_skipped_not_fatal(self):
        payload = {"jobs": [{"id": 1}, {"id": 2, "title": "SWE", "absolute_url": "u"}]}
        jobs = greenhouse.parse(payload, "acme")
        assert len(jobs) == 1
        assert jobs[0].title == "SWE"


class TestLever:
    def test_parses_all_postings(self, lever_payload):
        jobs = lever.parse(lever_payload, "matchgroup")
        assert len(jobs) == len(lever_payload["jobs"])

    def test_title_read_from_text_field(self, lever_payload):
        jobs = lever.parse(lever_payload, "matchgroup")
        assert all(j.title for j in jobs)

    def test_location_read_from_categories(self, lever_payload):
        jobs = lever.parse(lever_payload, "matchgroup")
        assert any(j.location for j in jobs)

    def test_epoch_ms_converted_to_iso(self, lever_payload):
        jobs = lever.parse(lever_payload, "matchgroup")
        stamped = [j for j in jobs if j.updated_at]
        assert stamped, "fixture has no timestamps to check"
        # ISO-8601, not a raw epoch integer
        assert all(j.updated_at.startswith("20") and "T" in j.updated_at
                   for j in stamped)

    def test_global_id_namespaced_by_ats(self, lever_payload):
        job = lever.parse(lever_payload, "matchgroup")[0]
        assert job.global_id.startswith("lever:matchgroup:")


class TestAshby:
    def test_unlisted_postings_excluded(self, ashby_payload):
        """Ashby returns drafts and internal roles alongside live ones."""
        listed = [j for j in ashby_payload["jobs"] if j.get("isListed") is not False]
        assert len(ashby.parse(ashby_payload, "ramp")) == len(listed)

    def test_remote_flag_annotates_location(self, ashby_payload):
        jobs = ashby.parse(ashby_payload, "ramp")
        assert any(j.location and "(Remote)" in j.location for j in jobs)

    def test_description_plain_used(self, ashby_payload):
        jobs = ashby.parse(ashby_payload, "ramp")
        described = [j for j in jobs if j.description]
        assert described
        assert not any("<p>" in j.description for j in described)


class TestRegistryContract:
    """Every registered client must satisfy the same shape."""

    @pytest.mark.parametrize("module", [greenhouse, lever, ashby])
    def test_exposes_required_names(self, module):
        assert isinstance(module.ATS_NAME, str)
        assert callable(module.fetch)
        assert callable(module.parse)
        assert callable(module.company_name)

    def test_registry_keys_match_module_names(self):
        from jobtracker.ats import REGISTRY

        assert all(name == mod.ATS_NAME for name, mod in REGISTRY.items())

    def test_unknown_ats_raises(self):
        from jobtracker.ats import get_client

        with pytest.raises(ATSError):
            get_client("workday")