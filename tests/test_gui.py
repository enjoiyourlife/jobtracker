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


def _seed_unapplied_job(client) -> int:
    """A job with no application row yet — the state /apply expects."""
    import jobtracker.db.connection as connection
    from jobtracker.ats.base import RawJob
    from jobtracker.db import jobs as repo

    with connection.session() as conn:
        cid = repo.get_or_create_company(conn, "Acme", "greenhouse", "acme")
        repo.upsert_jobs(conn, cid, [RawJob(
            global_id="greenhouse:acme:apply-test", ats_job_id="apply-test",
            title="Backend Engineer", location="Seattle, WA",
            absolute_url="https://example.com/apply-test", description="",
            updated_at="2026-08-01T00:00:00Z", raw_payload="{}",
        )])
        return conn.execute(
            "SELECT id FROM jobs WHERE global_id='greenhouse:acme:apply-test'"
        ).fetchone()["id"]

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

    def test_apply_route(self, client, monkeypatch):
        monkeypatch.setattr("jobtracker.gui.Thread", _ImmediateThread)
        monkeypatch.setattr("jobtracker.gui.browser_launcher.open_url", lambda *a, **k: None)
        job_id = _seed_unapplied_job(client)

        assert client.get(f"/apply/{job_id}").status_code == 302

    def test_apply_to_a_nonexistent_job_id_does_not_500(self, client):
        assert client.get("/apply/999999").status_code == 302

    def test_add_companies_with_no_input_flashes_not_crashes(self, client):
        resp = client.post("/settings/companies", data={"company_names": ""})
        assert resp.status_code == 302

    def test_remove_company_with_missing_fields_is_a_no_op_not_a_crash(self, client):
        resp = client.post("/settings/companies/remove", data={})
        assert resp.status_code == 302


def _base_settings_form(**overrides):
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
        # Real checkboxes default checked (see settings.html) and a
        # browser submits every checked box on a plain save — an
        # omitted key here would silently mean "user unchecked all
        # three," which isn't what an untouched, normal submission
        # actually sends.
        "remote_ok": "on",
        "hybrid_ok": "on",
        "in_person_ok": "on",
    }
    form.update(overrides)
    return form


class TestSettingsFormSubmission:
    """Regression coverage for the empty max_years/penalty_per_year 500."""

    def _base_form(self, **overrides):
        return _base_settings_form(**overrides)

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


