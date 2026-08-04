"""
Lever job board client.

Lever exposes postings as a bare JSON array:
    https://api.lever.co/v0/postings/{slug}?mode=json

Two quirks drive the code below:

  1. A missing board returns HTTP 200 carrying {"ok": false, ...} rather
     than a 404, so status codes alone cannot detect a bad slug.
  2. Timestamps are epoch milliseconds, not ISO strings, and are
     converted here so the persistence layer sees one format.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from jobtracker.ats.base import ATSError, RawJob

ATS_NAME = "lever"
BASE_URL = "https://api.lever.co/v0/postings/{slug}"
USER_AGENT = "jobtracker/0.1 (personal job search tool)"
TIMEOUT_SECONDS = 30.0


class LeverError(ATSError):
    """Raised when a board cannot be fetched or its payload is malformed."""


def _epoch_ms_to_iso(value: Any) -> str | None:
    """Convert Lever's epoch-milliseconds timestamps to ISO-8601 UTC."""
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (ValueError, OSError, OverflowError):
        return None


def fetch(slug: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """
    Retrieve a board's postings.

    Returns a dict wrapper so the return type matches the ATSClient
    contract; Lever's own response is a bare list, which is placed under
    a 'jobs' key here rather than special-casing the caller.

    Raises:
        LeverError: on network failure, non-2xx, invalid JSON, or the
                    HTTP-200 {"ok": false} form Lever uses for a missing
                    board.
    """
    url = BASE_URL.format(slug=slug)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, headers=headers)

    try:
        response = http.get(url, params={"mode": "json"}, headers=headers)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        raise LeverError(
            f"Board '{slug}' returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise LeverError(f"Network error fetching board '{slug}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LeverError(f"Board '{slug}' returned malformed JSON") from exc
    finally:
        if owned:
            http.close()

    # Soft failure: HTTP 200 with an error body.
    if isinstance(data, dict):
        raise LeverError(
            f"Board '{slug}' not found: {data.get('error', 'unknown error')}"
        )
    if not isinstance(data, list):
        raise LeverError(f"Board '{slug}' returned unexpected payload type")

    return {"jobs": data}


def parse(payload: dict[str, Any], slug: str) -> list[RawJob]:
    """
    Convert a board payload into RawJob records. Pure — no I/O.

    Lever names the title field 'text' and nests location under
    'categories'. descriptionPlain is preferred over the HTML variant
    since Lever supplies both.
    """
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise LeverError(f"Board '{slug}' payload missing 'jobs' list")

    parsed: list[RawJob] = []
    for entry in jobs:
        try:
            ats_job_id = str(entry["id"])
            title = entry["text"]
            absolute_url = entry.get("hostedUrl") or entry["applyUrl"]
        except (KeyError, TypeError):
            continue

        categories = entry.get("categories") or {}
        location = (
            categories.get("location") if isinstance(categories, dict) else None
        )

        description = entry.get("descriptionPlain") or entry.get("description")

        parsed.append(
            RawJob(
                global_id=f"{ATS_NAME}:{slug}:{ats_job_id}",
                ats_job_id=ats_job_id,
                title=title,
                location=location,
                absolute_url=absolute_url,
                description=description,
                updated_at=_epoch_ms_to_iso(entry.get("createdAt")),
                raw_payload=json.dumps(entry, separators=(",", ":")),
            )
        )

    return parsed


def company_name(parsed: list[RawJob], slug: str) -> str:
    """Lever exposes no company name field; the slug is the best available."""
    return slug