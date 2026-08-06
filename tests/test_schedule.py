"""
Scheduling tests.

subprocess.run is monkeypatched throughout — these tests must never
touch the real crontab, only jobtracker's own logic for building and
finding its entry within it.
"""

from __future__ import annotations

import subprocess

import pytest

from jobtracker import schedule


class _FakeCrontab:
    """
    Stand-in for the real `crontab` binary.

    Tracks state in memory and answers `crontab -l` / `crontab -` the
    same way the real command does, including "no crontab yet" as a
    non-zero exit rather than an error.
    """

    def __init__(self, initial_lines: list[str] | None = None) -> None:
        self.lines = list(initial_lines or [])
        self.has_crontab = initial_lines is not None

    def run(self, args, capture_output=True, text=True, input=None):
        if args == ["crontab", "-l"]:
            if not self.has_crontab:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="no crontab")
            return subprocess.CompletedProcess(args, 0, stdout="\n".join(self.lines) + "\n", stderr="")
        if args == ["crontab", "-"]:
            self.lines = [l for l in (input or "").splitlines() if l.strip()]
            self.has_crontab = True
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected crontab invocation: {args}")


@pytest.fixture
def fake_cron(monkeypatch):
    fake = _FakeCrontab()
    monkeypatch.setattr(schedule.subprocess, "run", fake.run)
    return fake


class TestInstall:
    def test_adds_entry_to_empty_crontab(self, fake_cron):
        schedule.install(hour=7, minute=0)
        assert len(fake_cron.lines) == 1
        assert "jobtracker.poller" in fake_cron.lines[0]
        assert fake_cron.lines[0].startswith("0 7 ")

    def test_preserves_unrelated_existing_entries(self, fake_cron):
        fake_cron.lines = ["0 3 * * * /usr/bin/backup.sh"]
        fake_cron.has_crontab = True

        schedule.install(hour=7, minute=30)

        assert "0 3 * * * /usr/bin/backup.sh" in fake_cron.lines
        assert any("jobtracker.poller" in l for l in fake_cron.lines)

    def test_reinstalling_replaces_rather_than_duplicates(self, fake_cron):
        schedule.install(hour=7, minute=0)
        schedule.install(hour=9, minute=15)

        jobtracker_lines = [l for l in fake_cron.lines if "jobtracker.poller" in l]
        assert len(jobtracker_lines) == 1
        assert jobtracker_lines[0].startswith("15 9 ")

    @pytest.mark.parametrize("hour,minute", [(-1, 0), (24, 0), (0, 60), (0, -1)])
    def test_rejects_invalid_time(self, fake_cron, hour, minute):
        with pytest.raises(ValueError):
            schedule.install(hour=hour, minute=minute)


class TestStatus:
    def test_none_when_not_installed(self, fake_cron):
        assert schedule.status() is None

    def test_returns_the_installed_line(self, fake_cron):
        schedule.install(hour=7, minute=0)
        assert "jobtracker.poller" in schedule.status()


class TestUninstall:
    def test_removes_installed_entry(self, fake_cron):
        schedule.install(hour=7, minute=0)
        removed = schedule.uninstall()

        assert removed is True
        assert schedule.status() is None

    def test_returns_false_when_nothing_to_remove(self, fake_cron):
        assert schedule.uninstall() is False

    def test_leaves_unrelated_entries_alone(self, fake_cron):
        fake_cron.lines = ["0 3 * * * /usr/bin/backup.sh"]
        fake_cron.has_crontab = True
        schedule.install(hour=7, minute=0)

        schedule.uninstall()

        assert fake_cron.lines == ["0 3 * * * /usr/bin/backup.sh"]
