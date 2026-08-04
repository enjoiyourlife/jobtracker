"""
ATS client registry.

The poller resolves a client by name through this mapping rather than
importing concrete modules, so supporting a new source means adding one
module and one entry here — nothing downstream changes.
"""

from __future__ import annotations

from types import ModuleType

from jobtracker.ats import ashby, greenhouse, lever
from jobtracker.ats.base import ATSError, RawJob

REGISTRY: dict[str, ModuleType] = {
    greenhouse.ATS_NAME: greenhouse,
    lever.ATS_NAME: lever,
    ashby.ATS_NAME: ashby,
}


def get_client(ats: str) -> ModuleType:
    """
    Look up a client module by ATS name.

    Raises:
        ATSError: if the name is not registered — surfaced as a normal
                  run failure rather than a KeyError traceback, since a
                  typo in config.yaml is a config problem, not a bug.
    """
    try:
        return REGISTRY[ats]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise ATSError(f"Unknown ATS '{ats}'. Registered: {known}") from None


__all__ = ["REGISTRY", "get_client", "ATSError", "RawJob"]