"""
Friendly <-> raw translation for the Settings page.

config_editor.py and filters.py deal in the scoring model's actual
vocabulary — numeric scores, raw term lists — because that's what
config.yaml and the scoring engine need. Nobody should have to
understand that vocabulary just to say "Seattle first, remote's fine,
I'm a new grad" — recognizing a labeled choice from a short list beats
recalling what number "40" versus "35" is supposed to mean, and this
module is the translation layer that makes that possible without
changing what's actually stored on disk.

Each mapping is deliberately lossy in one direction: a numeric score
that doesn't exactly match a preset (someone hand-edited config.yaml
to 37) rounds to the nearest labeled choice for display, but saving
from the form always writes the preset's exact value. That's a
one-way snap, not a bug — the alternative is showing raw numbers again
for anything that doesn't fit neatly, which defeats the point.
"""

from __future__ import annotations

from jobtracker.config_editor import Tier

REMOTE_TOKENS = {"remote", "remote - us", "united states"}
REMOTE_SCORE = 25

# (key, score, label) — ordered highest to lowest for display.
PRIORITY_LEVELS = [
    ("top", 40, "Top priority"),
    ("high", 35, "High priority"),
    ("medium", 30, "Medium priority"),
    ("low", 20, "Low priority"),
]
PRIORITY_SCORE = {key: score for key, score, _ in PRIORITY_LEVELS}


def closest_priority(score: int) -> str:
    """The priority key whose score is nearest an arbitrary existing score."""
    return min(PRIORITY_SCORE, key=lambda k: abs(PRIORITY_SCORE[k] - score))


# level -> (preferred terms, penalized terms)
EXPERIENCE_LEVELS: dict[str, tuple[list[str], list[str]]] = {
    "entry": (
        ["junior", "associate", "new grad", "new graduate", "new college grad",
         "recent grad", "recent graduate", "university grad", "entry level",
         "early career", "early in career", "class of 20", "i", "1"],
        ["senior", "sr.", "lead", "staff", "iii", "iv"],
    ),
    "mid": (
        ["ii", "2", "mid-level", "midlevel"],
        [],
    ),
    "senior": (
        ["senior", "sr.", "staff", "lead", "iii", "iv"],
        ["junior", "entry level", "new grad", "intern"],
    ),
}

EXPERIENCE_LABELS = {
    "entry": "New grad / entry level",
    "mid": "A couple years of experience",
    "senior": "Senior / experienced",
    "custom": "Custom (set my own terms below)",
}

# key -> min_score
STRICTNESS_LEVELS = {"broad": 0, "balanced": 20, "strict": 40}
STRICTNESS_LABELS = {
    "broad": "Show me everything remotely relevant",
    "balanced": "Balanced (recommended)",
    "strict": "Only strong matches",
}


def closest_strictness(min_score: int) -> str:
    return min(STRICTNESS_LEVELS, key=lambda k: abs(STRICTNESS_LEVELS[k] - min_score))


def guess_experience_level(preferred: list[str], penalized: list[str]) -> str:
    """
    Best-effort reverse mapping so the radio button defaults sanely.

    An exact set match (order doesn't matter) against a known preset
    wins; anything else — including a preset someone then hand-tweaked
    — is 'custom', since claiming it's still "entry level" would be
    guessing at intent rather than reading it off the data.
    """
    current = (sorted(preferred), sorted(penalized))
    for level, (pref, pen) in EXPERIENCE_LEVELS.items():
        if current == (sorted(pref), sorted(pen)):
            return level
    return "custom"


def split_remote(tiers: list[Tier]) -> tuple[list[Tier], bool]:
    """
    Separate the remote tier (if present) from city tiers.

    A tier counts as "the remote tier" if any of its comma-separated
    entries is one of REMOTE_TOKENS — matches how it's actually written
    in config.yaml today (`remote, remote - us, united states`) without
    requiring an exact whole-tier match.
    """
    cities: list[Tier] = []
    remote_on = False
    for t in tiers:
        names = {c.strip().lower() for c in t.cities.split(",")}
        if names & REMOTE_TOKENS:
            remote_on = True
        else:
            cities.append(t)
    return cities, remote_on


def with_remote(city_tiers: list[Tier], remote_on: bool) -> list[Tier]:
    """Inverse of split_remote: reattach the remote tier if it's wanted."""
    if not remote_on:
        return city_tiers
    return [*city_tiers, Tier(score=REMOTE_SCORE, cities="remote, remote - us, united states")]
