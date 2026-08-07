"""
Opens job postings in a real, separate browser — never inside the
app's own window.

The GUI runs in a WKWebView-backed native window (see gui.py): a
stripped-down browser with none of your saved passwords, autofill, or
extensions. Before this module existed, "Apply" issued a same-window
redirect, which took the app's own window to the job posting instead
of leaving it on the queue — convenient to write, wrong to use, since
filling out an application inside a bare webview is a strictly worse
experience than your actual browser. Every call site launches a
separate OS process instead of navigating in place.

macOS-specific (`open -a`) because the rest of this app already is —
pywebview's WKWebView, cron for scheduling. webbrowser.open() is the
one cross-platform fallback, used for "system default" and for any
named browser this module doesn't recognize.
"""

from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path

# name -> /Applications bundle path. "system" always means "OS default"
# and isn't listed here since it has no bundle of its own to check.
_KNOWN_BROWSERS = {
    "Safari": "/Applications/Safari.app",
    "Google Chrome": "/Applications/Google Chrome.app",
    "Firefox": "/Applications/Firefox.app",
    "Microsoft Edge": "/Applications/Microsoft Edge.app",
    "Brave Browser": "/Applications/Brave Browser.app",
    "Arc": "/Applications/Arc.app",
    "Opera": "/Applications/Opera.app",
}

SYSTEM_DEFAULT = "system"


def available_browsers() -> list[str]:
    """
    Browsers actually installed on this Mac, 'system' always first.

    Checked by bundle presence rather than assumed, so the Settings
    dropdown never offers a choice that would silently fail — a
    browser you uninstalled six months ago won't still be listed.
    """
    installed = [name for name, path in _KNOWN_BROWSERS.items() if Path(path).exists()]
    return [SYSTEM_DEFAULT, *installed]


def open_url(url: str, browser: str = SYSTEM_DEFAULT) -> None:
    """
    Launch `url` in `browser` as a separate process.

    Falls back to the OS default for 'system' or any name this module
    doesn't recognize (e.g. a browser choice saved before it was
    uninstalled) — a job posting opening in the wrong browser is a
    minor annoyance; failing to open at all over an unrecognized
    string is not an acceptable tradeoff for a single click.
    """
    if browser == SYSTEM_DEFAULT or browser not in _KNOWN_BROWSERS:
        webbrowser.open(url)
        return
    subprocess.run(["open", "-a", browser, url], check=False)
