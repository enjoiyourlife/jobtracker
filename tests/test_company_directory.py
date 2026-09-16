"""
company_directory tests.

This is suggestion data, not verified against any ATS — see the
module's own docstring for why that's fine (resolve_all() is what
actually verifies a name, same as it always has). What's worth
guarding here is just that the list stays usable as a list: no
duplicates to show twice in a suggestions dropdown, no blank/whitespace
entries that would suggest nothing.
"""

from __future__ import annotations

from jobtracker.company_directory import COMPANY_DIRECTORY


def test_not_empty():
    assert len(COMPANY_DIRECTORY) > 0


def test_no_duplicates():
    assert len(COMPANY_DIRECTORY) == len(set(COMPANY_DIRECTORY))


def test_no_blank_or_whitespace_only_entries():
    assert all(name.strip() == name and name for name in COMPANY_DIRECTORY)
