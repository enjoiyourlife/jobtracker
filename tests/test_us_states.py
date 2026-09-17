"""us_states data tests — see the module's own docstring for why a
state suggestion inserts both name and abbreviation."""

from __future__ import annotations

from jobtracker.us_states import US_STATES


def test_fifty_states_plus_dc():
    assert len(US_STATES) == 51


def test_no_duplicate_names_or_abbreviations():
    names = [name for name, _ in US_STATES]
    abbrs = [abbr for _, abbr in US_STATES]
    assert len(names) == len(set(names))
    assert len(abbrs) == len(set(abbrs))


def test_abbreviations_are_two_letters():
    assert all(len(abbr) == 2 and abbr.isupper() for _, abbr in US_STATES)