class TestResetQueue:
    def test_closes_undecided_jobs_and_redirects(self, client):
        from jobtracker.db.connection import session

        job_id = _seed_unapplied_job(client)

        resp = client.post("/settings/reset-queue")

        assert resp.status_code == 302
        with session() as conn:
            row = conn.execute("SELECT closed_at FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["closed_at"] is not None

    def test_does_not_touch_decided_jobs(self, client):
        from jobtracker.db.connection import session

        job_id = TestApplicationsExport()._seed_one_application(client)  # status='queued'

        client.post("/settings/reset-queue")

        with session() as conn:
            row = conn.execute("SELECT closed_at FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["closed_at"] is None

    def test_empty_queue_does_not_500(self, client):
        assert client.post("/settings/reset-queue").status_code == 302


class TestLocationStateSearch:
    """The city-priority autocomplete also suggests states and 'United
    States' — see us_states.py's docstring for why matching already
    worked, and this is only exposing it."""

    def test_settings_page_embeds_state_names(self, client):
        body = client.get("/settings").data
        assert b"Washington" in body  # a real entry from US_STATES

    def test_settings_page_offers_a_united_states_wide_suggestion(self, client):
        body = client.get("/settings").data
        assert b"United States" in body


class TestCompanySearchUI:
    """
    Regression coverage for replacing the plain textarea with a
    search-to-queue UI: the actual add/resolve flow (add_companies,
    resolve_all) is unchanged and already covered elsewhere — what's
    new here is that the page ships the pieces that UI depends on.
    """

    def test_settings_page_includes_the_search_input(self, client):
        body = client.get("/settings").data
        assert b'id="company-search"' in body

    def test_settings_page_embeds_the_company_directory(self, client):
        body = client.get("/settings").data
        assert b"Stripe" in body  # a real entry from COMPANY_DIRECTORY

    def test_hidden_company_names_field_still_posts_to_add_companies(self, client):
        """The JS populates a hidden field before submit — confirms the
        form still posts under the same field name add_companies() reads."""
        resp = client.post("/settings/companies", data={"company_names": "Acme"})
        assert resp.status_code == 302


class TestWorkModeCheckboxes:
    """
    Unchecked checkboxes simply aren't in form data at all — the actual
    risk here isn't a crash (that's already covered by the empty-string
    cases above), it's that "not present" must read as "off," not
    silently keep whatever was there before.
    """

    def test_unchecking_a_box_omits_it_and_that_narrows_the_filter(self, client):
        from jobtracker.config_editor import load_editable

        form = _base_settings_form()
        del form["hybrid_ok"]
        client.post("/settings", data=form)

        saved = load_editable()
        assert saved.show_remote is True
        assert saved.show_hybrid is False
        assert saved.show_in_person is True

    def test_all_boxes_checked_shows_every_mode(self, client):
        from jobtracker.config_editor import load_editable

        client.post("/settings", data=_base_settings_form())

        saved = load_editable()
        assert saved.show_remote is True
        assert saved.show_hybrid is True
        assert saved.show_in_person is True


class TestApplyDoesNotBlockOnTheBrowserLaunch:
    """
    Regression coverage for a real, measured lag: opening a real browser
    (webbrowser.open / `open -a <Browser>`) took ~110ms on its own — pure
    Launch Services handoff, nothing to speed up, only something to stop
    making the most-clicked button in the app wait on. Backgrounded now;
    these confirm the decision is still recorded synchronously (so a
    slow/failing launch can't lose it) while the launch itself runs
    through Thread rather than blocking the response.
    """

    def test_application_is_recorded_before_the_response_returns(self, client, monkeypatch):
        import jobtracker.gui as gui_module

        launched = []
        monkeypatch.setattr(gui_module, "Thread", _ImmediateThread)
        monkeypatch.setattr(
            gui_module.browser_launcher, "open_url",
            lambda url, browser=None: launched.append(url),
        )
        job_id = _seed_unapplied_job(client)

        client.get(f"/apply/{job_id}")

        from jobtracker.db.connection import session
        with session() as conn:
            row = conn.execute(
                "SELECT status FROM applications WHERE job_id=?", (job_id,)
            ).fetchone()
        assert row["status"] == "queued"
        assert launched == ["https://example.com/apply-test"]

    def test_the_browser_launch_itself_goes_through_a_real_thread(self, client, monkeypatch):
        """Not _ImmediateThread here — confirms the route actually calls
        Thread(...).start() rather than calling open_url directly."""
        import jobtracker.gui as gui_module

        started = []

        class _RecordingThread:
            def __init__(self, target, daemon=True):
                self._target = target
                started.append(True)

            def start(self):
                self._target()

        monkeypatch.setattr(gui_module, "Thread", _RecordingThread)
        monkeypatch.setattr(gui_module.browser_launcher, "open_url", lambda *a, **k: None)
        job_id = _seed_unapplied_job(client)

        client.get(f"/apply/{job_id}")

        assert started == [True]

    def test_a_failing_browser_launch_does_not_lose_the_recorded_application(self, client, monkeypatch):
        import jobtracker.gui as gui_module

        monkeypatch.setattr(gui_module, "Thread", _ImmediateThread)

        def _boom(*a, **k):
            raise RuntimeError("no browser available")

        monkeypatch.setattr(gui_module.browser_launcher, "open_url", _boom)
        job_id = _seed_unapplied_job(client)

        with pytest.raises(RuntimeError):
            client.get(f"/apply/{job_id}")

        from jobtracker.db.connection import session
        with session() as conn:
            row = conn.execute(
                "SELECT status FROM applications WHERE job_id=?", (job_id,)
            ).fetchone()
        assert row["status"] == "queued"  # recorded before the thread ever ran


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


class TestRunAutoPolls:
    """
    Regression coverage for "stale jobs on relaunch": without this,
    the queue only ever reflects whenever someone last remembered to
    click 'Poll for new postings,' and reopening the app shows the
    exact same postings as last time even if plenty have closed since.
    run() now fires the same call the button does, once, at startup.
    """

    def test_run_starts_a_poll_on_launch(self, monkeypatch):
        import jobtracker.gui as gui_module

        monkeypatch.setattr(gui_module.app, "run", lambda **kw: None)
        monkeypatch.setattr(gui_module, "Timer", lambda *a, **k: type("T", (), {"start": lambda self: None})())

        calls = []
        monkeypatch.setattr(gui_module, "_start_poll", lambda: calls.append(True))

        gui_module.run(native_window=False)

        assert calls == [True]
