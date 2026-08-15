"""
Entry point for the packaged desktop app — the PyInstaller target.

Distinct from cli.py's main(): that's the full argparse CLI with nine
subcommands, built for someone comfortable in a terminal. A
double-clicked .app has no terminal and no arguments to parse, so this
skips all of that and does the one thing a double-click should do:
seed a starter config on first launch, then open the GUI window.
"""

from __future__ import annotations

from jobtracker import paths
from jobtracker.gui import run


def main() -> int:
    paths.ensure_default_config()
    run(native_window=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
