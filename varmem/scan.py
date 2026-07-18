"""Whole-repo baseline scan.

Hooks only see writes made after installation; `varmem scan` walks the repo
and snapshots every supported file so duplicate detection and drift tracking
work on pre-existing code from day one. Scan rows carry origin='scan' and
never steal provenance from agent-origin rows (claude/kilo — those just get
their repo state refreshed).
"""
from __future__ import annotations

import os
from pathlib import Path

from . import config, extractors, redact, store


def _gitignore_dirs(root: Path) -> tuple[set[str], set[str]]:
    """Lightweight .gitignore support for directory pruning: bare names
    (node_modules) and anchored paths (extension/engine). Globs, negations, and
    file-only patterns are deliberately skipped — this prunes directories so the
    scan never walks committed-but-ignored build output."""
    names: set[str] = set()
    paths: set[str] = set()
    try:
        lines = (root / ".gitignore").read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return names, paths
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#") or ln.startswith("!") or "*" in ln:
            continue
        ln = ln.strip("/")
        if not ln:
            continue
        (paths if "/" in ln else names).add(ln)
    return names, paths


def iter_supported_files(root: Path, cfg: dict):
    exclude = set(cfg["exclude_dirs"])
    gi_names, gi_paths = _gitignore_dirs(root)
    exclude |= gi_names
    max_bytes = cfg["max_file_kb"] * 1024
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        base = "" if rel_dir in ("", ".") else rel_dir + "/"
        dirnames[:] = [d for d in dirnames
                       if d not in exclude and (base + d) not in gi_paths]
        for fn in filenames:
            p = Path(dirpath) / fn
            if extractors.lang_for_path(fn) is None:
                continue
            try:
                if p.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            yield p


def scan_project(root: Path, progress=None) -> dict:
    cfg = config.load(root)
    st = store.open_store(root)
    totals = {"files": 0, "added": 0, "updated": 0, "refreshed": 0,
              "missing": 0, "pruned": 0, "vars": 0}
    visited: set[str] = set()
    for p in iter_supported_files(root, cfg):
        rel = p.relative_to(root).as_posix()
        visited.add(rel)
        try:
            source = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lang, found = extractors.extract(rel, source)
        totals["files"] += 1
        if progress and totals["files"] % 100 == 0:
            progress(totals["files"])
        prior = {(r["scope"], r["name"]): r for r in st.file_rows(rel)}
        current = set()
        for v in found:
            key = (v["scope"], v["name"])
            current.add(key)
            totals["vars"] += 1
            preview, was_red = redact.make_preview(
                v["value"], cfg["redact"], cfg["preview_len"])
            vhash = redact.value_hash(v["value"])
            existing = prior.get(key)
            if existing is not None and existing["origin"] != "scan":
                st.refresh_repo_state(existing, line=v["line"],
                                      preview=preview, vhash=vhash,
                                      claude_hash=existing["value_hash"])
                totals["refreshed"] += 1
            else:
                action = st.upsert_write(
                    file=rel, scope=v["scope"], name=v["name"],
                    lang=lang, kind=v["kind"], line=v["line"],
                    preview=preview, vhash=vhash, redacted=was_red,
                    session_id="scan", origin="scan", anno=v.get("anno"))
                totals[action] += 1
        for key, row in prior.items():
            if key in current:
                continue
            if row["origin"] == "scan":
                # baseline rows are a repo snapshot, not agent memory:
                # gone from the repo means gone from the baseline
                st.delete_row(row)
                totals["pruned"] += 1
            elif row["status"] not in ("missing", "file_deleted"):
                st.mark_status(row, "missing")
                totals["missing"] += 1
    # Auto-hygiene: baseline (scan-origin) rows for files no longer in scan
    # scope — now gitignored, excluded, or deleted — are stale snapshots with
    # zero memory value. Prune them so the report never accumulates
    # unactionable file_deleted/missing entries the user must wade through
    # (dogfood 2026-07-17: 672 orphaned rows from a gitignored bundled-engine
    # build artifact were 82% of the drift report). Agent-origin rows are left
    # alone here — those are cross-session memory, surfaced only as recall.
    for row in list(st.all_rows()):
        if row["origin"] == "scan" and row["file"] not in visited:
            st.delete_row(row)
            totals["pruned"] += 1

    st.log_event("scan", "scan_completed",
                 detail=f"{totals['files']} files, {totals['vars']} vars")
    st.save()
    return totals
