"""PostToolUse capture: hook payload in, registry rows out.

Attribution rule (which assignments count as "Claude wrote this"):
  Write  -> every assignment in the file (Claude authored the whole content)
  Edit   -> assignments whose name appears as a token in new_string
  (MultiEdit was removed from Claude Code; handled anyway for old versions
   by unioning edits[].new_string tokens)

Non-attributed assignments in the same file are NOT claimed, but rows already
tracked get their repo state refreshed for free while we're parsing the file.
A hook must never break the session: every failure is swallowed and logged.
"""
from __future__ import annotations

import os
import re
import traceback
from pathlib import Path

from . import config, extractors, redact, store

SUPPORTED_TOOLS = {"Write", "Edit", "MultiEdit"}

_TOKEN = re.compile(r"[A-Za-z_$][\w$]*(?:\.[\w$]+)*")


def _log(root: Path, msg: str):
    try:
        p = config.log_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{store.now_iso()} {msg}\n")
    except Exception:
        pass


def _tokens(text: str) -> set[str]:
    toks: set[str] = set()
    for m in _TOKEN.finditer(text or ""):
        dotted = m.group(0)
        toks.add(dotted)
        toks.update(dotted.split("."))
    return toks


def _attribution_tokens(tool: str, tool_input: dict) -> set[str] | None:
    """None means 'attribute everything' (full-file Write)."""
    if tool == "Write":
        return None
    if tool == "Edit":
        return _tokens(tool_input.get("new_string", ""))
    if tool == "MultiEdit":
        toks: set[str] = set()
        for e in tool_input.get("edits", []) or []:
            toks |= _tokens(e.get("new_string", ""))
        return toks
    return set()


def _rel_path(file_path: str, root: Path) -> str | None:
    try:
        rel = Path(file_path).resolve().relative_to(root)
    except (ValueError, OSError):
        return None
    return rel.as_posix()


def _excluded(rel: str, cfg: dict) -> bool:
    parts = set(rel.split("/")[:-1])
    return bool(parts & set(cfg["exclude_dirs"]))


def process_payload(payload: dict, explicit_root: str | None = None,
                    origin: str = "claude") -> dict:
    """Returns a summary dict; never raises. `origin` tags attributed rows
    ('claude' for the Claude hook, 'kilo' for the Kilo plugin)."""
    summary = {"handled": False, "added": 0, "updated": 0, "refreshed": 0,
               "removed": 0, "file": None, "reason": ""}
    root = config.resolve_project_root(payload, explicit_root)
    summary["root"] = str(root)  # let kilo's caller reuse it without re-resolving
    try:
        tool = payload.get("tool_name", "")
        if tool not in SUPPORTED_TOOLS:
            summary["reason"] = f"tool {tool!r} not captured"
            return summary
        tool_input = payload.get("tool_input") or {}
        file_path = tool_input.get("file_path") or ""
        if not file_path:
            summary["reason"] = "no file_path"
            return summary

        cfg = config.load(root)
        rel = _rel_path(file_path, root)
        if rel is None:
            summary["reason"] = "outside project root"
            return summary
        if _excluded(rel, cfg):
            summary["reason"] = "excluded dir"
            return summary
        lang = extractors.lang_for_path(rel)
        if lang is None:
            summary["reason"] = "unsupported language"
            return summary

        p = Path(file_path)
        try:
            if p.stat().st_size > cfg["max_file_kb"] * 1024:
                summary["reason"] = "file too large"
                return summary
            source = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # deleted between tool call and hook, or unreadable: fall back to
            # the content Claude sent, when the tool gave us the whole file
            source = tool_input.get("content")
            if source is None:
                summary["reason"] = "unreadable file"
                return summary

        _, found = extractors.extract(rel, source)
        session_id = payload.get("session_id") or "unknown"
        attrib = _attribution_tokens(tool, tool_input)

        # Hold the repo lock across the whole read-modify-write so a concurrent
        # capture (another editor/hook) on the same file can't lost-update the
        # shard between our load and save.
        with store.file_lock(root):
            st = store.open_store(root)
            prior = {(r["scope"], r["name"]): r for r in st.file_rows(rel)}
            current_keys = set()
            for v in found:
                key = (v["scope"], v["name"])
                current_keys.add(key)
                preview, was_red = redact.make_preview(
                    v["value"], cfg["redact"], cfg["preview_len"])
                vhash = redact.value_hash(v["value"])
                claude_wrote = attrib is None or v["name"] in attrib
                if claude_wrote:
                    action = st.upsert_write(
                        file=rel, scope=v["scope"], name=v["name"],
                        lang=lang, kind=v["kind"], line=v["line"],
                        preview=preview, vhash=vhash, redacted=was_red,
                        session_id=session_id, origin=origin,
                        anno=v.get("anno"))
                    summary[action] += 1
                    st.log_event(session_id, action, name=v["name"],
                                 file=rel, scope=v["scope"],
                                 detail=f"{tool} line {v['line']}")
                elif key in prior:
                    st.refresh_repo_state(
                        prior[key], line=v["line"], preview=preview,
                        vhash=vhash, claude_hash=prior[key]["value_hash"])
                    summary["refreshed"] += 1
            for key, row in prior.items():
                if key not in current_keys and row["status"] not in (
                        "missing", "file_deleted"):
                    st.mark_status(row, "missing")
                    st.log_event(session_id, "went_missing",
                                 name=row["name"], file=rel, scope=row["scope"],
                                 detail=f"absent after {tool}")
                    summary["removed"] += 1
            st.save()

        summary["handled"] = True
        summary["file"] = rel
        return summary
    except Exception:
        _log(root, "capture error:\n" + traceback.format_exc())
        summary["reason"] = "internal error (see varmem.log)"
        return summary


KILO_SESSION_PREFIX = "kilo:"


def process_kilo_payload(payload: dict,
                         explicit_root: str | None = None) -> dict:
    """Kilo plugin entrypoint. Same payload shape as the Claude hook, but the
    session id is namespaced `kilo:<id>` and rows are tagged origin='kilo' so
    the UI can tell which agent introduced a variable. Never raises."""
    # str() so a non-string session_id from a malformed payload can't blow up
    # .startswith — this entrypoint must never raise (see cmd_capture_kilo).
    sid = str(payload.get("session_id") or "unknown")
    if not sid.startswith(KILO_SESSION_PREFIX):
        payload = {**payload, "session_id": KILO_SESSION_PREFIX + sid}
    summary = process_payload(payload, explicit_root, origin="kilo")
    # A supported tool that mapped to no usable file is the signature of a Kilo
    # arg-schema drift (the plugin's arg-name / cwd assumptions). Surface it in
    # the log so the capture path degrades loudly, not silently.
    if not summary["handled"] and summary["reason"] in (
            "no file_path", "outside project root"):
        try:
            _log(Path(summary["root"]),
                 f"kilo capture skipped: {summary['reason']} "
                 f"(tool={payload.get('tool_name')!r}, "
                 f"cwd={payload.get('cwd')!r})")
        except Exception:
            pass
    return summary
