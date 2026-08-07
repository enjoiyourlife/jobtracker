"""
browser_launcher tests. Path.exists and subprocess.run are monkeypatched
throughout — these must never actually probe /Applications or launch a
real browser process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobtracker import browser_launcher as bl


class TestAvailableBrowsers:
    def test_system_default_always_first(self, monkeypatch):
        monkeypatch.setattr(Path, "exists", lambda self: False)
        assert bl.available_browsers()[0] == "system"

    def test_only_lists_installed_browsers(self, monkeypatch):
        installed = {"/Applications/Safari.app", "/Applications/Google Chrome.app"}
        monkeypatch.setattr(Path, "exists", lambda self: str(self) in installed)

        result = bl.available_browsers()

        assert "Safari" in result
        assert "Google Chrome" in result
        assert "Firefox" not in result

    def test_none_installed_leaves_only_system(self, monkeypatch):
        monkeypatch.setattr(Path, "exists", lambda self: False)
        assert bl.available_browsers() == ["system"]


class TestOpenUrl:
    def test_system_default_uses_webbrowser(self, monkeypatch):
        calls = []
        monkeypatch.setattr(bl.webbrowser, "open", calls.append)
        monkeypatch.setattr(bl.subprocess, "run", lambda *a, **k: pytest.fail("should not shell out"))

        bl.open_url("https://example.com/job", browser="system")

        assert calls == ["https://example.com/job"]

    def test_named_browser_shells_out_to_open_dash_a(self, monkeypatch):
        calls = []
        monkeypatch.setattr(bl.subprocess, "run", lambda args, **k: calls.append(args))

        bl.open_url("https://example.com/job", browser="Google Chrome")

        assert calls == [["open", "-a", "Google Chrome", "https://example.com/job"]]

    def test_unrecognized_browser_falls_back_to_system_default(self, monkeypatch):
        """A browser choice saved before it was uninstalled shouldn't
        break Apply — it should just fall back rather than error."""
        calls = []
        monkeypatch.setattr(bl.webbrowser, "open", calls.append)

        bl.open_url("https://example.com/job", browser="Netscape Navigator")

        assert calls == ["https://example.com/job"]
