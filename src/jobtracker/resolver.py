"""
Board slug resolver.

Mapping a company name to its board token is the tedious part of
building a target list: the token is usually a lowercased,
punctuation-stripped name, but often is not ("Hewlett Packard" ->
"hpe", "Airbnb" -> "airbnb", "Chan Zuckerberg Initiative" -> "czi").
And the same company might be on Greenhouse, Lever, or Ashby — there's
no way to know without asking each.

Rather than guess in a browser, generate candidate slugs and probe
each against every registered ATS's real fetch(), in the order they
appear in the registry. Reusing fetch() rather than reimplementing the
HTTP call means a probe's notion of "not found" never drifts from the
poller's — the same 404/soft-failure handling each client already has
is exactly what decides a miss here.

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

from jobtracker.ats import REGISTRY
from jobtracker.ats.base import ATSError

# Courtesy delay between probes. These are public endpoints, but there
# is no reason to hammer them — this is a background task, not a race.
PROBE_DELAY_SECONDS = 0.3
PROBE_TIMEOUT_SECONDS = 10.0
USER_AGENT = "jobtracker/0.1 (personal job search tool)"

_PUNCT = re.compile(r"[^a-z0-9\s]")
_SUFFIXES = (" inc", " llc", " ltd", " corp", " corporation", " co", " technologies")


@dataclass(frozen=True)
class Resolution:
    """Outcome of probing one company name."""
    name: str
    ats: str | None = None
    slug: str | None = None
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


def probe(ats_name: str, slug: str, client: httpx.Client) -> int | None:
    """
    Test one (ats, slug) pair via that ATS's own fetch(). Returns the
    board's job count, or None if it is not a live board on that ATS.

    A response carrying zero jobs is treated as a miss: an existing
    board with no postings is indistinguishable here from a wrong
    guess, and a false positive would poison the target list.
    """
    try:
        payload = REGISTRY[ats_name].fetch(slug, client=client)
    except (ATSError, httpx.RequestError):
        return None
    jobs = payload.get("jobs")
    return len(jobs) if jobs else None


def resolve(name: str, clients: dict[str, httpx.Client]) -> Resolution:
    """
    Probe each candidate slug for a name across every registered ATS;
    first hit wins. Slugs are tried before ATSes are alternated — a
    company is far more likely to be on one ATS under several plausible
    tokens than on several ATSes under the same token.
    """
    for slug in candidates(name):
        for ats_name, client in clients.items():
            count = probe(ats_name, slug, client)
            time.sleep(PROBE_DELAY_SECONDS)
            if count is not None:
                return Resolution(name=name, ats=ats_name, slug=slug, job_count=count)
    return Resolution(name=name)


def resolve_all(names: list[str]) -> list[Resolution]:
    """Resolve a batch, reusing one connection per ATS across all names."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    clients = {
        ats_name: httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, headers=headers)
        for ats_name in REGISTRY
    }
    try:
        return [resolve(n, clients) for n in names]
    finally:
        for client in clients.values():
            client.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve company names to board slugs across Greenhouse, Lever, and Ashby."
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

    print("# paste into config.yaml under boards.<ats> — grouped below")
    for ats_name in REGISTRY:
        group = sorted(
            (r for r in found if r.ats == ats_name), key=lambda r: -r.job_count
        )
        if not group:
            continue
        print(f"\n# {ats_name}:")
        for r in group:
            print(f"    - {r.slug}    # {r.name} ({r.job_count} jobs)")

    if missing:
        print(f"\n# unresolved ({len(missing)}) — find these manually", file=sys.stderr)
        for r in missing:
            print(f"#   {r.name}", file=sys.stderr)

    print(f"\n# {len(found)}/{len(results)} resolved", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())