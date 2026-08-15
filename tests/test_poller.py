"""
poller.poll_all tests — orchestration only.

poll_board itself does real network I/O (fetch from a live ATS) and
isn't covered here; the ATS client modules already have their own
fetch/parse tests against committed fixtures. What's tested is the
part poll_all actually adds: gathering targets from config.yaml and
firing on_board_done once per board, which is exactly the logic the
GUI's background poll trigger depends on for live progress.
"""

from __future__ import annotations

from types import SimpleNamespace

from jobtracker import poller
from jobtracker.filters import Criteria


def _fake_criteria(boards: dict[str, tuple[str, ...]]) -> SimpleNamespace:
    return SimpleNamespace(boards=boards)


class TestPollAll:
    def test_gathers_targets_from_every_ats(self, monkeypatch):
        monkeypatch.setattr(
            Criteria, "load",
            staticmethod(lambda path=None: _fake_criteria({
                "greenhouse": ("stripe", "figma"),
                "lever": ("acme",),
            })),
        )
        called: list[tuple[str, str]] = []
        monkeypatch.setattr(poller, "poll_board", lambda conn, ats, slug: called.append((ats, slug)))

        result = poller.poll_all(conn=object())

        assert set(result) == {("greenhouse", "stripe"), ("greenhouse", "figma"), ("lever", "acme")}
        assert set(called) == set(result)

    def test_empty_boards_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(Criteria, "load", staticmethod(lambda path=None: _fake_criteria({})))
        monkeypatch.setattr(poller, "poll_board", lambda *a: (_ for _ in ()).throw(AssertionError("should not be called")))

        assert poller.poll_all(conn=object()) == []

    def test_on_board_done_fires_once_per_board(self, monkeypatch):
        monkeypatch.setattr(
            Criteria, "load",
            staticmethod(lambda path=None: _fake_criteria({"greenhouse": ("stripe", "figma", "notion")})),
        )
        monkeypatch.setattr(poller, "poll_board", lambda conn, ats, slug: None)
        done: list[tuple[str, str]] = []

        poller.poll_all(conn=object(), on_board_done=lambda ats, slug: done.append((ats, slug)))

        assert done == [("greenhouse", "stripe"), ("greenhouse", "figma"), ("greenhouse", "notion")]

    def test_callback_is_optional(self, monkeypatch):
        """The CLI calls poll_all with no callback — must not require one."""
        monkeypatch.setattr(
            Criteria, "load", staticmethod(lambda path=None: _fake_criteria({"greenhouse": ("stripe",)}))
        )
        monkeypatch.setattr(poller, "poll_board", lambda conn, ats, slug: None)

        poller.poll_all(conn=object())  # must not raise
