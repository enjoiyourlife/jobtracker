"""
US states, for the Settings page's location-priority autocomplete.

Matching against a posting's location string is plain substring/word-
boundary text matching (see filters._contains_term) — it has no idea
what a "city" or "state" is, so a state name or abbreviation already
works as a match term today exactly the way a city name does ("wa"
correctly matches "Seattle, WA" without false-matching inside "Walla
Walla" — the word-boundary handling for short terms already covers
this). The gap was never capability, only that nothing suggested
states existed to type. Suggesting a state inserts both its full name
and its abbreviation, since real postings write it either way
depending on the company/ATS — a state entered as just "washington"
would miss a posting listed as "Seattle, WA," and vice versa.
"""

from __future__ import annotations

US_STATES: tuple[tuple[str, str], ...] = (
    ("Alabama", "AL"), ("Alaska", "AK"), ("Arizona", "AZ"), ("Arkansas", "AR"),
    ("California", "CA"), ("Colorado", "CO"), ("Connecticut", "CT"),
    ("Delaware", "DE"), ("Florida", "FL"), ("Georgia", "GA"), ("Hawaii", "HI"),
    ("Idaho", "ID"), ("Illinois", "IL"), ("Indiana", "IN"), ("Iowa", "IA"),
    ("Kansas", "KS"), ("Kentucky", "KY"), ("Louisiana", "LA"), ("Maine", "ME"),
    ("Maryland", "MD"), ("Massachusetts", "MA"), ("Michigan", "MI"),
    ("Minnesota", "MN"), ("Mississippi", "MS"), ("Missouri", "MO"),
    ("Montana", "MT"), ("Nebraska", "NE"), ("Nevada", "NV"),
    ("New Hampshire", "NH"), ("New Jersey", "NJ"), ("New Mexico", "NM"),
    ("New York", "NY"), ("North Carolina", "NC"), ("North Dakota", "ND"),
    ("Ohio", "OH"), ("Oklahoma", "OK"), ("Oregon", "OR"), ("Pennsylvania", "PA"),
    ("Rhode Island", "RI"), ("South Carolina", "SC"), ("South Dakota", "SD"),
    ("Tennessee", "TN"), ("Texas", "TX"), ("Utah", "UT"), ("Vermont", "VT"),
    ("Virginia", "VA"), ("Washington", "WA"), ("West Virginia", "WV"),
    ("Wisconsin", "WI"), ("Wyoming", "WY"), ("District of Columbia", "DC"),
)
