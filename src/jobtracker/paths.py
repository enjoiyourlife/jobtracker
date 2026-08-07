"""
Where jobtracker's files live — source checkout vs packaged app.

Running from source (this repo, `pip install -e .`), writable state
(config.yaml, the database, profile/, .env) lives in the project root,
exactly where it always has — dev mode changes nothing about the
existing setup, on purpose, so this refactor can't silently orphan
anyone's already-running config or database.

Running as a packaged app (PyInstaller sets sys.frozen), the same
files live in the OS's actual per-user data directory instead. A
double-clicked .app can't write into its own bundle — and shouldn't
try to; /Applications is meant to be read-only — and there's no
"project root" once the source tree isn't there at all.

Bundled read-only resources (templates/, the starter config template)
are located differently again when frozen: PyInstaller extracts them
under sys._MEIPASS, not next to this file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# Dev mode only. Two different bases, not one — writable state (config,
# database) lives at the project root, but bundled resources like
# templates/ live inside the package directory itself
# (src/jobtracker/templates/), one level down from the root.
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_DIR = Path(__file__).resolve().parent


def _user_data_dir() -> Path:
    """OS-appropriate per-user data directory. Packaged-app mode only."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "jobtracker"


def writable_dir() -> Path:
    """Directory for anything jobtracker needs to read AND write."""
    if not FROZEN:
        return _SOURCE_ROOT
    d = _user_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_resource(*parts: str) -> Path:
    """
    A read-only file shipped with jobtracker — templates/, the starter
    config. Never written to.

    Dev mode resolves from the package directory (src/jobtracker/),
    where templates/ and config.default.yaml actually live alongside
    the code — not the project root, which is one level up and has no
    templates/ of its own.
    """
    base = Path(getattr(sys, "_MEIPASS", _PACKAGE_DIR)) if FROZEN else _PACKAGE_DIR
    return base.joinpath(*parts)


CONFIG_PATH = writable_dir() / "config.yaml"
DB_PATH = writable_dir() / "data" / "jobtracker.db"
PROFILE_DIR = writable_dir() / "profile"
ENV_PATH = writable_dir() / ".env"
POLLER_LOG_PATH = writable_dir() / "data" / "poller.log"


def ensure_default_config() -> None:
    """
    First launch of a packaged app: seed a starter config.yaml into the
    writable dir if one isn't there yet.

    Never overwrites an existing one — this is a one-time bootstrap for
    a brand-new install, not a way to reset someone's edited settings
    back to the template on every launch.
    """
    if CONFIG_PATH.exists():
        return
    template = bundled_resource("config.default.yaml")
    if template.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(template.read_text())
