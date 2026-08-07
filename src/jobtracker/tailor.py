"""
Answer-bank tailoring.

Two stages, deliberately separate — mirrors the classify()/score()
split in filters.py:

  select_variant()  — pure, offline, no API call. Picks the answer-bank
                       variant whose `use_when` tags best overlap the
                       posting's title and description.

  tailor_answer()    — one Claude API call. Retargets the selected
                       variant's wording to the specific posting:
                       company name, echoed keywords, tightened phrasing.

The system prompt in tailor_answer() carries a hard constraint: the
model may retarget wording but must never invent a skill, number, or
claim absent from the source text or the project detail handed to it.
A hallucinated "3 years of Kubernetes" in a submitted application is
worse than no tailoring at all, so this is enforced as an instruction
on every call rather than trusted to good judgment once.

Requires ANTHROPIC_API_KEY (see cli.py, which loads .env on startup).
Variant selection needs no key; only tailor_answer() does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import yaml

from jobtracker import paths

PROFILE_DIR = paths.PROFILE_DIR
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 400
DESCRIPTION_CHARS = 1500  # enough context to tailor against; keeps cost predictable

SYSTEM_PROMPT = """\
You adapt a job applicant's pre-written answer to fit one specific job \
posting. You will be given the answer to adapt, the posting it needs to \
fit, and optionally a factual project detail to draw specifics from.

Rules, non-negotiable:
1. Do not invent anything: no new skills, employers, numbers, projects, \
or years of experience beyond what is already stated in the answer or \
the project detail provided.
2. You may reference the company name, echo terms and technologies from \
the posting that genuinely overlap with what is already true, tighten \
phrasing, and reorder for emphasis.
3. Keep the applicant's voice and roughly the original length.
4. Output only the final answer text. No preamble, no markdown, no \
quotation marks around it.\
"""


class ProfileError(ValueError):
    """Raised when profile/ is missing or malformed."""


class TailorError(RuntimeError):
    """Raised when the Claude API call fails."""


@dataclass(frozen=True)
class Variant:
    id: str
    use_when: tuple[str, ...]
    text: str
    project_ref: str | None


@dataclass(frozen=True)
class Question:
    id: str
    prompts: tuple[str, ...]
    variants: tuple[Variant, ...]


@dataclass(frozen=True)
class Profile:
    """Validated view of profile/answers.yaml and profile/projects.yaml."""
    boilerplate: dict[str, str]
    questions: tuple[Question, ...]
    projects: dict[str, dict[str, Any]]  # project id -> raw project dict

    @staticmethod
    def load(path: Path | None = None) -> "Profile":
        base = path or PROFILE_DIR
        answers_path = base / "answers.yaml"
        if not answers_path.exists():
            raise ProfileError(
                f"No profile at {base}. Copy profile.example/ to profile/ "
                "and fill in your own answers first."
            )

        raw = yaml.safe_load(answers_path.read_text()) or {}
        questions = tuple(
            Question(
                id=q["id"],
                prompts=tuple(q.get("prompts", [])),
                variants=tuple(
                    Variant(
                        id=v["id"],
                        use_when=tuple(t.lower() for t in v.get("use_when", [])),
                        text=v["text"].strip(),
                        project_ref=v.get("project_ref"),
                    )
                    for v in q["variants"]
                ),
            )
            for q in raw.get("questions", [])
        )

        projects: dict[str, dict[str, Any]] = {}
        projects_path = base / "projects.yaml"
        if projects_path.exists():
            praw = yaml.safe_load(projects_path.read_text()) or {}
            projects = {p["id"]: p for p in praw.get("projects", [])}

        return Profile(
            boilerplate=raw.get("boilerplate") or {},
            questions=questions,
            projects=projects,
        )


def _score(variant: Variant, haystack: str) -> int:
    return sum(1 for tag in variant.use_when if tag in haystack)


def select_variant(question: Question, title: str, description: str | None) -> Variant:
    """
    Best-matching variant for a posting, by use_when tag overlap.

    A variant with no use_when tags scores zero, same as a tagged
    variant that simply didn't match — so a naive max() can't tell
    "deliberately universal" apart from "targeted but missed" and picks
    whichever is listed first. When nothing scores above zero, this
    explicitly prefers the empty-tag variant over that arbitrary pick,
    since an author writing `use_when: []` means "always applies," not
    "lowest priority."
    """
    haystack = f"{title} {description or ''}".lower()
    scored = [(_score(v, haystack), v) for v in question.variants]
    best_score, best = max(scored, key=lambda sv: sv[0])
    if best_score > 0:
        return best
    return next((v for v in question.variants if not v.use_when), best)


def tailor_answer(
    variant: Variant,
    project: dict[str, Any] | None,
    *,
    company: str,
    title: str,
    description: str | None,
    client: anthropic.Anthropic,
) -> str:
    """
    Retarget one answer-bank variant to a specific posting via Claude.

    project is the resolved projects.yaml entry for variant.project_ref
    (or None) — handed over as ground truth the model can pull specifics
    from but never contradict or extend.
    """
    parts = [f"Job: {title} at {company}"]
    if description:
        parts.append(f"Posting excerpt:\n{description[:DESCRIPTION_CHARS]}")
    if project:
        parts.append(f"Factual project detail (do not alter facts):\n{json.dumps(project)}")
    parts.append(f"Answer to adapt:\n{variant.text}")

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
        )
    except anthropic.APIError as exc:
        raise TailorError(f"Claude API call failed: {exc}") from exc

    return response.content[0].text.strip()
