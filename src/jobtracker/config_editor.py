"""
config.yaml editor — round-trip safe, for the GUI's Settings page.

filters.Criteria.load() reads config.yaml with plain PyYAML, which is
fine for reading but would be destructive for writing: dumping through
plain PyYAML flattens every hand-written comment and expands the
_non_us_countries anchor into a duplicated inline list, silently
rewriting sections nobody asked to change. ruamel.yaml's round-trip
mode loads and re-saves the file structurally intact, so editing five
values through a form doesn't clobber the other 250 lines.

Two things had to be gotten right for this to actually work, found by
testing against the real file, not a simplified fixture:

  1. Indentation. ruamel's own default sequence style differs from
     this file's (items indented 2 past their parent key), so without
     an explicit .indent() call, saving would reformat every list in
     the file — including ones nobody touched — not just the edited
     ones.

  2. Section-header comments ("# Seniority scoring...", "# Location
     scoring...") aren't stored as the next key's leading comment the
     way you'd expect. ruamel attaches them to the *previous list's
     last item*, in the same slot an inline end-of-line comment on
     that item would use — see _replace_list()'s docstring for how
     they're told apart and carried forward.

What's still an accepted loss, not a bug: per-item inline comments
(there are a few, like the note on "engineering intern") don't survive
a GUI-triggered save of that list, since the list is replaced wholesale
rather than diffed item-by-item, and there's no way to know which new
item a comment on a specific old item should now belong to. Everything
in the file this module never touches — the boards list,
location.disallow and its country anchor, all structural section
comments — survives because it's genuinely never rewritten, not
because of any preservation logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

from jobtracker import paths

CONFIG_PATH = paths.CONFIG_PATH

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096  # don't line-wrap long match/include lists
# Matches config.yaml's actual style (list items indented 2 past their
# parent key). ruamel's own default differs, so without this every
# dump reformats every list in the file, not just the edited ones.
_yaml.indent(mapping=2, sequence=4, offset=2)


@dataclass
class Tier:
    score: int
    cities: str  # comma-separated, as typed in the form


@dataclass
class EditableSettings:
    """The subset of config.yaml the Settings page can see and change."""
    tiers: list[Tier] = field(default_factory=list)
    role_include: list[str] = field(default_factory=list)
    role_exclude: list[str] = field(default_factory=list)
    seniority_preferred: list[str] = field(default_factory=list)
    seniority_penalized: list[str] = field(default_factory=list)
    max_years: int = 2
    penalty_per_year: int = 15
    min_score: int = 0
    browser: str = "system"  # which browser "Apply" opens postings in — see browser_launcher.py


def _flow_list(items: list[str]) -> CommentedSeq:
    """A YAML list rendered compactly ([a, b, c]) rather than one item per line."""
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def load_raw(path: Path | None = None) -> Any:
    """The full config as a ruamel CommentedMap — comments and anchors intact."""
    return _yaml.load((path or CONFIG_PATH).read_text())


def save_raw(data: Any, path: Path | None = None) -> None:
    with (path or CONFIG_PATH).open("w") as fh:
        _yaml.dump(data, fh)


def load_editable(path: Path | None = None) -> EditableSettings:
    raw = load_raw(path)
    return EditableSettings(
        tiers=[
            Tier(score=int(t["score"]), cities=", ".join(t["match"]))
            for t in raw["location"]["tiers"]
        ],
        role_include=list(raw["role"]["include"]),
        role_exclude=list(raw["role"].get("exclude", [])),
        seniority_preferred=list(raw["seniority"].get("preferred", [])),
        seniority_penalized=list(raw["seniority"].get("penalized", [])),
        max_years=int(raw["experience"].get("max_years", 2)),
        penalty_per_year=int(raw["experience"].get("penalty_per_year", 15)),
        min_score=int(raw.get("min_score", 0)),
        browser=str(raw.get("browser", "system")),
    )


def _replace_list(container: Any, key: str, items: list[str]) -> None:
    """
    Set container[key] to `items`, preserving one specific kind of
    comment ruamel attaches to the *sequence*, not any item in it.

    A "before the next section" comment (e.g. "# Seniority scoring...")
    isn't stored as the next key's leading comment the way you'd
    expect — ruamel attaches it to the *last item of the previous
    list*, in the same comment slot an inline end-of-line comment on
    that item would use. The only way to tell them apart is column
    position: an inline comment like "engineering intern  # remove
    this line..." starts well to the right, past the item's own text;
    a comment on its own line announcing what comes next is dedented
    back to (or past) the column the list's items start at. That's what
    gets carried forward, re-attached to the new list's last item —
    everything else (real per-item comments, which don't map onto a
    reordered/different list anyway) is discarded rather than risk
    silently reattaching stale text to the wrong item.
    """
    existing = container.get(key)
    if isinstance(existing, CommentedSeq):
        trailing = None
        if len(existing) > 0:
            entry = existing.ca.items.get(len(existing) - 1)
            token = entry[0] if entry else None
            if token is not None and token.start_mark.column < existing.lc.col:
                trailing = token
        existing.ca.items.clear()
        existing.clear()
        existing.extend(items)
        if trailing is not None and items:
            existing.ca.items[len(items) - 1] = [trailing, None, None, None]
    else:
        container[key] = items


def apply_editable(settings: EditableSettings, path: Path | None = None) -> None:
    """Write `settings` back into config.yaml. See module docstring for what's preserved."""
    raw = load_raw(path)

    raw["location"]["tiers"] = [
        {"score": t.score, "match": _flow_list([c.strip() for c in t.cities.split(",") if c.strip()])}
        for t in settings.tiers
        if t.cities.strip()
    ]
    _replace_list(raw["role"], "include", settings.role_include)
    _replace_list(raw["role"], "exclude", settings.role_exclude)
    _replace_list(raw["seniority"], "preferred", settings.seniority_preferred)
    _replace_list(raw["seniority"], "penalized", settings.seniority_penalized)
    raw["experience"]["max_years"] = settings.max_years
    raw["experience"]["penalty_per_year"] = settings.penalty_per_year
    raw["min_score"] = settings.min_score
    raw["browser"] = settings.browser

    save_raw(raw, path)
