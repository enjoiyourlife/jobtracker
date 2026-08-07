"""
settings_presets tests — the friendly<->raw translation the Settings
page relies on to never show a bare score number.
"""

from __future__ import annotations

from jobtracker.config_editor import Tier
from jobtracker.settings_presets import (
    EXPERIENCE_LEVELS,
    STRICTNESS_LEVELS,
    closest_priority,
    closest_strictness,
    guess_experience_level,
    split_remote,
    with_remote,
)


class TestPriority:
    def test_exact_scores_map_to_their_own_label(self):
        assert closest_priority(40) == "top"
        assert closest_priority(20) == "low"

    def test_off_preset_scores_snap_to_nearest(self):
        """A hand-edited config.yaml (score: 37) shouldn't crash the
        page — it should just round to the closest labeled choice."""
        assert closest_priority(37) == "high"  # |37-35|=2 beats |37-40|=3
        assert closest_priority(22) == "low"


class TestStrictness:
    def test_known_values_round_trip(self):
        for key, score in STRICTNESS_LEVELS.items():
            assert closest_strictness(score) == key

    def test_off_preset_snaps_to_nearest(self):
        assert closest_strictness(5) == "broad"
        assert closest_strictness(45) == "strict"


class TestExperienceLevel:
    def test_recognizes_each_preset_exactly(self):
        for level, (preferred, penalized) in EXPERIENCE_LEVELS.items():
            assert guess_experience_level(preferred, penalized) == level

    def test_order_does_not_matter(self):
        preferred, penalized = EXPERIENCE_LEVELS["entry"]
        assert guess_experience_level(list(reversed(preferred)), penalized) == "entry"

    def test_unrecognized_combination_is_custom(self):
        assert guess_experience_level(["some", "random", "terms"], []) == "custom"

    def test_hand_tweaked_preset_is_custom_not_silently_relabeled(self):
        """A preset plus one extra term is a real difference, not noise
        — claiming it's still 'entry level' would misrepresent it."""
        preferred, penalized = EXPERIENCE_LEVELS["entry"]
        assert guess_experience_level([*preferred, "extra term"], penalized) == "custom"


class TestRemoteSplit:
    def test_detects_remote_tier(self):
        tiers = [
            Tier(score=40, cities="seattle, bellevue"),
            Tier(score=25, cities="remote, remote - us, united states"),
        ]
        cities, remote_on = split_remote(tiers)
        assert remote_on is True
        assert cities == [Tier(score=40, cities="seattle, bellevue")]

    def test_no_remote_tier_present(self):
        tiers = [Tier(score=40, cities="seattle, bellevue")]
        cities, remote_on = split_remote(tiers)
        assert remote_on is False
        assert cities == tiers

    def test_with_remote_reattaches_when_on(self):
        cities = [Tier(score=40, cities="seattle")]
        result = with_remote(cities, remote_on=True)
        assert len(result) == 2
        assert "remote" in result[1].cities

    def test_with_remote_no_op_when_off(self):
        cities = [Tier(score=40, cities="seattle")]
        assert with_remote(cities, remote_on=False) == cities

    def test_split_then_with_remote_round_trips(self):
        original = [
            Tier(score=40, cities="seattle, bellevue"),
            Tier(score=25, cities="remote, remote - us, united states"),
        ]
        cities, remote_on = split_remote(original)
        assert with_remote(cities, remote_on) == original
