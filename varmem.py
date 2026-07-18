#!/usr/bin/env python3
"""Launcher so hooks can run varmem without packaging/installation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows consoles/pipes default to cp1252, which cannot encode characters the
# reports and prompts legitimately use (arrows, dashes). Force UTF-8 so no
# command ever dies in print() — callers (the VS Code extension, hooks, CI)
# all read UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # non-reconfigurable stream (tests capturing IO); already safe

from varmem.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
