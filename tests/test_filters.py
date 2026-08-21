"""
Classification and scoring tests.

Several cases here are regressions for bugs found by running against
live data: "Manager, Software Engineering" and "Staff Software Engineer"
both scored as matches because the exclude list held only the two-word
forms "engineering manager" and "staff engineer". Substring matching on
titles fails in exactly this way, so each fix is pinned by a test.
"""

from __future__ import annotations

import pytest

from jobtracker.filters import (
    Classification,
    ConfigError,
    Criteria,
    classify,
    classify_work_mode,
    extract_years,
    score,
    work_mode_allowed,
)


@pytest.fixture(scope="module")
def crit() -> Criteria:
    """The real config.yaml — these tests assert the shipped criteria."""
    return Criteria.load()


class TestConfigLoading:
    def test_loads_shipped_config(self, crit):
        assert crit.role_include
        assert crit.role_exclude
        assert crit.location_tiers

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            Criteria.load(tmp_path / "nope.yaml")

    def test_missing_section_raises(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("role:\n  include: [software engineer]\n")
        with pytest.raises(ConfigError):
            Criteria.load(path)

    def test_empty_include_raises(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            "role:\n  include: []\nseniority: {}\nlocation: {}\nexperience: {}\n"
        )
        with pytest.raises(ConfigError):
            Criteria.load(path)

    def test_missing_work_modes_defaults_to_all_three(self, tmp_path):
        """An old config.yaml with no work_modes key must behave exactly
        as it did before this existed — nothing hidden by default."""
        path = tmp_path / "config.yaml"
        path.write_text(
            "role:\n  include: [software engineer]\n"
            "seniority: {}\nlocation: {}\nexperience: {}\n"
        )
        assert Criteria.load(path).work_modes == {"remote", "hybrid", "in_person"}

    def test_explicit_work_modes_are_read(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            "role:\n  include: [software engineer]\n"
            "seniority: {}\n"
            "location:\n  work_modes: [remote]\n"
            "experience: {}\n"
        )
        assert Criteria.load(path).work_modes == {"remote"}


class TestClassification:
    @pytest.mark.parametrize(
        "title",
        [
            "Software Engineer",
            "Software Engineer, Backend",
            "Full Stack Developer",
            "SDET",
            "QA Engineer",
            "Member of Technical Staff",
        ],
    )
    def test_coding_roles_match(self, title, crit):
        assert classify(title, crit) is Classification.MATCH

    @pytest.mark.parametrize(
        "title",
        [
            "Sales Engineer",
            "Solutions Engineer",
            "Engineering Manager",
            "Technical Recruiter",
            "Product Manager",
        ],
    )
    def test_non_coding_roles_excluded(self, title, crit):
        assert classify(title, crit) is Classification.EXCLUDE

    @pytest.mark.parametrize(
        "title",
        ["Software Engineer Internship, Android", "Engineering Intern"],
    )
    def test_internships_excluded(self, title, crit):
        """'Engineering Intern' and 'Internship' are different substrings —
        both need their own exclude entry, or one phrasing slips through."""
        assert classify(title, crit) is Classification.EXCLUDE

    @pytest.mark.parametrize(
        "title",
        [
            "Manager, Software Engineering - Payload",
            "Staff Software Engineer - Money Team",
            "Systems PhD - Software Engineer",
            "Founding Full Stack Engineer, AI Incubation",
        ],
    )
    def test_regression_senior_titles_excluded(self, title, crit):
        """Each of these scored as a MATCH before the exclude list was fixed."""
        assert classify(title, crit) is Classification.EXCLUDE

    def test_exclude_beats_include(self, crit):
        """'Sales Engineer' contains no include term, but order still matters."""
        assert classify("Senior Sales Engineer", crit) is Classification.EXCLUDE

    @pytest.mark.parametrize(
        "title", ["Account Executive", "Data Scientist", "Chef de Partie"]
    )
    def test_unknown_titles_held_for_review(self, title, crit):
        """Unrecognized titles must be reviewable, never silently dropped."""
        assert classify(title, crit) is Classification.UNCLASSIFIED

    def test_case_insensitive(self, crit):
        assert classify("SOFTWARE ENGINEER", crit) is Classification.MATCH


class TestClassifyWorkMode:
    @pytest.mark.parametrize(
        "location", ["Remote", "Remote - US", "Remote (Bulgaria)", "REMOTE"]
    )
    def test_remote_locations(self, location):
        assert classify_work_mode(location) == "remote"

    @pytest.mark.parametrize("location", ["Hybrid - Seattle, WA", "Seattle (Hybrid)"])
    def test_hybrid_locations(self, location):
        assert classify_work_mode(location) == "hybrid"

    @pytest.mark.parametrize(
        "location", ["Seattle, WA", "San Francisco, CA", "United States", None, ""]
    )
    def test_everything_else_is_in_person(self, location):
        """A bare 'United States' is deliberately NOT treated as remote
        here — that's fine as a soft scoring signal (the location tiers
        already do that), but wrong to hard-hide a posting over."""
        assert classify_work_mode(location) == "in_person"


class TestWorkModeAllowed:
    def _criteria_with_modes(self, tmp_path, modes: str) -> Criteria:
        path = tmp_path / "config.yaml"
        path.write_text(
            "role:\n  include: [software engineer]\n"
            "seniority: {}\n"
            f"location:\n  work_modes: [{modes}]\n"
            "experience: {}\n"
        )
        return Criteria.load(path)

    def test_remote_only_hides_hybrid_and_in_person(self, tmp_path):
        criteria = self._criteria_with_modes(tmp_path, "remote")

        assert work_mode_allowed("Remote", criteria) is True
        assert work_mode_allowed("Hybrid - Seattle", criteria) is False
        assert work_mode_allowed("Seattle, WA", criteria) is False

    def test_in_person_only_hides_remote_and_hybrid(self, tmp_path):
        criteria = self._criteria_with_modes(tmp_path, "in_person")

        assert work_mode_allowed("Seattle, WA", criteria) is True
        assert work_mode_allowed("Remote", criteria) is False
        assert work_mode_allowed("Hybrid - Seattle", criteria) is False

    def test_remote_and_hybrid_hides_only_in_person(self, tmp_path):
        criteria = self._criteria_with_modes(tmp_path, "remote, hybrid")

        assert work_mode_allowed("Remote", criteria) is True
        assert work_mode_allowed("Hybrid - Seattle", criteria) is True
        assert work_mode_allowed("Seattle, WA", criteria) is False

    def test_all_three_hides_nothing(self, crit):
        """The default (shipped) config — nothing filtered on work mode."""
        assert work_mode_allowed("Remote", crit) is True
        assert work_mode_allowed("Hybrid - Seattle", crit) is True
        assert work_mode_allowed("Seattle, WA", crit) is True


class TestExtractYears:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("3+ years of experience", 3),
            ("2-4 years required", 2),
            ("at least 5 yrs", 5),
            ("minimum of 4 years", 4),
            ("8+ years backend, 3+ years distributed systems", 3),
        ],
    )
    def test_parses_common_phrasings(self, text, expected):
        assert extract_years(text) == expected

    def test_takes_lowest_stated_figure(self):
        """Postings list several; the lowest is the realistic floor."""
        assert extract_years("10+ years leadership. 2+ years Python.") == 2

    @pytest.mark.parametrize("text", [None, "", "No experience mentioned here."])
    def test_returns_none_when_absent(self, text):
        assert extract_years(text) is None

    def test_implausible_figures_ignored(self):
        """Guards against matching years like '2026' or marketing copy."""
        assert extract_years("Founded 40 years ago") is None


