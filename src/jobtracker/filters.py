"""
Role classification and scoring.

Two distinct stages, deliberately separate:

  classify()  — hard filter on title. Returns MATCH, EXCLUDE, or
                UNCLASSIFIED. Nothing is deleted; UNCLASSIFIED is a
                review bucket, so a real coding role with an unusual
                title ("Member of Technical Staff") surfaces for
                inspection rather than vanishing silently.

  score()     — soft ranking over seniority, location, and years of
                experience. Each dimension is independent and reports
                its own contribution, so a low score can always be
                traced to the dimension responsible.

Criteria live in config.yaml. This module contains no titles, cities,
or weights of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"

# "3+ years", "3-5 years", "minimum of 4 years", "at least 2 yrs"
_YOE_PATTERN = re.compile(
    r"(\d+)\s*(?:\+|-\s*\d+)?\s*(?:\+\s*)?(?:years?|yrs?)\b", re.IGNORECASE
)

# Large enough that no combination of seniority/experience points can
# offset it, so a disallowed location reliably drops below min_score —
# an effective veto without adding a second hard-filter mechanism
# alongside classify().
DISALLOWED_LOCATION_PENALTY = -1000


class Classification(str, Enum):
    MATCH = "match"
    EXCLUDE = "exclude"
    UNCLASSIFIED = "unclassified"


class ConfigError(ValueError):
    """Raised when config.yaml is missing required structure."""


@dataclass(frozen=True)
class ScoreBreakdown:
    """
    A score plus its provenance.

    Storing per-dimension contributions rather than a bare total is what
    makes the ranking auditable — "why is this job at rank 40" has an
    answer.
    """
    total: int
    seniority: int
    location: int
    experience: int
    reasons: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Criteria:
    """Validated view of config.yaml. Constructed via Criteria.load()."""
    role_include: tuple[str, ...]
    role_exclude: tuple[str, ...]
    seniority_preferred: tuple[str, ...]
    seniority_penalized: tuple[str, ...]
    location_tiers: tuple[tuple[int, tuple[str, ...]], ...]
    location_disallow: tuple[str, ...]
    max_years: int
    penalty_per_year: int
    min_score: int
    boards: dict[str, tuple[str, ...]]

    @staticmethod
    def load(path: Path | None = None) -> "Criteria":
        """
        Read and validate config.yaml.

        Fails loudly on missing sections rather than defaulting silently —
        a typo'd key should not quietly disable a filter.
        """
        cfg_path = path or DEFAULT_CONFIG_PATH
        if not cfg_path.exists():
            raise ConfigError(f"Config not found: {cfg_path}")

        raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text()) or {}

        for section in ("role", "seniority", "location", "experience"):
            if section not in raw:
                raise ConfigError(f"config.yaml missing required section: '{section}'")

        role = raw["role"]
        if not role.get("include"):
            raise ConfigError("config.yaml: role.include must be a non-empty list")

        tiers: list[tuple[int, tuple[str, ...]]] = []
        for tier in raw["location"].get("tiers", []):
            tiers.append((
                int(tier["score"]),
                tuple(_lower(t) for t in tier["match"]),
            ))

        boards = {
            ats: tuple(slugs or ())
            for ats, slugs in (raw.get("boards") or {}).items()
        }

        return Criteria(
            role_include=tuple(_lower(x) for x in role["include"]),
            role_exclude=tuple(_lower(x) for x in role.get("exclude", [])),
            seniority_preferred=tuple(
                _lower(x) for x in raw["seniority"].get("preferred", [])
            ),
            seniority_penalized=tuple(
                _lower(x) for x in raw["seniority"].get("penalized", [])
            ),
            location_tiers=tuple(tiers),
            location_disallow=tuple(
                _lower(x) for x in raw["location"].get("disallow", [])
            ),
            max_years=int(raw["experience"].get("max_years", 2)),
            penalty_per_year=int(raw["experience"].get("penalty_per_year", 15)),
            min_score=int(raw.get("min_score", 0)),
            boards=boards,
        )


def _lower(value: Any) -> str:
    return str(value).strip().lower()


def _bounded_pattern(term: str) -> str:
    """
    Word-boundary regex for a short term, tolerant of trailing punctuation.

    `\\b` only fires between a word char and a non-word char, so a term
    like "sr." breaks it: the boundary right after the period sits
    between two non-word characters ('.' and the space that follows),
    and `\\b` never matches there. Terms are checked lowercased, so the
    only realistic punctuation is a trailing period ("sr.") — this drops
    to a whitespace/end lookahead on whichever side isn't a word
    character, instead of demanding an impossible word/non-word boundary.
    """
    escaped = re.escape(term)
    left = r"\b" if term[0].isalnum() else r"(?<!\S)"
    right = r"\b" if term[-1].isalnum() else r"(?!\S)"
    return f"{left}{escaped}{right}"


def _contains_term(haystack: str, terms: tuple[str, ...]) -> str | None:
    """
    Return the first term present in haystack, or None.

    Single-character and numeric markers ("i", "1") are matched on word
    boundaries; substring matching would fire on every title containing
    the letter i.
    """
    for term in terms:
        if len(term) <= 3:
            if re.search(_bounded_pattern(term), haystack):
                return term
        elif term in haystack:
            return term
    return None


def classify(title: str, criteria: Criteria) -> Classification:
    """
    Hard filter on title. Exclusions are evaluated first, since a title
    like "Sales Engineer" matches an include term but is not the role.
    """
    lowered = title.lower()

    if _contains_term(lowered, criteria.role_exclude):
        return Classification.EXCLUDE
    if _contains_term(lowered, criteria.role_include):
        return Classification.MATCH
    return Classification.UNCLASSIFIED


def extract_years(description: str | None) -> int | None:
    """
    Lowest experience figure stated in a description, or None.

    The minimum is taken because postings list several ("3+ years backend,
    5+ years distributed systems") and the lowest is the realistic floor.
    """
    if not description:
        return None
    found = [int(m) for m in _YOE_PATTERN.findall(description)]
    plausible = [y for y in found if 0 < y <= 20]
    return min(plausible) if plausible else None


def score(
    title: str, location: str | None, description: str | None, criteria: Criteria
) -> ScoreBreakdown:
    """
    Rank a posting across three independent dimensions.

    Dimensions never veto in the sense of removing a posting outright —
    only classify() does that, on title. Location is the one exception
    to "never veto on merit": a disallowed country isn't a worse fit,
    it's not legally available, so it's penalized hard enough to always
    fall below min_score rather than merely scoring low. See
    DISALLOWED_LOCATION_PENALTY.
    """
    lowered = title.lower()
    reasons: list[str] = []

    seniority_pts = 0
    if hit := _contains_term(lowered, criteria.seniority_penalized):
        seniority_pts = -40
        reasons.append(f"seniority: '{hit}' (-40)")
    elif hit := _contains_term(lowered, criteria.seniority_preferred):
        seniority_pts = 30
        reasons.append(f"seniority: '{hit}' (+30)")

    location_pts = 0
    loc_lower = (location or "").lower()
    disallowed = _contains_term(loc_lower, criteria.location_disallow)
    if disallowed:
        location_pts = DISALLOWED_LOCATION_PENALTY
        reasons.append(f"location: '{disallowed}' not a US location ({DISALLOWED_LOCATION_PENALTY})")
    else:
        for tier_score, terms in criteria.location_tiers:
            if hit := _contains_term(loc_lower, terms):
                location_pts = tier_score
                reasons.append(f"location: '{hit}' (+{tier_score})")
                break

    experience_pts = 0
    years = extract_years(description)
    if years is not None and years > criteria.max_years:
        over = years - criteria.max_years
        experience_pts = -(over * criteria.penalty_per_year)
        reasons.append(f"experience: {years}y required ({experience_pts})")

    total = seniority_pts + location_pts + experience_pts
    return ScoreBreakdown(
        total=total,
        seniority=seniority_pts,
        location=location_pts,
        experience=experience_pts,
        reasons=tuple(reasons),
    )