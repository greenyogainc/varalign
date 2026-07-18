"""varmem — track variable assignments written by AI agents across sessions.

Capture:   PostToolUse hook on Write/Edit -> extract assignments -> registry.
Reconcile: re-scan tracked files, detect drift/removal vs what the agent wrote.
Recall:    SessionStart hook injects a compact summary into the new session.
"""

__version__ = "1.0.0"


def build_info() -> dict:
    """Engine version plus the source commit it was BUILT from.

    scripts/bundle-engine.js stamps `_build.py` into the *packaged* engine at
    package time, so a bundled copy reports the exact source it came from;
    running straight from source (no `_build.py`) reports ``"source"``. The
    extension surfaces this, so a stale bundled engine is visible instead of
    being silently served — the Bug 4 failure mode (dogfood 2026-07-16).
    """
    build = "source"
    try:
        from ._build import BUILD_SHA  # written by bundle-engine.js at package time
        build = str(BUILD_SHA)
    except Exception:
        pass
    return {"version": __version__, "build": build}