class TestScoring:
    def test_preferred_location_scores(self, crit):
        assert score("Software Engineer", "Seattle, WA", None, crit).location > 0

    def test_unlisted_location_scores_zero_not_negative(self, crit):
        """Locations never veto — an unlisted city ranks lower, not out."""
        assert score("Software Engineer", "Reykjavik", None, crit).location == 0

    def test_senior_title_penalized(self, crit):
        assert score("Senior Software Engineer", "Seattle", None, crit).seniority < 0

    def test_new_grad_title_rewarded(self, crit):
        assert score("Software Engineer, New Grad", "Seattle", None, crit).seniority > 0

    def test_excess_experience_penalized(self, crit):
        s = score("Software Engineer", "Seattle", "8+ years required", crit)
        assert s.experience < 0

    def test_within_max_years_not_penalized(self, crit):
        s = score("Software Engineer", "Seattle", "2+ years required", crit)
        assert s.experience == 0

    def test_total_is_sum_of_dimensions(self, crit):
        s = score("Senior Software Engineer", "Seattle, WA", "6+ years", crit)
        assert s.total == s.seniority + s.location + s.experience

    def test_breakdown_explains_every_adjustment(self, crit):
        """A rank must always be traceable to the dimension that caused it."""
        s = score("Senior Software Engineer", "Seattle, WA", "6+ years", crit)
        assert len(s.reasons) == 3

    def test_ranking_order_is_sane(self, crit):
        junior_local = score("Software Engineer, New Grad", "Seattle, WA", None, crit)
        senior_remote = score("Senior Software Engineer", "Remote", "8+ years", crit)
        assert junior_local.total > senior_remote.total

    def test_abbreviated_senior_title_penalized(self, crit):
        """
        Regression: `\\b` never matches right after 'sr.' because both
        sides of that position are non-word characters ('.' and the
        space after it), so the penalty silently never applied to
        titles using the abbreviation instead of the full word.
        """
        assert score("Sr. Software Engineer", "Seattle, WA", None, crit).seniority < 0

    def test_country_scoped_remote_does_not_score_as_us_remote(self, crit):
        """
        Regression: 'remote' is a plain substring match, so
        'Remote (Buenos Aires, Argentina)' scored the same +25 as a
        genuine US-remote posting. Now caught by location.disallow,
        which runs before tiers are considered at all — so this drops
        below min_score rather than merely losing the +25.
        """
        s = score("Software Engineer", "Remote (Buenos Aires, Argentina)", None, crit)
        assert s.total < crit.min_score

    def test_plain_remote_still_scores(self, crit):
        """The exclude list must veto country-scoped remote without
        breaking the common case of an unqualified 'Remote'."""
        assert score("Software Engineer", "Remote", None, crit).location > 0

    def test_foreign_onsite_location_falls_below_min_score(self, crit):
        """
        Regression: an onsite posting in a non-US country previously
        scored 0 for location like any other unlisted city, so a
        strong-seniority title (e.g. 'Software Engineer I') could still
        clear min_score despite requiring work authorization the
        candidate doesn't have.
        """
        s = score("Software Engineer I", "Beijing, China", None, crit)
        assert s.total < crit.min_score

    def test_disallowed_location_reason_is_traceable(self, crit):
        s = score("Software Engineer I", "Beijing, China", None, crit)
        assert any("china" in r for r in s.reasons)

    def test_us_location_unaffected_by_disallow_list(self, crit):
        assert score("Software Engineer", "Seattle, WA", None, crit).location > 0