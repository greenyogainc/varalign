"""Optional Docker/Compose container discovery → group suggestions.

Best-effort and strictly read-only: when the docker CLI is installed, inspect
running containers, read Compose project labels and bind mounts, map mounted
paths onto registered repository roots, and return structured suggestions.
This module NEVER mutates the registry — turning a suggestion into a group is
an explicit, separate confirmation write through repos.create_group().

Degrades cleanly when Docker is missing, stopped, remote, or returns
malformed data; confirmed groups are untouched either way (they live in the
registry, not here). No YAML/Compose-file parsing is attempted.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import repos

_TIMEOUT_S = 6
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_WORKDIR_LABEL = "com.docker.compose.project.working_dir"

# Docker Desktop / WSL translate C:\Users\x into /run/desktop/mnt/host/c/…,
# /host_mnt/c/…, /mnt/c/…, or /c/… — fold them all back into c:/… form.
_DRIVE_MOUNT = re.compile(
    r"^(?:/run/desktop/mnt/host|/host_mnt|/mnt)?/([a-zA-Z])(/.*)?$")


class DiscoveryError(RuntimeError):
    pass


def _default_runner(cmd: list[str]) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True,
                         timeout=_TIMEOUT_S)
    if out.returncode != 0:
        raise DiscoveryError((out.stderr or "").strip()[:300]
                             or f"docker exited {out.returncode}")
    return out.stdout


def _norm(path: str) -> str:
    """Comparable form of a host path: forward slashes, drive-letter folded,
    lowercase (suggestions only — safe to compare case-insensitively)."""
    s = (path or "").strip().replace("\\", "/")
    m = _DRIVE_MOUNT.match(s)
    if m and len(m.group(1)) == 1:
        s = f"{m.group(1)}:{m.group(2) or ''}"
    return s.rstrip("/").lower()


def _within(mount: str, repo_root: str) -> bool:
    """mount and repo overlap: equal, or one contains the other."""
    if not mount or not repo_root:
        return False
    return (mount == repo_root
            or mount.startswith(repo_root + "/")
            or repo_root.startswith(mount + "/"))


def _container_paths(c: dict) -> list[str]:
    paths = []
    for m in c.get("Mounts") or []:
        if isinstance(m, dict) and m.get("Type") == "bind" and m.get("Source"):
            paths.append(str(m["Source"]))
    labels = ((c.get("Config") or {}).get("Labels")) or {}
    wd = labels.get(COMPOSE_WORKDIR_LABEL)
    if wd:
        paths.append(str(wd))
    return paths


def discover(runner=None) -> dict:
    """Structured, non-mutating suggestions:

    {"docker": "ok"|"unavailable"|"error", "error": str|None,
     "containers_seen": int,
     "suggestions": [{"name", "compose_projects", "repo_ids", "repo_names",
                      "containers", "already_grouped"}]}
    """
    result = {"docker": "ok", "error": None, "containers_seen": 0,
              "suggestions": []}
    try:
        registered = repos.repo_entries()
        existing_groups = repos.group_entries()
    except repos.RegistryError as e:
        return {"docker": "error", "error": str(e), "containers_seen": 0,
                "suggestions": []}

    if runner is None:
        if shutil.which("docker") is None:
            return {"docker": "unavailable",
                    "error": "docker CLI not found on PATH",
                    "containers_seen": 0, "suggestions": []}
        runner = _default_runner

    try:
        ids = runner(["docker", "ps", "-q"]).split()
        if not ids:
            return result
        containers = json.loads(runner(["docker", "inspect", *ids]))
        if not isinstance(containers, list):
            raise DiscoveryError("unexpected docker inspect output")
    except Exception as e:  # missing daemon, timeout, malformed JSON, …
        return {"docker": "error", "error": str(e)[:300],
                "containers_seen": 0, "suggestions": []}

    by_repo_id = {r["id"]: r for r in registered}
    norm_roots = {r["id"]: _norm(r["path"]) for r in registered}

    # key -> {"project": str|None, "repo_ids": set, "containers": set}
    buckets: dict[str, dict] = {}
    for c in containers:
        if not isinstance(c, dict):
            continue
        result["containers_seen"] += 1
        labels = ((c.get("Config") or {}).get("Labels")) or {}
        project = labels.get(COMPOSE_PROJECT_LABEL)
        cname = (c.get("Name") or c.get("Id") or "?").lstrip("/")
        matched = set()
        for src in _container_paths(c):
            nsrc = _norm(src)
            for rid, nroot in norm_roots.items():
                if _within(nsrc, nroot):
                    matched.add(rid)
        if not matched:
            continue
        key = f"compose:{project}" if project else f"container:{cname}"
        b = buckets.setdefault(key, {"project": project,
                                     "repo_ids": set(), "containers": set()})
        b["repo_ids"] |= matched
        b["containers"].add(cname)

    grouped_sets = [frozenset(g.get("repo_ids", [])) for g in existing_groups]
    for key in sorted(buckets):
        b = buckets[key]
        if len(b["repo_ids"]) < 2:
            continue  # a group needs at least two participating repos
        rids = sorted(b["repo_ids"])
        result["suggestions"].append({
            "name": b["project"] or f"container {sorted(b['containers'])[0]}",
            "compose_projects": [b["project"]] if b["project"] else [],
            "repo_ids": rids,
            "repo_names": [by_repo_id[r]["name"] for r in rids],
            "containers": sorted(b["containers"]),
            "already_grouped": frozenset(rids) in grouped_sets,
        })
    return result


def confirm_suggestion(suggestion: dict, name: str | None = None) -> dict:
    """Explicit user confirmation: persist a suggestion as a confirmed group.
    Idempotent — an existing group with identical membership is returned
    unchanged rather than duplicated."""
    rids = list(suggestion.get("repo_ids") or [])
    if not rids:
        raise ValueError("suggestion has no repositories")
    for g in repos.group_entries():
        if set(g.get("repo_ids", [])) == set(rids):
            return g
    return repos.create_group(
        name or suggestion.get("name") or "container group",
        rids, compose_projects=suggestion.get("compose_projects") or [],
        confirmed=True)
