"""Machine-level registry of repos and groups for the control tower.

Stored at ~/.varmem/repos.json — machine-local by design (absolute paths
don't belong in any repo). The per-repo state itself lives in each repo's
committable .varmem/ directory.

v2 schema (the legacy v1 flat ``list[str]`` is read transparently and
migrated on the first mutation, with a one-time backup):

    {
      "version": 2,
      "repos":  [{"id": "r-…", "path": "C:/abs/path", "name": "basename"}],
      "groups": [{"id": "g-…", "name": "Trading platform",
                  "repo_ids": ["r-…"], "compose_projects": ["trading"],
                  "confirmed": true}]
    }

Repo ids derive from the canonical path (stable across reorderings and
re-registration; case-insensitive on Windows). Group ids are minted once at
creation and survive renames. A repo may belong to any number of groups.
Writes are atomic (tmp + os.replace). A malformed registry is never silently
overwritten — mutations raise RegistryError with the offending path instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

REGISTRY = Path.home() / ".varmem" / "repos.json"


class RegistryError(RuntimeError):
    """The registry file is unreadable or structurally invalid."""


def canon_path(path: str | Path) -> str:
    """Canonical absolute path: resolved, forward slashes on every OS."""
    return str(Path(path).resolve()).replace("\\", "/")


def repo_id_for(path: str | Path) -> str:
    """Stable id for a repo: derived from the canonical path only."""
    c = canon_path(path)
    key = c.lower() if os.name == "nt" else c
    # non-cryptographic id derivation (stable short id from a path), not a
    # signature — usedforsecurity=False says so and keeps it FIPS-safe
    return "r-" + hashlib.sha1(  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
        key.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]


def _new_group_id() -> str:
    return "g-" + uuid.uuid4().hex[:10]


def _repo_name(path: str) -> str:
    return Path(path).name or path


# -------------------------------------------------------------- load / save

def _read_raw():
    if not REGISTRY.exists():
        return None
    try:
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RegistryError(
            f"registry {REGISTRY} is unreadable or not valid JSON ({e}); "
            "fix or move it manually — refusing to overwrite") from e


def _upgrade(data) -> tuple[dict, bool]:
    """Returns (v2 registry dict, came_from_v1)."""
    if data is None:
        return {"version": 2, "repos": [], "groups": []}, False
    if isinstance(data, list):  # v1 flat list[str]
        repos, seen = [], set()
        for p in data:
            if not isinstance(p, str):
                continue
            rid = repo_id_for(p)
            if rid in seen:
                continue
            seen.add(rid)
            cp = canon_path(p)
            repos.append({"id": rid, "path": cp, "name": _repo_name(cp)})
        return {"version": 2, "repos": repos, "groups": []}, True
    if (isinstance(data, dict) and data.get("version") == 2
            and isinstance(data.get("repos"), list)
            and isinstance(data.get("groups"), list)):
        return data, False
    raise RegistryError(
        f"registry {REGISTRY} has an unrecognized structure "
        f"(expected a v1 list or a v2 dict); fix or move it manually — "
        "refusing to overwrite")


def load_registry() -> dict:
    """Validated v2 view of the registry (in-memory migration, no writes)."""
    reg, _ = _upgrade(_read_raw())
    return reg


def _save(reg: dict):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.parent / (REGISTRY.name + ".tmp")
    tmp.write_text(json.dumps(reg, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, REGISTRY)


def _load_for_mutation() -> dict:
    """Load + migrate for a write; back up a v1 file once before rewriting."""
    raw = _read_raw()
    reg, was_v1 = _upgrade(raw)
    if was_v1:
        bak = REGISTRY.parent / (REGISTRY.name + ".v1.bak")
        if not bak.exists():
            bak.write_text(json.dumps(raw, indent=1) + "\n", encoding="utf-8")
    return reg


# --------------------------------------------------- v1-compatible repo API

def list_repos() -> list[str]:
    try:
        return [r["path"] for r in load_registry()["repos"]]
    except RegistryError:
        return []  # read paths stay tolerant, exactly like v1


def add_repo(path: str) -> str:
    reg = _load_for_mutation()
    rid = repo_id_for(path)
    cp = canon_path(path)
    if not any(r["id"] == rid for r in reg["repos"]):
        reg["repos"].append({"id": rid, "path": cp, "name": _repo_name(cp)})
    _save(reg)
    return cp


def remove_repo(path: str):
    reg = _load_for_mutation()
    rid = repo_id_for(path)
    reg["repos"] = [r for r in reg["repos"] if r["id"] != rid]
    for g in reg["groups"]:
        g["repo_ids"] = [i for i in g.get("repo_ids", []) if i != rid]
    _save(reg)


# --------------------------------------------------------------- repo reads

def repo_entries() -> list[dict]:
    return [dict(r) for r in load_registry()["repos"]]


def get_repo(repo_id: str) -> dict | None:
    for r in load_registry()["repos"]:
        if r["id"] == repo_id:
            return dict(r)
    return None


def find_repo(ident: str) -> dict | None:
    """Resolve a repo by id, canonical path, or unique name."""
    entries = load_registry()["repos"]
    for r in entries:
        if r["id"] == ident:
            return dict(r)
    try:
        cid = repo_id_for(ident)
        for r in entries:
            if r["id"] == cid:
                return dict(r)
    except OSError:
        pass
    named = [r for r in entries if r["name"] == ident]
    return dict(named[0]) if len(named) == 1 else None


# -------------------------------------------------------------- group CRUD

def _copy_group(g: dict) -> dict:
    out = dict(g)
    out["repo_ids"] = list(g.get("repo_ids", []))
    out["compose_projects"] = list(g.get("compose_projects", []))
    return out


def group_entries() -> list[dict]:
    return [_copy_group(g) for g in load_registry()["groups"]]


def get_group(group_id: str) -> dict | None:
    for g in load_registry()["groups"]:
        if g["id"] == group_id:
            return _copy_group(g)
    return None


def find_group(ident: str) -> dict | None:
    """Resolve a group by id or unique name."""
    groups = load_registry()["groups"]
    for g in groups:
        if g["id"] == ident:
            return _copy_group(g)
    named = [g for g in groups if g.get("name") == ident]
    return _copy_group(named[0]) if len(named) == 1 else None


def _validate_repo_ids(reg: dict, repo_ids) -> list[str]:
    known = {r["id"] for r in reg["repos"]}
    out = []
    for rid in repo_ids or []:
        if rid not in known:
            raise ValueError(f"unknown repo id: {rid}")
        if rid not in out:
            out.append(rid)
    return out


def create_group(name: str, repo_ids=(), compose_projects=(),
                 confirmed: bool = True) -> dict:
    if not (name or "").strip():
        raise ValueError("group name must not be empty")
    reg = _load_for_mutation()
    g = {
        "id": _new_group_id(),
        "name": name.strip(),
        "repo_ids": _validate_repo_ids(reg, repo_ids),
        "compose_projects": [p for p in (compose_projects or []) if p],
        "confirmed": bool(confirmed),
    }
    reg["groups"].append(g)
    _save(reg)
    return _copy_group(g)


def rename_group(group_id: str, name: str) -> dict:
    if not (name or "").strip():
        raise ValueError("group name must not be empty")
    reg = _load_for_mutation()
    for g in reg["groups"]:
        if g["id"] == group_id:
            g["name"] = name.strip()
            _save(reg)
            return _copy_group(g)
    raise ValueError(f"unknown group id: {group_id}")


def delete_group(group_id: str) -> bool:
    reg = _load_for_mutation()
    before = len(reg["groups"])
    reg["groups"] = [g for g in reg["groups"] if g["id"] != group_id]
    if len(reg["groups"]) == before:
        return False
    _save(reg)
    return True


def set_group_repos(group_id: str, repo_ids) -> dict:
    reg = _load_for_mutation()
    for g in reg["groups"]:
        if g["id"] == group_id:
            g["repo_ids"] = _validate_repo_ids(reg, repo_ids)
            g["confirmed"] = True  # explicit membership edit = confirmation
            _save(reg)
            return _copy_group(g)
    raise ValueError(f"unknown group id: {group_id}")


def add_repo_to_group(group_id: str, repo_id: str) -> dict:
    g = get_group(group_id)
    if g is None:
        raise ValueError(f"unknown group id: {group_id}")
    ids = g["repo_ids"] + ([repo_id] if repo_id not in g["repo_ids"] else [])
    return set_group_repos(group_id, ids)


def remove_repo_from_group(group_id: str, repo_id: str) -> dict:
    g = get_group(group_id)
    if g is None:
        raise ValueError(f"unknown group id: {group_id}")
    return set_group_repos(group_id,
                           [i for i in g["repo_ids"] if i != repo_id])


# ------------------------------------------------- remote API sources (v2.1)

def _source_id(url: str) -> str:
    # non-cryptographic id derived from the URL, not a signature
    return "s-" + hashlib.sha1(  # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
        url.rstrip("/").lower().encode("utf-8"),
        usedforsecurity=False).hexdigest()[:10]


def api_source_entries() -> list[dict]:
    try:
        return [dict(s) for s in load_registry().get("api_sources", [])]
    except RegistryError:
        return []


def add_api_source(url: str, token: str | None = None,
                   name: str | None = None, insecure: bool = False) -> dict:
    """Register a remote VarAlign API whose projects appear in the control
    tower. The token is machine-local (like the absolute paths already here);
    the registry never travels into a repo's committable .varmem/."""
    if not (url or "").strip():
        raise ValueError("api source url must not be empty")
    url = url.strip().rstrip("/")
    reg = _load_for_mutation()
    sources = reg.get("api_sources") or []
    sid = _source_id(url)
    entry = {"id": sid, "url": url,
             "name": (name or "").strip() or url.split("//")[-1],
             "token": token or "", "insecure": bool(insecure)}
    reg["api_sources"] = [s for s in sources if s.get("id") != sid] + [entry]
    _save(reg)
    return dict(entry)


def remove_api_source(ident: str) -> bool:
    reg = _load_for_mutation()
    sources = reg.get("api_sources") or []
    key = (ident or "").rstrip("/")
    kept = [s for s in sources if s.get("id") != ident
            and s.get("url", "").rstrip("/") != key and s.get("name") != ident]
    if len(kept) == len(sources):
        return False
    reg["api_sources"] = kept
    _save(reg)
    return True


def standalone_repo_ids() -> list[str]:
    """Registered repos that belong to no named group (UI pseudo-group)."""
    reg = load_registry()
    grouped: set[str] = set()
    for g in reg["groups"]:
        grouped |= set(g.get("repo_ids", []))
    return [r["id"] for r in reg["repos"] if r["id"] not in grouped]
