"""
Where jobtracker's files live — source checkout vs packaged app.

Running from source (this repo, `pip install -e .`), writable state
(config.yaml, the database, profile/, .env) lives in the project root,
exactly where it always has — dev mode changes nothing about the
existing setup, on purpose, so this refactor can't silently orphan
anyone's already-running config or database.

Running as a packaged app (PyInstaller sets sys.frozen), writable
state lives inside the .app bundle itself — Contents/Resources/appdata
— rather than the OS's usual per-user data directory. That's a
deliberate departure from the normal convention (and from what this
module did before): a location outside the bundle survives the app
being deleted, which is exactly backwards for a single-user local tool
someone might want to cleanly remove — drag jobtracker.app to the
Trash and every trace, config included, goes with it in that one
action. No separate "uninstall" step, no orphaned folder in ~/Library
nobody remembers to clean up. Writing into a signed bundle is
against Apple's general guidance and would be a real problem for an
App Store or notarized-for-wide-distribution build, but this one is
personal, unsigned/ad-hoc-signed, and only ever runs on the machine
that built it, so that tradeoff doesn't apply here. New files land in
their own fresh, never-signed subdirectory rather than overwriting
anything PyInstaller placed, which keeps this from touching whatever
the bundle's own signature actually covers.

Bundled read-only resources (templates/, the starter config template)
are located differently again when frozen: PyInstaller extracts them
under sys._MEIPASS, not next to this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# Dev mode only. Two different bases, not one — writable state (config,
# database) lives at the project root, but bundled resources like
# templates/ live inside the package directory itself
# (src/jobtracker/templates/), one level down from the root.
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_DIR = Path(__file__).resolve().parent


def _bundle_data_dir() -> Path:
    """
    Contents/Resources/appdata inside the running .app. Packaged-app
    mode only.

    Derived from sys.executable (PyInstaller sets it to the real
    bootloader binary at Contents/MacOS/jobtracker), not sys._MEIPASS
    — _MEIPASS's exact location relative to the bundle isn't part of
    PyInstaller's stable contract the way the executable's own path is.
    """
    bundle_contents = Path(sys.executable).resolve().parent.parent
    return bundle_contents / "Resources" / "appdata"


def writable_dir() -> Path:
    """Directory for anything jobtracker needs to read AND write."""
    if not FROZEN:
        return _SOURCE_ROOT
    d = _bundle_data_dir()
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
