"""
Interactive queue.

apply_selected() and skip_selected() are the only parts of this module
that carry logic — plain functions over (conn, entry) with no terminal
dependency, tested the same way the rest of the persistence layer is.
The curses event loop below them owns rendering and keypresses only;
it is exercised by running the program, not by pytest, for the same
reason poller.py's orchestration isn't unit-tested while jobs.py's
upsert logic is: a keypress loop has no meaningful assertion to make
against it, only behavior to observe.

No queue_snapshot indirection here, unlike `apply <position>` — the
whole point of this command is that you never type a number. Selection
is a cursor over the in-memory list `browse()` was handed, and applying
or skipping removes that entry immediately so the list on screen always
matches what's still undecided.
"""

from __future__ import annotations

import curses
import sqlite3

from jobtracker import browser_launcher
from jobtracker.config_editor import load_editable
from jobtracker.db import applications as apps

_HELP = "↑/↓ or j/k move · a apply · s skip · q quit"


def apply_selected(conn: sqlite3.Connection, entry: apps.QueueEntry) -> None:
    """Record the decision and open the posting — same effect as `apply <position>`."""
    apps.add(conn, entry.job_id, score=entry.score)
    conn.commit()
    browser_launcher.open_url(entry.url, browser=load_editable().browser)


def skip_selected(conn: sqlite3.Connection, entry: apps.QueueEntry) -> None:
    """Record a 'skipped' decision so the posting stops resurfacing in the ranked queue."""
    apps.add(conn, entry.job_id, status="skipped")
    conn.commit()


def _draw(
    stdscr, entries: list[apps.QueueEntry], cursor: int, applied: int, skipped: int
) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    header = f"{len(entries)} remaining · applied {applied} · skipped {skipped}"
    stdscr.addstr(0, 0, header[: width - 1], curses.A_BOLD)
    stdscr.addstr(1, 0, _HELP[: width - 1])

    visible_rows = max(0, height - 4)
    # Keep the cursor roughly centered rather than always at the top,
    # so applying/skipping through a long list doesn't leave you
    # staring at row 1 while your actual position scrolls off screen.
    top = max(0, min(cursor - visible_rows // 2, max(0, len(entries) - visible_rows)))

    for i, entry in enumerate(entries[top : top + visible_rows]):
        row_idx = top + i
        y = i + 3
        location = entry.location or "—"
        line = f"[{entry.score:>3}] {entry.title}  ·  {entry.company}  ·  {location}"
        attr = curses.A_REVERSE if row_idx == cursor else curses.A_NORMAL
        stdscr.addstr(y, 0, line[: width - 1], attr)

    stdscr.refresh()


def _run(stdscr, conn: sqlite3.Connection, entries: list[apps.QueueEntry]) -> tuple[int, int]:
    curses.curs_set(0)
    cursor = 0
    applied = skipped = 0

    while entries:
        cursor = max(0, min(cursor, len(entries) - 1))
        _draw(stdscr, entries, cursor, applied, skipped)
        key = stdscr.getch()

        if key in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = min(len(entries) - 1, cursor + 1)
        elif key == ord("a"):
            apply_selected(conn, entries[cursor])
            applied += 1
            entries.pop(cursor)
        elif key == ord("s"):
            skip_selected(conn, entries[cursor])
            skipped += 1
            entries.pop(cursor)
        elif key == ord("q"):
            break

    return applied, skipped


def browse(conn: sqlite3.Connection, entries: list[apps.QueueEntry]) -> tuple[int, int]:
    """
    Run the interactive queue over `entries`, returning (applied, skipped).

    Wraps curses.wrapper so the terminal is always restored to a normal
    state on exit — including on an uncaught exception, which curses
    otherwise leaves the terminal garbled after.
    """
    if not entries:
        return 0, 0
    return curses.wrapper(_run, conn, entries)
