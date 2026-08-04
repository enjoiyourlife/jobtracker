"""
Board slug resolver.

Mapping a company name to its Greenhouse board token is the tedious
part of building a target list: the token is usually a lowercased,
punctuation-stripped name, but often is not ("Hewlett Packard" ->
"hpe", "Airbnb" -> "airbnb", "Chan Zuckerberg Initiative" -> "czi").

Rather than guess in a browser, generate candidate slugs, probe each
against the live board endpoint, and report the first that responds.
Unresolved names are returned separately for manual lookup.

Usage:
    python -m jobtracker.resolver "Zillow" "Remitly" "Nordstrom"
    python -m jobtracker.resolver --file companies.txt
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass

import httpx

from jobtracker.ats.greenhouse import BASE_URL, USER_AGENT

# Courtesy delay between probes. These are public endpoints, but there
# is no reason to hammer them — this is a background task, not a race.
PROBE_DELAY_SECONDS = 0.3
PROBE_TIMEOUT_SECONDS = 10.0

_PUNCT = re.compile(r"[^a-z0-9\s]")
_SUFFIXES = (" inc", " llc", " ltd", " corp", " corporation", " co", " technologies")


@dataclass(frozen=True)
class Resolution:
    """Outcome of probing one company name."""
    name: str
    slug: str | None
    job_count: int = 0

    @property
    def resolved(self) -> bool:
        return self.slug is not None


def candidates(name: str) -> list[str]:
    """
    Generate plausible board tokens for a company name, most likely first.

    Greenhouse tokens are lowercase and unpunctuated; the common forms are
    the name collapsed, hyphenated, or with a legal suffix dropped.
    """
    base = _PUNCT.sub("", name.strip().lower())

    for suffix in _SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)].strip()
            break

    words = base.split()
    if not words:
        return []

    forms = [
        "".join(words),          # "remitly"      / "chanzuckerberg"
        "-".join(words),         # "chan-zuckerberg"
        words[0],                # "chan"
        "".join(w[0] for w in words) if len(words) > 1 else "",  # "czi"
    ]

    seen: set[str] = set()
    return [f for f in forms if f and not (f in seen or seen.add(f))]


def probe(slug: str, client: httpx.Client) -> int | None:
    """
    Test one slug. Returns the board's job count, or None if it is not
    a live Greenhouse board.

    A 200 carrying an empty 'jobs' list is treated as a miss: an existing
    board with zero postings is indistinguishable here from a wrong guess,
    and a false positive would poison the target list.
    """
    try:
        response = client.get(
            BASE_URL.format(slug=slug),
            params={"content": "false"},
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return None
        jobs = response.json().get("jobs")
        return len(jobs) if jobs else None
    except (httpx.RequestError, ValueError):
        return None


def resolve(name: str, client: httpx.Client) -> Resolution:
    """Probe each candidate slug for a name; first hit wins."""
    for slug in candidates(name):
        count = probe(slug, client)
        time.sleep(PROBE_DELAY_SECONDS)
        if count is not None:
            return Resolution(name=name, slug=slug, job_count=count)
    return Resolution(name=name, slug=None)


def resolve_all(names: list[str]) -> list[Resolution]:
    """Resolve a batch over one reused connection."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(headers=headers) as client:
        return [resolve(n, client) for n in names]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve company names to Greenhouse board slugs."
    )
    parser.add_argument("names", nargs="*", help="Company names")
    parser.add_argument("--file", help="Text file with one company name per line")
    args = parser.parse_args()

    names = list(args.names)
    if args.file:
        with open(args.file) as fh:
            names.extend(line.strip() for line in fh if line.strip())

    if not names:
        parser.error("provide company names or --file")

    results = resolve_all(names)
    found = [r for r in results if r.resolved]
    missing = [r for r in results if not r.resolved]

    print("# paste into config.yaml under boards.greenhouse")
    for r in sorted(found, key=lambda r: -r.job_count):
        print(f"    - {r.slug}    # {r.name} ({r.job_count} jobs)")

    if missing:
        print(f"\n# unresolved ({len(missing)}) — find these manually", file=sys.stderr)
        for r in missing:
            print(f"#   {r.name}", file=sys.stderr)

    print(f"\n# {len(found)}/{len(results)} resolved", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())