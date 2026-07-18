"""Session-start context: a compact block of tracked-variable state.

Emitted through the SessionStart hook. Hook output is capped at 10,000
characters by Claude Code, so the block is budgeted well under that.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, duplicates, reconcile, store

MAX_CONTEXT_CHARS = 6000


def _fmt_row(r) -> str:
    loc = f"{r['file']}:{r['repo_line'] or r['line']}"
    scope = f" [{r['scope']}]" if r["scope"] else ""
    val = r["value_preview"] or ""
    return f"- `{r['name']}`{scope} = `{val}` ({loc})"


def build_context(root: Path, limit: int | None = None) -> str:
    cfg = config.load(root)
    limit = limit or cfg["context_limit"]
    if not store.exists(root):
        return ""
    st_obj = store.open_store(root)
    stats = st_obj.stats()
    if stats["total"] == 0:
        return ""
    drifted = st_obj.query(status="drifted", limit=10)
    missing = st_obj.query(status="missing", limit=10)
    deleted = st_obj.query(status="file_deleted", limit=5)
    # rank durable names first: module/class level beats function locals,
    # and one-letter locals are noise in a between-sessions summary
    pool = st_obj.query(status="active", limit=limit * 8)
    pool = [r for r in pool if not (r["scope"] and len(r["name"]) <= 1)]
    pool.sort(key=lambda r: (r["scope"].count(".") + (r["scope"] != ""),))
    recent = pool[:limit]

    lines = [
        "## varmem: variables Claude assigned in previous sessions",
        f"Tracked: {stats['total']} assignments across {stats['files']} files "
        f"({stats['sessions']} sessions). Statuses: "
        + ", ".join(f"{k}={v}" for k, v in sorted(stats["by_status"].items()))
        + ".",
    ]
    if stats["total"] <= 3000:  # keep session start fast on huge registries
        try:
            dups = duplicates.find_duplicates(st_obj)
            if dups:
                by_level = {"high": 0, "medium": 0, "low": 0}
                for d in dups:
                    by_level[d["level"]] += 1
                lines.append(
                    f"Duplicate-variable suspects: {by_level['high']} high, "
                    f"{by_level['medium']} medium, {by_level['low']} low — "
                    "before introducing a new variable for an existing "
                    "concept, check `python varmem.py duplicates`.")
        except Exception:
            pass
    if drifted:
        lines.append("")
        lines.append("### Changed in repo since Claude wrote them (drifted)")
        for r in drifted:
            lines.append(_fmt_row(r))
            lines.append(f"  repo now: `{r['repo_value_preview'] or ''}`")
    if missing:
        lines.append("")
        lines.append("### No longer present in their file (missing)")
        for r in missing:
            when = (r["last_written_at"] or "")[:19]
            lines.append(f"- `{r['name']}` was in {r['file']} "
                         f"(last written {when})")
    if deleted:
        lines.append("")
        lines.append("### Tracked files deleted from repo")
        for r in deleted:
            lines.append(f"- {r['file']} (held `{r['name']}`)")
    if recent:
        lines.append("")
        lines.append(f"### Recently written (module-level first, up to {limit})")
        for r in recent:
            lines.append(_fmt_row(r))
    lines.append("")
    lines.append("Query details: `python varmem.py query <name>` "
                 "(from the varmem checkout) or the .varmem/ files in-repo")
    text = "\n".join(lines)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS - 15] + "\n…(truncated)"
    return text


def session_start_output(payload: dict, explicit_root: str | None = None) -> str:
    """Reconcile (bounded), then return the JSON the SessionStart hook prints."""
    root = config.resolve_project_root(payload, explicit_root)
    cfg = config.load(root)
    try:
        if store.exists(root):
            reconcile.reconcile_project(
                root,
                max_files=cfg["session_start_reconcile_max_files"],
                session_id=payload.get("session_id") or "session-start",
            )
    except Exception:
        pass  # recall still works from last known state
    ctx = build_context(root)
    if not ctx:
        return ""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }
    })
