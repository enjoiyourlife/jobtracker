"""
gui.py tests.

Flask routes read config.yaml and the database through module-level
path constants (connection.DEFAULT_DB_PATH, filters.DEFAULT_CONFIG_PATH,
config_editor.CONFIG_PATH) rather than accepting a path per call the
way the CLI's session()/Criteria.load() calls do — so isolating a test
means monkeypatching those constants to a tmp_path copy, not passing
an argument. The `client` fixture does that once for every test here.
"""

from __future__ import annotations

import pytest

from jobtracker.gui import _safe_int

MINIMAL_CONFIG = """
boards:
  greenhouse: [acme]

role:
  include: [software engineer]
  exclude: []

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
browser: system
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(MINIMAL_CONFIG)

    monkeypatch.setattr("jobtracker.db.connection.DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr("jobtracker.filters.DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("jobtracker.config_editor.CONFIG_PATH", config_path)

    from jobtracker.gui import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_background_state():
    """
    _poll_state/_resolve_state are module-level dicts shared by every
    route and every test in this file — without resetting them, one
    test leaving polling=True (or a real background thread still
    running past its test's teardown) silently breaks whichever test
    happens to run next, exactly the way test_poll_route_redirects did
    before this fixture existed.
    """
    import jobtracker.gui as gui_module

    gui_module._poll_state.update(running=False, boards_done=0, boards_total=0)
    gui_module._resolve_state.update(running=False, found=[], missing=[])
    yield
    gui_module._poll_state.update(running=False, boards_done=0, boards_total=0)
    gui_module._resolve_state.update(running=False, found=[], missing=[])


class TestSafeInt:
    """Regression coverage for the empty-number-field 500."""

    def test_valid_string_parses(self):
        assert _safe_int("5", default=2) == 5

    def test_empty_string_falls_back_to_default(self):
        assert _safe_int("", default=2) == 2

    def test_none_falls_back_to_default(self):
        assert _safe_int(None, default=2) == 2

    def test_garbage_falls_back_to_default_instead_of_raising(self):
        assert _safe_int("not a number", default=2) == 2


class TestRoutesDontCrash:
    """
    Every page and JSON endpoint, hit once each. Not exhaustive
    behavioral coverage — just the baseline that shipping code should
    already clear: no route 500s on a normal, empty-but-valid setup.
    """

    def test_queue_page(self, client):
        assert client.get("/").status_code == 200

    def test_status_page(self, client):
        assert client.get("/status").status_code == 200

    def test_applications_page(self, client):
        assert client.get("/applications").status_code == 200

    def test_applications_csv(self, client):
        resp = client.get("/applications.csv")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"

    def test_settings_page(self, client):
        assert client.get("/settings").status_code == 200

    def test_api_queue(self, client):
        assert client.get("/api/queue").status_code == 200

    def test_api_poll_status(self, client):
        assert client.get("/api/poll-status").status_code == 200

    def test_poll_route_redirects(self, client, monkeypatch):
        # Without mocking Thread, this spawns a real background thread
        # that hits the real network (MINIMAL_CONFIG's "acme" board
        # isn't real) — slow, flaky, and it leaks a live thread past
        # this test's teardown. _ImmediateThread runs it synchronously
        # instead, against the isolated tmp_path config/db the `client`
        # fixture already set up.
        monkeypatch.setattr("jobtracker.gui.Thread", _ImmediateThread)
        assert client.post("/poll").status_code == 302

    def test_add_companies_with_no_input_flashes_not_crashes(self, client):
        resp = client.post("/settings/companies", data={"company_names": ""})
        assert resp.status_code == 302

    def test_remove_company_with_missing_fields_is_a_no_op_not_a_crash(self, client):
        resp = client.post("/settings/companies/remove", data={})
        assert resp.status_code == 302


class TestSettingsFormSubmission:
    """Regression coverage for the empty max_years/penalty_per_year 500."""

    def _base_form(self, **overrides):
        form = {
            "tier_priority": ["top"],
            "tier_cities": ["seattle"],
            "role_include": "software engineer",
            "role_exclude": "",
            "experience_level": "entry",
            "max_years": "2",
            "penalty_per_year": "15",
            "strictness": "balanced",
            "browser": "system",
        }
        form.update(overrides)
        return form

    def test_normal_submission_saves(self, client):
        resp = client.post("/settings", data=self._base_form())
        assert resp.status_code == 302

    def test_empty_max_years_does_not_500(self, client):
        """The actual bug: clearing a number input submits "", and
        request.form.get(key, default) does not catch that — only a
        missing key, not an empty value."""
        resp = client.post("/settings", data=self._base_form(max_years=""))
        assert resp.status_code == 302

    def test_empty_penalty_per_year_does_not_500(self, client):
        resp = client.post("/settings", data=self._base_form(penalty_per_year=""))
        assert resp.status_code == 302

    def test_empty_numbers_fall_back_to_sane_defaults(self, client):
        from jobtracker.config_editor import load_editable

        client.post("/settings", data=self._base_form(max_years="", penalty_per_year=""))
        saved = load_editable()
        assert saved.max_years == 2
        assert saved.penalty_per_year == 15


class TestApplicationsExport:
    """CSV export must reflect real seeded data, not just return 200
    on an empty database — content-shape coverage the smoke test above
    doesn't give."""

    def _seed_one_application(self, client) -> int:
        import jobtracker.db.connection as connection
        from jobtracker.ats.base import RawJob
        from jobtracker.db import applications as apps
        from jobtracker.db import jobs as repo

        with connection.session() as conn:
            cid = repo.get_or_create_company(conn, "Acme", "greenhouse", "acme")
            repo.upsert_jobs(conn, cid, [RawJob(
                global_id="greenhouse:acme:1", ats_job_id="1",
                title="Backend Engineer", location="Seattle, WA",
                absolute_url="https://example.com/1", description="",
                updated_at="2026-08-01T00:00:00Z", raw_payload="{}",
            )])
            job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
            apps.add(conn, job_id, status="queued")
            return job_id

    def test_csv_contains_the_seeded_row(self, client):
        self._seed_one_application(client)

        resp = client.get("/applications.csv")

        body = resp.get_data(as_text=True)
        assert "Acme" in body
        assert "Backend Engineer" in body
        assert "queued" in body
        assert "https://example.com/1" in body

    def test_csv_download_has_a_filename(self, client):
        resp = client.get("/applications.csv")
        assert "attachment; filename=" in resp.headers["Content-Disposition"]

    def test_applications_page_shows_the_seeded_row(self, client):
        self._seed_one_application(client)

        resp = client.get("/applications")

        assert b"Acme" in resp.data
        assert b"Backend Engineer" in resp.data


class TestRemoveApplication:
    def test_removes_and_redirects(self, client):
        job_id = TestApplicationsExport()._seed_one_application(client)

        resp = client.post(f"/applications/{job_id}/remove")

        assert resp.status_code == 302
        assert b"Acme" not in client.get("/applications").data

    def test_removing_an_already_gone_job_id_does_not_500(self, client):
        resp = client.post("/applications/999999/remove")
        assert resp.status_code == 302


class _ImmediateThread:
    """
    Stand-in for threading.Thread that runs its target synchronously on
    .start() instead of on a real thread — lets these tests assert on
    worker-function state immediately rather than racing a background
    thread, and (the actual point) lets a raised exception surface
    directly to the test instead of vanishing into a thread's default
    unhandled-exception hook.
    """

    def __init__(self, target, daemon=True):
        self._target = target

    def start(self):
        self._target()


class TestCompanyResolveWorker:
    """Regression coverage for the UnboundLocalError in the background
    resolve worker when resolve_all() raises before `found` is assigned."""

    def test_exception_during_resolve_does_not_crash_the_worker(self, client, monkeypatch):
        import jobtracker.gui as gui_module

        def _boom(names):
            raise RuntimeError("simulated network failure")

        monkeypatch.setattr(gui_module, "resolve_all", _boom)
        monkeypatch.setattr(gui_module, "Thread", _ImmediateThread)

        started = gui_module._start_company_resolve(["Some Company"])

        assert started is True
        assert gui_module._resolve_state["running"] is False
        assert gui_module._resolve_state["missing"] == ["Some Company"]


class TestPollWorker:
    """Regression coverage for the same class of bug in the poll worker
    — an exception (e.g. a malformed config.yaml) must not leave
    _poll_state stuck at running=True forever."""

    def test_exception_during_poll_does_not_leave_state_stuck_running(self, client, monkeypatch):
        import jobtracker.gui as gui_module

        def _boom(path=None):
            raise RuntimeError("simulated config load failure")

        monkeypatch.setattr(gui_module.Criteria, "load", staticmethod(_boom))
        monkeypatch.setattr(gui_module, "Thread", _ImmediateThread)

        started = gui_module._start_poll()

        assert started is True
        assert gui_module._poll_state["running"] is False
