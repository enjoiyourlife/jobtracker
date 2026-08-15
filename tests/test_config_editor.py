"""
config_editor tests.

Two concerns: the editable fields round-trip correctly (load -> apply
-> load again matches), and sections the Settings page never touches
survive completely intact — comments, the YAML anchor, everything.
The second is checked against the real config.yaml, since a synthetic
fixture wouldn't exercise the anchor that's the actual risk here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from jobtracker.config_editor import (
    EditableSettings,
    Tier,
    add_boards,
    apply_editable,
    load_editable,
    remove_board,
)

MINIMAL_CONFIG = """\
boards:
  greenhouse: [acme]

role:
  include: [software engineer]
  exclude: [sales engineer]

seniority:
  preferred: [junior]
  penalized: [senior]

location:
  tiers:
    - score: 40
      match: [seattle, bellevue]
  disallow: [china]

experience:
  max_years: 2
  penalty_per_year: 15

min_score: 20
"""

REAL_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class TestLoadEditable:
    def test_reads_tiers_as_comma_joined_cities(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        settings = load_editable(path)

        assert settings.tiers == [Tier(score=40, cities="seattle, bellevue")]
        assert settings.role_include == ["software engineer"]
        assert settings.role_exclude == ["sales engineer"]
        assert settings.max_years == 2
        assert settings.min_score == 20


class TestApplyEditable:
    def test_changes_are_visible_on_reload(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        updated = EditableSettings(
            tiers=[Tier(score=50, cities="austin, dallas")],
            role_include=["software engineer", "sdet"],
            role_exclude=["sales engineer"],
            seniority_preferred=["junior", "new grad"],
            seniority_penalized=["senior"],
            max_years=3,
            penalty_per_year=10,
            min_score=25,
        )
        apply_editable(updated, path)
        reloaded = load_editable(path)

        assert reloaded.tiers == [Tier(score=50, cities="austin, dallas")]
        assert reloaded.role_include == ["software engineer", "sdet"]
        assert reloaded.seniority_preferred == ["junior", "new grad"]
        assert reloaded.max_years == 3
        assert reloaded.min_score == 25

    def test_blank_tier_rows_are_dropped(self, tmp_path):
        """The form always submits one trailing empty 'add a group' row —
        it must not become a tier with an empty city list."""
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        updated = load_editable(path)
        updated.tiers.append(Tier(score=0, cities="   "))
        apply_editable(updated, path)

        assert load_editable(path).tiers == [Tier(score=40, cities="seattle, bellevue")]

    def test_untouched_sections_survive_byte_for_byte(self, tmp_path):
        """boards: is never part of EditableSettings — editing other
        fields must not perturb it at all."""
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        settings = load_editable(path)
        settings.min_score = 99
        apply_editable(settings, path)

        boards_line = [l for l in path.read_text().splitlines() if "greenhouse" in l]
        assert boards_line == ["  greenhouse: [acme]"]

    def test_real_config_anchor_and_comments_survive_a_round_trip(self, tmp_path):
        """
        The actual risk this module exists to avoid: plain PyYAML would
        expand _non_us_countries's anchor into a duplicated inline list
        and strip every comment. Round-trip the real file through an
        edit that touches every editable list and confirm both are
        still there — this caught two real bugs (list indentation
        reformatting the whole file, and section-header comments being
        silently dropped) that a minimal synthetic fixture couldn't
        have, since neither problem exists in a small flat file.
        """
        path = tmp_path / "config.yaml"
        shutil.copy(REAL_CONFIG_PATH, path)

        settings = load_editable(path)
        settings.role_include = [*settings.role_include, "test engineer ii"]
        settings.seniority_penalized = [*settings.seniority_penalized, "lead ii"]
        apply_editable(settings, path)

        result = path.read_text()
        assert "&non_us_countries" in result
        assert "*non_us_countries" in result
        assert "jobtracker search criteria" in result  # file header comment
        assert "- china" in result  # anchor's country list wasn't expanded/duplicated

        # Section-header comments: these live on the *previous* list's
        # last item internally (see _replace_list's docstring), so
        # they're the real test that the column-based heuristic works,
        # not just that untouched sections were left alone.
        assert "# Seniority scoring. Applied to the title." in result
        assert "# Evaluated first." in result
        assert "# Location scoring, matched against" in result

        # Untouched sections weren't reformatted just by opening the file.
        # (Anchored on the newline — "    - stripe" contains "  - stripe"
        # as a plain substring, so an unanchored check can't tell 4-space
        # indent from 2-space indent.)
        assert "\n    - stripe\n" in result
        assert "\n  - stripe\n" not in result

    def test_real_config_still_loads_as_valid_criteria_after_a_save(self, tmp_path):
        """The round-tripped file must still be exactly what Criteria.load()
        (plain PyYAML, used at query time) expects — not just visually intact."""
        from jobtracker.filters import Criteria

        path = tmp_path / "config.yaml"
        shutil.copy(REAL_CONFIG_PATH, path)

        settings = load_editable(path)
        settings.min_score = 33
        apply_editable(settings, path)

        criteria = Criteria.load(path)
        assert criteria.min_score == 33
        assert criteria.location_disallow  # anchor still resolves to real data


class TestAddBoards:
    def test_appends_to_existing_ats_list(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        add_boards([("greenhouse", "figma")], path)

        assert load_editable(path).boards["greenhouse"] == ["acme", "figma"]

    def test_creates_a_new_ats_key_if_absent(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        add_boards([("lever", "notion")], path)

        assert load_editable(path).boards["lever"] == ["notion"]
        assert load_editable(path).boards["greenhouse"] == ["acme"]  # untouched

    def test_duplicate_slug_is_not_added_twice(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        add_boards([("greenhouse", "acme")], path)

        assert load_editable(path).boards["greenhouse"] == ["acme"]

    def test_works_on_a_fresh_empty_boards_section(self, tmp_path):
        """The starter config.default.yaml ships with boards: {greenhouse:
        [], lever: [], ashby: []} — adding to an empty list must work."""
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG.replace("boards:\n  greenhouse: [acme]", "boards:\n  greenhouse: []"))

        add_boards([("greenhouse", "stripe")], path)

        assert load_editable(path).boards["greenhouse"] == ["stripe"]


class TestAddBoardsPreservesTrailingSectionComment:
    """
    Regression coverage for real, reproduced corruption: appending to
    boards.ashby in the actual config.yaml broke the file, because its
    last item ("opensea") carries a single merged CommentToken holding
    both its own inline "# OpenSea" note and the entire "11 companies
    still unresolved" section comment that follows, joined by a blank
    line. A plain .append() leaves that token keyed to opensea's old
    index — no longer the list's last item — and ruamel then emits the
    new item outside the boards: structure entirely.

    Two prior fix attempts were each wrong in their own way (see
    add_boards's docstring): moving the whole token mislabeled the new
    item with opensea's own note; splitting the token without
    preserving the blank-line separator put the section comment's first
    line inline after the new item instead of on its own line. This
    checks the actually-correct end state: both original items keep
    their own comments untouched in meaning, and the section comment
    still reads as a standalone block, not text attached to "notion".
    """

    def test_opensea_keeps_its_own_note_and_the_section_comment_stays_standalone(self, tmp_path):
        import shutil

        path = tmp_path / "config.yaml"
        shutil.copy(REAL_CONFIG_PATH, path)

        add_boards([("ashby", "notion")], path)

        result = path.read_text()
        assert "- opensea             # OpenSea\n" in result
        assert "- notion\n" in result
        assert "- notion              #" not in result  # nothing misattached inline
        assert "\n\n# 11 companies from companies.txt are still unresolved" in result

        assert load_editable(path).boards["ashby"][-2:] == ["opensea", "notion"]

        import yaml as pyyaml
        pyyaml.safe_load(result)  # still valid, and still parses under the read-time loader too


class TestRemoveBoard:
    def test_removes_an_existing_slug(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        remove_board("greenhouse", "acme", path)

        assert load_editable(path).boards["greenhouse"] == []

    def test_missing_slug_is_a_no_op_not_an_error(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        remove_board("greenhouse", "not-there", path)  # must not raise

        assert load_editable(path).boards["greenhouse"] == ["acme"]

    def test_unknown_ats_is_a_no_op_not_an_error(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        remove_board("lever", "acme", path)  # lever key doesn't exist at all

        assert load_editable(path).boards["greenhouse"] == ["acme"]

    def test_add_then_remove_round_trips_to_original(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(MINIMAL_CONFIG)

        add_boards([("greenhouse", "figma")], path)
        remove_board("greenhouse", "figma", path)

        assert load_editable(path).boards["greenhouse"] == ["acme"]


class TestEmptyingAListDoesNotCorruptTheFile:
    """
    Regression coverage for a real, fully single-threaded bug: clearing
    a Settings textarea to empty and saving corrupted config.yaml.
    ruamel dumped the cleared CommentedSeq as a bare `[]` at column 0
    instead of inline as `key: []`, which isn't valid in that position
    and breaks parsing for everything after it. Reproduced against the
    real file, not MINIMAL_CONFIG — found by testing the actual
    anchor-bearing file, and that's the only copy proven to trigger it.
    """

    def _round_trip_with_empty(self, tmp_path, field: str) -> None:
        import shutil
        import yaml as pyyaml

        path = tmp_path / "config.yaml"
        shutil.copy(REAL_CONFIG_PATH, path)

        current = load_editable(path)
        kwargs = current.__dict__.copy()
        kwargs[field] = []
        apply_editable(EditableSettings(**kwargs), path)

        pyyaml.safe_load(path.read_text())  # must not raise

    def test_empty_role_include(self, tmp_path):
        self._round_trip_with_empty(tmp_path, "role_include")

    def test_empty_role_exclude(self, tmp_path):
        self._round_trip_with_empty(tmp_path, "role_exclude")

    def test_empty_seniority_preferred(self, tmp_path):
        self._round_trip_with_empty(tmp_path, "seniority_preferred")

    def test_empty_seniority_penalized(self, tmp_path):
        self._round_trip_with_empty(tmp_path, "seniority_penalized")

    def test_the_emptied_field_is_actually_saved_as_empty(self, tmp_path):
        """Not just 'doesn't crash' — the empty save has to actually
        stick, not silently keep the old values."""
        import shutil

        path = tmp_path / "config.yaml"
        shutil.copy(REAL_CONFIG_PATH, path)

        current = load_editable(path)
        kwargs = current.__dict__.copy()
        kwargs["seniority_penalized"] = []
        apply_editable(EditableSettings(**kwargs), path)

        assert load_editable(path).seniority_penalized == []


class TestConcurrentAccess:
    """
    Regression coverage for actual, reproduced file corruption: this
    module had no locking at all, and two threads racing add_boards()
    against the same file corrupted it — sometimes wrong content,
    sometimes an unparseable file. This isn't a synthetic worry; it's
    what broke when the GUI's "resolve a company" background thread
    (which chains straight into a poll trigger) happened to run
    alongside another Settings action.

    Not fully deterministic by nature of testing real threads, but with
    enough concurrent readers and writers this reliably reproduced the
    corruption before the fix and reliably doesn't after.
    """

    def test_concurrent_writes_and_reads_never_corrupt_the_file(self, tmp_path):
        import shutil
        import threading

        import yaml as pyyaml

        path = tmp_path / "config.yaml"
        shutil.copy(REAL_CONFIG_PATH, path)
        errors: list[Exception] = []

        def add_worker(name: str) -> None:
            try:
                add_boards([("greenhouse", name)], path)
            except Exception as exc:
                errors.append(exc)

        def settings_worker() -> None:
            try:
                apply_editable(load_editable(path), path)
            except Exception as exc:
                errors.append(exc)

        def read_worker() -> None:
            try:
                for _ in range(5):
                    load_editable(path)
            except Exception as exc:
                errors.append(exc)

        threads = (
            [threading.Thread(target=add_worker, args=(f"race{i}",)) for i in range(6)]
            + [threading.Thread(target=settings_worker) for _ in range(4)]
            + [threading.Thread(target=read_worker) for _ in range(6)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        data = pyyaml.safe_load(path.read_text())  # must still parse
        added = sorted(s for s in data["boards"]["greenhouse"] if s.startswith("race"))
        assert added == [f"race{i}" for i in range(6)]  # every write landed, none lost
