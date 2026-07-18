"""File-backed registry living INSIDE the tracked repo at <repo>/.varmem/.

Why files, not SQLite (v1.2 decision): the registry is shared state BETWEEN
tools — Claude Code hooks, Kilo plugins, the VS Code extension, CI, humans.
Plain JSON files are readable by all of them with zero drivers, committable
so state travels with the repo, and diffable/mergeable in git.

Layout:
    .varmem/
      vars/<shard>.jsonl   one shard per source file, one variable per line,
                           sorted by (scope, name) -> minimal git diffs
      reviews.json         duplicate-pair verdicts + notes (human curation)
      events.jsonl         append-only audit trail
      meta.json            schema version
      config.json          optional per-repo overrides
      varmem.log           local-only (listed in .varmem/.gitignore)
      report.html          generated artifact (local-only)

Concurrency: parallel hooks touch different source files -> different shards;
shard writes are atomic (tmp + os.replace). events.jsonl uses O_APPEND. Two
captures on the SAME file would still lost-update its shard, so the capture
path serializes the read-modify-write with a cross-process `file_lock` (below).

A legacy SQLite registry (.claude/varmem/varmem.db, v1.x) is migrated
automatically on first open; the old file is left in place untouched.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from . import config

SCHEMA_VERSION = "3"

# Cross-process advisory lock so two capture processes (e.g. a Claude hook and
# a Kilo plugin, or two editor windows) editing the SAME repo don't lost-update
# a shard. The OS drops the lock when the holder exits, so a crash can't
# deadlock; on any error or timeout callers proceed unlocked (a rare lost
# update beats a hung capture hook). The lock file lives in the temp dir keyed
# by the canonical repo path, never inside the committed .varmem/.
try:
    import fcntl

    def _os_lock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _os_unlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
except ImportError:  # Windows
    import msvcrt

    def _os_lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _os_unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def _lock_path(root: str | Path) -> Path:
    key = hashlib.sha1(  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
        str(Path(root).resolve()).encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / "varmem-locks" / f"{key}.lock"


@contextlib.contextmanager
def file_lock(root: str | Path, timeout: float = 5.0):
    """Best-effort cross-process lock serializing a repo's store read-modify-
    write. Never raises: any locking failure just yields unlocked."""
    f = None
    locked = False
    try:
        p = _lock_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        f = open(p, "a+")
        deadline = time.monotonic() + timeout
        while True:
            try:
                _os_lock(f)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break  # give up waiting; proceed unlocked
                time.sleep(0.05)
    except Exception:
        pass
    try:
        yield
    finally:
        if f is not None:
            if locked:
                try:
                    _os_unlock(f)
                except Exception:
                    pass
            try:
                f.close()
            except Exception:
                pass

# The generated .varmem/.gitignore. Only the human-curated files (review
# verdicts + detection config) are committed so they travel with the repo; the
# rest of the registry is machine-generated (rebuilt by `varmem scan`) and
# churns on every scan, so it is ignored to keep diffs clean. This means users
# never have to hand-add a `.varmem/*` rule to their repo-root .gitignore — a
# rule that would silently drop reviews.json (the dismissal ledger) too.
_GITIGNORE = (
    "# varmem registry. Only the human-curated files un-ignored below are\n"
    "# committed; everything else is machine-generated (rebuilt by `varmem\n"
    "# scan`) and left out of git to keep diffs clean.\n"
    "*\n"
    "!.gitignore\n"
    "!reviews.json\n"
    "!config.json\n"
)
# The prior default — recognised so an unmodified one migrates in place while a
# user-customised .gitignore is never touched.
_LEGACY_GITIGNORE = "varmem.log\nreport.html\n*.tmp\n"

_ROW_FIELDS = [
    "name", "lang", "file", "scope", "kind", "line",
    "value_preview", "value_hash", "redacted", "anno",
    "repo_value_preview", "repo_value_hash", "repo_line",
    "status", "first_session", "last_session",
    "first_seen_at", "last_written_at", "last_verified_at",
    "origin", "note",
]


def now_iso() -> str:
    # microsecond precision: the reconcile mtime fast-path compares file
    # mtimes against these strings
    return _dt.datetime.now().astimezone().isoformat(timespec="microseconds")


def row_id(file: str, scope: str, name: str) -> str:
    return f"{file}|{scope}|{name}"


def normalize_pair_key(key: str) -> str:
    """Canonicalize a duplicate-pair key so a verdict recorded under any
    reasonable representation still matches: backslash vs forward-slash paths,
    swapped A/B order, and stray whitespace all fold to one form. The canonical
    shape is `file|scope|name||file|scope|name` — which splits on `|` into
    exactly 7 fields with an empty field at index 3 (the `||` side boundary),
    so we can re-sort the two sides even when a scope is empty. Anything that
    doesn't fit that shape is still slash+whitespace normalized, never raising
    (dogfood 2026-07-17, a production repo: an agent with no CLI hand-wrote
    reviews.json and guessed the order/separators — normalization makes that
    guess harmless)."""
    if not isinstance(key, str):
        return key
    k = key.replace("\\", "/")
    parts = k.split("|")
    if len(parts) == 7 and parts[3] == "":
        a = "|".join(p.strip() for p in parts[0:3])
        b = "|".join(p.strip() for p in parts[4:7])
        return "||".join(sorted((a, b)))
    return k.strip()


def pair_key(a, b) -> str:
    """Stable, canonical semantic key for a duplicate pair (survives store
    rebuilds and matches a verdict recorded under any equivalent form)."""
    sides = sorted(f"{r['file']}|{r['scope']}|{r['name']}" for r in (a, b))
    return normalize_pair_key("||".join(sides))


def _shard_name(rel_file: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "__", rel_file)[:80]
    # non-cryptographic shard-filename disambiguator, not a signature
    digest = hashlib.sha1(  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
        rel_file.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{safe}--{digest}.jsonl"


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.dir = self.root / config.VARMEM_DIRNAME
        self.vars_dir = self.dir / "vars"
        self._rows: dict[tuple[str, str, str], dict] = {}
        self._reviews: dict[str, dict] = {}
        self._dirty_files: set[str] = set()
        self._reviews_dirty = False
        self._load()

    # ------------------------------------------------------------- loading

    def _load(self):
        self.vars_dir.mkdir(parents=True, exist_ok=True)
        gi = self.dir / ".gitignore"
        try:
            cur = gi.read_text(encoding="utf-8") if gi.exists() else None
        except Exception:
            cur = None
        # write when absent, or migrate an unmodified legacy default in place;
        # a hand-edited .gitignore is left alone.
        if cur is None or cur == _LEGACY_GITIGNORE:
            gi.write_text(_GITIGNORE, encoding="utf-8")
        meta_p = self.dir / "meta.json"
        if not meta_p.exists():
            self._maybe_migrate_legacy()
            meta_p.write_text(
                json.dumps({"schema_version": SCHEMA_VERSION}) + "\n",
                encoding="utf-8")
        for shard in self.vars_dir.glob("*.jsonl"):
            try:
                with open(shard, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        r = json.loads(line)
                        self._index(r)
            except (OSError, json.JSONDecodeError):
                continue  # a corrupt shard loses one file's rows, not the store
        rp = self.dir / "reviews.json"
        if rp.exists():
            try:
                raw = json.loads(rp.read_text(encoding="utf-8"))
                # normalize keys on load so a hand-written verdict (swapped A/B
                # order, backslash paths, stray whitespace) matches the tool's
                # canonical key, and self-heals to canonical on the next save
                self._reviews = {normalize_pair_key(k): v
                                 for k, v in raw.items()}
            except (OSError, json.JSONDecodeError, AttributeError):
                self._reviews = {}

    def _index(self, r: dict):
        for k in _ROW_FIELDS:
            r.setdefault(k, None)
        r["id"] = row_id(r["file"], r["scope"], r["name"])
        self._rows[(r["file"], r["scope"], r["name"])] = r

    def _maybe_migrate_legacy(self):
        legacy = self.root / ".claude" / "varmem" / "varmem.db"
        if not legacy.exists():
            return
        try:
            import sqlite3
            conn = sqlite3.connect(legacy)
            conn.row_factory = sqlite3.Row
            for r in conn.execute("SELECT * FROM variables"):
                d = {k: r[k] for k in r.keys() if k in _ROW_FIELDS}
                self._index(d)
                self._dirty_files.add(d["file"])
            try:
                for r in conn.execute("SELECT * FROM dup_reviews"):
                    self._reviews[r["pair_key"]] = {
                        "verdict": r["verdict"], "note": r["note"],
                        "ts": r["ts"]}
                self._reviews_dirty = True
            except sqlite3.OperationalError:
                pass
            conn.close()
            self.log_event("migrate", "migrated_from_sqlite",
                           detail=f"{len(self._rows)} rows")
            self.save()
        except Exception:
            pass  # start empty rather than break a hook

    # -------------------------------------------------------------- saving

    def save(self):
        for file in self._dirty_files:
            rows = sorted(
                (r for r in self._rows.values() if r["file"] == file),
                key=lambda r: (r["scope"], r["name"]))
            shard = self.vars_dir / _shard_name(file)
            if not rows:
                try:
                    shard.unlink()
                except OSError:
                    pass
                continue
            tmp = shard.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                for r in rows:
                    out = {k: r[k] for k in _ROW_FIELDS}
                    f.write(json.dumps(out, sort_keys=True,
                                       ensure_ascii=False) + "\n")
            os.replace(tmp, shard)
        self._dirty_files.clear()
        if self._reviews_dirty:
            tmp = self.dir / "reviews.json.tmp"
            tmp.write_text(
                json.dumps(self._reviews, indent=1, sort_keys=True,
                           ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.dir / "reviews.json")
            self._reviews_dirty = False

    # -------------------------------------------------------------- events

    def log_event(self, session_id, action, name=None, file=None,
                  scope=None, detail=None):
        rec = {"ts": now_iso(), "session_id": session_id, "action": action,
               "name": name, "file": file, "scope": scope, "detail": detail}
        try:
            with open(self.dir / "events.jsonl", "a", encoding="utf-8",
                      newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def events(self) -> list[dict]:
        p = self.dir / "events.jsonl"
        out = []
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    # ------------------------------------------------------------ mutation

    def upsert_write(self, *, file: str, scope: str, name: str, lang: str,
                     kind: str, line: int, preview: str, vhash: str,
                     redacted: bool, session_id: str,
                     origin: str = "claude", anno: str | None = None) -> str:
        ts = now_iso()
        key = (file, scope, name)
        r = self._rows.get(key)
        self._dirty_files.add(file)
        if r is None:
            self._index({
                "name": name, "lang": lang, "file": file, "scope": scope,
                "kind": kind, "line": line, "value_preview": preview,
                "value_hash": vhash, "redacted": int(redacted), "anno": anno,
                "repo_value_preview": preview, "repo_value_hash": vhash,
                "repo_line": line, "status": "active",
                "first_session": session_id, "last_session": session_id,
                "first_seen_at": ts, "last_written_at": ts,
                "last_verified_at": ts, "origin": origin, "note": None,
            })
            return "added"
        r.update(lang=lang, kind=kind, line=line, value_preview=preview,
                 value_hash=vhash, redacted=int(redacted), anno=anno,
                 repo_value_preview=preview, repo_value_hash=vhash,
                 repo_line=line, status="active", last_session=session_id,
                 last_written_at=ts, last_verified_at=ts)
        if origin != "scan":  # an agent write claims a scan row, not reverse
            r["origin"] = origin
        return "updated"

    def refresh_repo_state(self, r: dict, *, line: int, preview: str,
                           vhash: str, claude_hash: str | None) -> str:
        status = "active" if (claude_hash and vhash == claude_hash) \
            else "drifted"
        r.update(repo_value_preview=preview, repo_value_hash=vhash,
                 repo_line=line, line=line if line else r["line"],
                 status=status, last_verified_at=now_iso())
        self._dirty_files.add(r["file"])
        return status

    def mark_status(self, r: dict, status: str):
        r["status"] = status
        r["last_verified_at"] = now_iso()
        self._dirty_files.add(r["file"])

    def accept_drift(self, r: dict) -> bool:
        """Re-baseline a drifted variable: adopt the current repo value as the
        accepted Claude value and mark it active. Resolves an intentional
        change (a completed refactor) without blinding future detection — it
        drifts again if the value changes later. Returns False if the row is
        not drifted. Only the tool touches the tracking shard (ground rule 4)."""
        if r.get("status") != "drifted":
            return False
        r.update(value_preview=r.get("repo_value_preview"),
                 value_hash=r.get("repo_value_hash"),
                 status="active", last_verified_at=now_iso())
        self._dirty_files.add(r["file"])
        return True

    def delete_row(self, r: dict):
        self._rows.pop((r["file"], r["scope"], r["name"]), None)
        self._dirty_files.add(r["file"])

    def change_scope(self, r: dict, new_scope: str):
        old_key = (r["file"], r["scope"], r["name"])
        self._rows.pop(old_key, None)
        r["scope"] = new_scope
        self._index(r)
        self._dirty_files.add(r["file"])

    def set_var_note(self, *, name: str, file: str | None, note: str) -> int:
        n = 0
        for r in self._rows.values():
            if r["name"] == name and (not file or file in r["file"]):
                r["note"] = note
                self._dirty_files.add(r["file"])
                n += 1
        return n

    # ------------------------------------------------------------- reviews

    @property
    def reviews(self) -> dict[str, dict]:
        return self._reviews

    def set_review(self, key: str, verdict: str, note: str | None):
        self._reviews[normalize_pair_key(key)] = {
            "verdict": verdict, "note": note, "ts": now_iso()}
        self._reviews_dirty = True

    # -------------------------------------------------------------- reads

    def file_rows(self, file: str) -> list[dict]:
        return [r for r in self._rows.values() if r["file"] == file]

    def tracked_files(self) -> list[str]:
        return sorted({r["file"] for r in self._rows.values()
                       if r["status"] != "file_deleted"})

    def all_rows(self, exclude_status: set[str] | None = None) -> list[dict]:
        if exclude_status:
            return [r for r in self._rows.values()
                    if r["status"] not in exclude_status]
        return list(self._rows.values())

    def query(self, pattern: str | None = None, *, status: str | None = None,
              session: str | None = None, file: str | None = None,
              lang: str | None = None, limit: int = 200) -> list[dict]:
        rx = None
        if pattern:
            rx = re.compile(
                "^" + re.escape(pattern).replace("\\*", ".*") + "$", re.I)
        out = []
        for r in self._rows.values():
            if rx and not rx.match(r["name"]):
                continue
            if status and r["status"] != status:
                continue
            if session and session not in (r["first_session"],
                                           r["last_session"]):
                continue
            if file and file not in r["file"]:
                continue
            if lang and r["lang"] != lang:
                continue
            out.append(r)
        out.sort(key=lambda r: r["last_written_at"] or "", reverse=True)
        return out[:limit]

    def stats(self) -> dict:
        out = {"total": 0, "by_status": {}, "by_lang": {}, "files": 0,
               "sessions": 0}
        files, sessions = set(), set()
        for r in self._rows.values():
            out["total"] += 1
            out["by_status"][r["status"]] = \
                out["by_status"].get(r["status"], 0) + 1
            lg = r["lang"] or "?"
            out["by_lang"][lg] = out["by_lang"].get(lg, 0) + 1
            files.add(r["file"])
            sessions.add(r["last_session"])
        out["files"] = len(files)
        out["sessions"] = len(sessions)
        return out


def open_store(root: str | Path) -> Store:
    return Store(root)


def exists(root: str | Path) -> bool:
    d = Path(root) / config.VARMEM_DIRNAME
    return (d / "vars").exists() or (d / "meta.json").exists() or \
        (Path(root) / ".claude" / "varmem" / "varmem.db").exists()
