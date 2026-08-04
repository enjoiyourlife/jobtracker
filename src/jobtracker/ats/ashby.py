"""
Ashby job board client.

Ashby exposes a public board API:
    https://api.ashbyhq.com/posting-api/job-board/{slug}

The cleanest of the three sources: descriptionPlain arrives pre-stripped,
publishedAt is already ISO-8601, and compensation is genuinely populated
on most boards. Unlisted postings are filtered out — Ashby returns drafts
and internal roles alongside live ones.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from jobtracker.ats.base import ATSError, RawJob

ATS_NAME = "ashby"
BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
USER_AGENT = "jobtracker/0.1 (personal job search tool)"
TIMEOUT_SECONDS = 30.0


class AshbyError(ATSError):
    """Raised when a board cannot be fetched or its payload is malformed."""


def fetch(slug: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """
    Retrieve a board's raw JSON payload.

    Raises:
        AshbyError: on network failure, non-2xx status, or invalid JSON.
    """
    url = BASE_URL.format(slug=slug)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, headers=headers)

    try:
        response = http.get(
            url, params={"includeCompensation": "true"}, headers=headers
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise AshbyError(
            f"Board '{slug}' returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise AshbyError(f"Network error fetching board '{slug}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AshbyError(f"Board '{slug}' returned malformed JSON") from exc
    finally:
        if owned:
            http.close()


def parse(payload: dict[str, Any], slug: str) -> list[RawJob]:
    """
    Convert a board payload into RawJob records. Pure — no I/O.

    Postings with isListed false are skipped: Ashby includes drafts and
    internal-only roles in the same response as live public postings.
    """
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise AshbyError(f"Board '{slug}' payload missing 'jobs' list")

    parsed: list[RawJob] = []
    for entry in jobs:
        if not isinstance(entry, dict) or entry.get("isListed") is False:
            continue

        try:
            ats_job_id = str(entry["id"])
            title = entry["title"]
            absolute_url = entry.get("jobUrl") or entry["applyUrl"]
        except (KeyError, TypeError):
            continue

        location = entry.get("location")
        if entry.get("isRemote") and location:
            location = f"{location} (Remote)"

        parsed.append(
            RawJob(
                global_id=f"{ATS_NAME}:{slug}:{ats_job_id}",
                ats_job_id=ats_job_id,
                title=title,
                location=location,
                absolute_url=absolute_url,
                description=entry.get("descriptionPlain"),
                updated_at=entry.get("publishedAt"),
                raw_payload=json.dumps(entry, separators=(",", ":")),
            )
        )

    return parsed


def company_name(parsed: list[RawJob], slug: str) -> str:
    """Ashby exposes no company name field; the slug is the best available."""
    return slug