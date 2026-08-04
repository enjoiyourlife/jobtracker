"""
Greenhouse job board client.

Greenhouse exposes a public, unauthenticated JSON API per company board:
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Fetch and parse are deliberately separate: fetch performs network I/O,
parse is pure. This keeps parser tests fast, offline, and deterministic.
"""

from __future__ import annotations

import html
import json
from typing import Any

import httpx

from jobtracker.ats.base import ATSError, RawJob

ATS_NAME = "greenhouse"
BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
USER_AGENT = "jobtracker/0.1 (personal job search tool)"
TIMEOUT_SECONDS = 30.0


class GreenhouseError(ATSError):
    """Raised when a board cannot be fetched or its payload is malformed."""


def _strip_html(raw: str) -> str:
    """
    Greenhouse double-escapes `content`: '\\u0026lt;' -> '&lt;' -> '<'.
    Two unescape passes are required before tags are even visible.
    """
    return html.unescape(html.unescape(raw))


def fetch(slug: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """
    Retrieve a board's raw JSON payload.

    Raises:
        GreenhouseError: on network failure, non-2xx status, or invalid JSON.
    """
    url = BASE_URL.format(slug=slug)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, headers=headers)

    try:
        response = http.get(url, params={"content": "true"}, headers=headers)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise GreenhouseError(
            f"Board '{slug}' returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise GreenhouseError(f"Network error fetching board '{slug}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GreenhouseError(f"Board '{slug}' returned malformed JSON") from exc
    finally:
        if owned:
            http.close()


def parse(payload: dict[str, Any], slug: str) -> list[RawJob]:
    """
    Convert a board payload into RawJob records. Pure — no I/O.

    Malformed individual postings are skipped rather than aborting the
    batch: one bad row should not cost us the other 546.
    """
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise GreenhouseError(f"Board '{slug}' payload missing 'jobs' list")

    parsed: list[RawJob] = []
    for entry in jobs:
        try:
            ats_job_id = str(entry["id"])
            title = entry["title"]
            absolute_url = entry["absolute_url"]
        except (KeyError, TypeError):
            continue  # required field absent — skip this posting

        location_obj = entry.get("location") or {}
        location = location_obj.get("name") if isinstance(location_obj, dict) else None

        content = entry.get("content")
        description = _strip_html(content) if content else None

        parsed.append(
            RawJob(
                global_id=f"{ATS_NAME}:{slug}:{ats_job_id}",
                ats_job_id=ats_job_id,
                title=title,
                location=location,
                absolute_url=absolute_url,
                description=description,
                updated_at=entry.get("updated_at"),
                raw_payload=json.dumps(entry, separators=(",", ":")),
            )
        )

    return parsed


def company_name(parsed: list[RawJob], slug: str) -> str:
    """Greenhouse carries company_name per posting rather than board-level."""
    if not parsed:
        return slug
    try:
        return json.loads(parsed[0].raw_payload).get("company_name") or slug
    except (json.JSONDecodeError, AttributeError):
        return slug