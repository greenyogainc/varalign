"""Configuration defaults and loading for varmem.

Per-project overrides live at <project>/.claude/varmem/config.json and are
shallow-merged over DEFAULTS.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# v1.2: the registry lives at the repo root as committable plain files so
# every tool (Claude Code, Kilo, VS Code extension, CI, humans) shares it
VARMEM_DIRNAME = ".varmem"
LEGACY_DIRNAME = os.path.join(".claude", "varmem")
LOG_FILENAME = "varmem.log"

DEFAULTS: dict = {
    # Redact secret-looking right-hand sides in stored previews (hashes are
    # always kept, so drift detection works either way).
    "redact": True,
    # Skip files larger than this (KB) — generated bundles, lockfiles, etc.
    "max_file_kb": 1024,
    # Path segments that are never captured or reconciled.
    "exclude_dirs": [
        ".git", "node_modules", "dist", "build", "out", "target",
        ".venv", "venv", "__pycache__", "vendor", ".next", ".claude",
        ".varmem", ".kilo",
    ],
    # How many variables the SessionStart context block lists.
    "context_limit": 25,
    # Reconcile at session start only if the project tracks fewer files
    # than this (keeps the hook fast on big registries).
    "session_start_reconcile_max_files": 200,
    # Truncation length for stored value previews.
    "preview_len": 120,
    # HTML report embeds at most this many duplicate pairs (top by score);
    # full level counts are always shown.
    "report_max_pairs": 400,
    # Learn family-suppression rules from `not_duplicate` verdicts: dismissing
    # one member of a naming family (ISSUES_PAGE_SIZE / VARS_PAGE_SIZE) quiets
    # the rest, and future members, of that family. Set false for per-pair-only.
    "learn_from_reviews": True,
    # Extra always-ignored names for duplicate pairing (adds to built-ins).
    "dup_ignore_names": [],
    # Names that legitimately recur across standalone entry-point scripts;
    # cross-file pairs of these are never flagged (compared case-insensitively
    # ignoring separators). User-reviewed defaults, 2026-07-15.
    "dup_ignore_crossfile_names": [
        "ENVFILE", "ENV_FILE", "DSN", "STATE", "TIMEOUT",
        "SRC", "DST", "OUT", "FINAL", "LOG", "TMP",
    ],
    # Isolated-subtree ("standalone unit") path patterns. Repos whose units are
    # independently deployable and CANNOT share code (per-handler Lambdas,
    # micro-service dirs, vendored sub-apps) legitimately copy the same
    # constants into every unit — a cross-unit same-name/same-value or
    # value-mismatch is expected duplication, not drift, because you can't
    # dedupe them into one module. When set, a duplicate pair whose two sides
    # live in DIFFERENT declared units is never flagged; WITHIN-unit dedup and
    # pairs outside any unit are unaffected. A '*' matches exactly one path
    # segment and becomes part of the unit identity: "lambda/*" makes each
    # child of lambda/ its own unit; "*" makes each top-level directory a unit.
    # Empty by default (feature off). (dogfood 2026-07-17, a production
    # monorepo: 1,118 copy-paste constants + 2,353 tuning knobs across
    # standalone units.)
    "standalone_units": [],
    # When standalone_units is empty, auto-detect a multi-component layout (>=2
    # top-level directories that each carry a deploy/package manifest — a
    # Cloudflare worker + a VS Code extension + a signing lib, say) and treat
    # each top-level directory as its own unit, so cross-runtime constant copies
    # are not flagged. Set false to disable the heuristic. (dogfood 2026-07-17.)
    "auto_standalone_units": True,
}


def varmem_dir(project_root: str | Path) -> Path:
    return Path(project_root) / VARMEM_DIRNAME


def log_path(project_root: str | Path) -> Path:
    return varmem_dir(project_root) / LOG_FILENAME


def load(project_root: str | Path) -> dict:
    cfg = dict(DEFAULTS)
    for candidate in (varmem_dir(project_root) / "config.json",
                      Path(project_root) / LEGACY_DIRNAME / "config.json"):
        try:
            if candidate.exists():
                with open(candidate, "r", encoding="utf-8") as f:
                    user = json.load(f)
                if isinstance(user, dict):
                    cfg.update(user)
                break
        except Exception:
            pass  # bad config must never break a hook
    return cfg


def add_ignored_names(project_root: str | Path, names: list[str]) -> list[str]:
    """Persist names into the project config's dup_ignore_names (deduped).
    Returns the resulting list. Writes <root>/.varmem/config.json, preserving
    any other user overrides in it."""
    path = varmem_dir(project_root) / "config.json"
    user: dict = {}
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                user = loaded
    except Exception:
        pass  # unreadable config: rewrite with just our key rather than crash
    current = list(user.get("dup_ignore_names") or [])
    for n in names:
        n = str(n).strip()
        if n and n not in current:
            current.append(n)
    user["dup_ignore_names"] = current
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(user, f, indent=2)
    return current


def resolve_project_root(payload: dict | None = None, explicit: str | None = None) -> Path:
    """Resolution order: --project flag > hook payload cwd > CLAUDE_PROJECT_DIR > cwd."""
    if explicit:
        return Path(explicit).resolve()
    if payload and payload.get("cwd"):
        return Path(payload["cwd"]).resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()
