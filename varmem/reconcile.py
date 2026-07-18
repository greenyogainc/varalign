"""Reconciler: keep the registry matched to what the repo actually holds.

For every tracked file, re-extract assignments and update each row:
  found, same hash   -> active
  found, hash differs-> drifted (repo_value_* records what the repo has now)
  name gone          -> missing
  file gone          -> file_deleted
A file whose mtime predates every row's last_verified_at is skipped unless
force=True, so session-start reconciles stay cheap.
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from . import config, extractors, redact, store


def _mtime_dt(path: Path) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return None


def _parse_iso(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def reconcile_file(st, root: Path, rel: str, cfg: dict,
                   force: bool = False, session_id: str = "reconcile") -> dict:
    out = {"file": rel, "checked": 0, "active": 0, "drifted": 0,
           "missing": 0, "file_deleted": 0, "moved": 0, "skipped": False}
    rows = st.file_rows(rel)
    if not rows:
        return out
    p = root / rel
    if not p.exists():
        for r in rows:
            if r["status"] != "file_deleted":
                st.mark_status(r, "file_deleted")
                st.log_event(session_id, "file_deleted",
                             name=r["name"], file=rel, scope=r["scope"])
        out["file_deleted"] = len(rows)
        return out

    if not force:
        mtime = _mtime_dt(p)
        newest_check = max(
            (d for d in (_parse_iso(r["last_verified_at"]) for r in rows) if d),
            default=None)
        if mtime and newest_check and mtime <= newest_check:
            out["skipped"] = True
            return out

    try:
        if p.stat().st_size > cfg["max_file_kb"] * 1024:
            out["skipped"] = True
            return out
        source = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        out["skipped"] = True
        return out

    _, found = extractors.extract(rel, source)
    by_key = {}
    by_name: dict[str, list[dict]] = {}
    for v in found:
        by_key[(v["scope"], v["name"])] = v
        by_name.setdefault(v["name"], []).append(v)

    for r in rows:
        out["checked"] += 1
        v = by_key.get((r["scope"], r["name"]))
        moved = False
        if v is None:
            candidates = by_name.get(r["name"], [])
            if len(candidates) == 1:  # same name, new scope: treat as moved
                v = candidates[0]
                moved = True
        if v is None:
            if r["status"] not in ("missing", "file_deleted"):
                st.mark_status(r, "missing")
                st.log_event(session_id, "went_missing", name=r["name"],
                             file=rel, scope=r["scope"], detail="reconcile")
            out["missing"] += 1
            continue
        was_drifted = r["status"] == "drifted"
        preview, _ = redact.make_preview(v["value"], cfg["redact"],
                                         cfg["preview_len"])
        vhash = redact.value_hash(v["value"])
        status = st.refresh_repo_state(r, line=v["line"], preview=preview,
                                       vhash=vhash,
                                       claude_hash=r["value_hash"])
        if moved:
            old_scope = r["scope"]
            st.change_scope(r, v["scope"])
            st.log_event(session_id, "moved", name=r["name"], file=rel,
                         scope=v["scope"], detail=f"was scope {old_scope!r}")
            out["moved"] += 1
        if status == "drifted" and not was_drifted:
            st.log_event(session_id, "drifted", name=r["name"],
                         file=rel, scope=r["scope"],
                         detail=f"repo now: {preview[:80]}")
        out[status] += 1
    return out


def reconcile_project(root: Path, force: bool = False,
                      only_files: list[str] | None = None,
                      max_files: int | None = None,
                      session_id: str = "reconcile") -> dict:
    cfg = config.load(root)
    st = store.open_store(root)
    totals = {"files": 0, "checked": 0, "active": 0, "drifted": 0,
              "missing": 0, "file_deleted": 0, "moved": 0, "skipped_files": 0}
    files = only_files or st.tracked_files()
    if max_files is not None and len(files) > max_files:
        files = files[:max_files]
    for rel in files:
        r = reconcile_file(st, root, rel, cfg, force=force,
                           session_id=session_id)
        totals["files"] += 1
        if r["skipped"]:
            totals["skipped_files"] += 1
            continue
        for k in ("checked", "active", "drifted", "missing",
                  "file_deleted", "moved"):
            totals[k] += r[k]
    st.save()
    return totals
