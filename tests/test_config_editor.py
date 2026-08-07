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

from jobtracker.config_editor import EditableSettings, Tier, load_editable, apply_editable

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
