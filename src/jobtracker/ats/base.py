"""
Shared ATS contract.

Every ATS client normalizes to the same RawJob shape and exposes the
same fetch/parse pair. The Protocol below is that contract stated
explicitly: a type checker verifies each client satisfies it, and the
poller depends only on the contract rather than on any concrete module.

Adding a fourth ATS means writing one module and registering it — no
change to the poller, the persistence layer, or the filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class ATSError(RuntimeError):
    """
    Base for all ATS fetch/parse failures.

    The poller catches this single type, so a new client's errors are
    handled correctly without the poller knowing the client exists.
    """


@dataclass(frozen=True)
class RawJob:
    """
    One normalized posting, pre-persistence.

    Frozen because a parsed posting is a value, not mutable state —
    anything wanting a variant constructs a new one.

    global_id is "{ats}:{slug}:{ats_job_id}" and is the sole key used
    for deduplication and upserts across every source.
    """
    global_id: str
    ats_job_id: str
    title: str
    location: str | None
    absolute_url: str
    description: str | None
    updated_at: str | None
    raw_payload: str


@runtime_checkable
class ATSClient(Protocol):
    """
    Structural contract for an ATS client module.

    Structural rather than inherited: a plain module satisfies this by
    defining the right names, so clients stay simple modules instead of
    classes that exist only to satisfy a base class.
    """

    ATS_NAME: str

    def fetch(self, slug: str) -> dict[str, Any]:
        """Retrieve a board's raw payload. Raises ATSError on failure."""
        ...

    def parse(self, payload: dict[str, Any], slug: str) -> list[RawJob]:
        """Convert a payload to RawJob records. Pure — no I/O."""
        ...