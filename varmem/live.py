"""Live control-tower server: `python varmem.py live`.

Local-only HTTP server (127.0.0.1) over every registered repo and group
(`varmem repos add`, `varmem groups …`). The frontend lives in
varmem/live_ui.py; this module is APIs and orchestration only.

Isolation contract: every analysis and write is repository-scoped. Duplicate
detection runs per repo (each store only ever holds its own rows), reviews
and notes write to that repo's .varmem/ alone, rescan/reconcile/prompt each
receive exactly one resolved root. Group endpoints aggregate COUNTS and merge
repository-labelled rows for display, but never combine stores before
scoring and never emit a cross-repository pair or blended prompt.

New APIs address repos by stable id (repos.repo_id_for); the legacy
index-based `/api/repos`, `/api/data`, and POST bodies keep working.
Interactive endpoints return structured JSON errors — the hook entrypoints
(capture/session-start) remain the only place errors are swallowed.
"""
from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

from . import discovery, live_ui, prompts, reconcile, report, repos, scan, \
    store

ISSUES_PAGE_SIZE = 50
VARS_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

_STATUS_SCORE = {"drifted": 0.70, "missing": 0.60, "file_deleted": 0.40}
_STATUS_LEVEL = {"drifted": "medium", "missing": "medium",
                 "file_deleted": "low"}


# ------------------------------------------------------------ repo listing

def _repo_list(extra_roots=None) -> list[dict]:
    """Ordered repo descriptors: extra roots (legacy --project) first, then
    the registry. Each: {id, name, path, root, registered}."""
    try:
        entries = repos.repo_entries()
    except repos.RegistryError:
        raise
    registered_ids = {e["id"] for e in entries}
    out, seen = [], set()

    def _add(path: str, registered: bool):
        root = Path(repos.canon_path(path))
        rid = repos.repo_id_for(root)
        if rid in seen or not root.exists():
            return
        seen.add(rid)
        out.append({"id": rid, "name": root.name or str(root),
                    "path": str(root).replace("\\", "/"), "root": root,
                    "registered": registered})

    for p in (extra_roots or []):
        _add(str(p), repos.repo_id_for(p) in registered_ids)
    for e in entries:
        _add(e["path"], True)
    return out


# ---------------------------------------------------- per-repo data cache

_cache_lock = threading.Lock()
_CACHE: dict[str, tuple[tuple, dict]] = {}


def _sig(root: Path):
    """Cheap change signature over the repo's .varmem state files."""
    d = root / ".varmem"
    parts = []
    try:
        vars_dir = d / "vars"
        if vars_dir.exists():
            for p in sorted(vars_dir.iterdir()):
                if p.suffix == ".jsonl":
                    st = p.stat()
                    parts.append((p.name, st.st_mtime_ns, st.st_size))
        rp = d / "reviews.json"
        if rp.exists():
            st = rp.stat()
            parts.append(("reviews.json", st.st_mtime_ns, st.st_size))
    except OSError:
        return None
    return tuple(parts)


def _data(root: Path) -> dict:
    key = str(root).lower()
    sig = _sig(root)
    with _cache_lock:
        hit = _CACHE.get(key)
        if hit and sig and hit[0] == sig:
            return hit[1]
    data = report.report_data(root)
    with _cache_lock:
        if sig:
            _CACHE[key] = (sig, data)
    return data


# ------------------------------------------------------- remote API sources

def _ssl_ctx(insecure):
    if not insecure:
        return None  # default verification against the system trust store
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api_json(base: str, token, path: str, *, method: str = "GET",
              body: dict | None = None, insecure: bool = False,
              timeout: int = 15):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers)
    ctx = _ssl_ctx(insecure) if url.startswith("https") else None
    # the URL is an operator-registered API source (repos.api_source_entries),
    # never request/attacker input — this is the control tower's whole job
    with urllib.request.urlopen(  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read() or b"{}")


def _remote_repo_descriptors() -> list[dict]:
    """Each registered API source's projects as repo descriptors carrying a
    `remote` handle (no local root). Unreachable sources are skipped."""
    out = []
    for src in repos.api_source_entries():
        try:
            listing = _api_json(src["url"], src.get("token"), "/v1/projects",
                                insecure=src.get("insecure"))
        except Exception:
            continue
        for proj in listing.get("projects", []):
            pid = proj.get("id")
            if not pid:
                continue
            out.append({
                "id": f"api:{src['id']}:{pid}",
                "name": pid, "path": f"{src['name']} · {pid}",
                "registered": True,
                "remote": {"url": src["url"], "token": src.get("token"),
                           "project": pid, "insecure": src.get("insecure"),
                           "source_id": src["id"],
                           "source_name": src["name"]},
            })
    return out


def _report_for(r) -> dict | None:
    """report_data-shaped bundle for a repo descriptor — local (from disk) or
    remote (from the API's /v1/projects/{id}/report). None if empty/down."""
    rem = r.get("remote")
    if rem is not None:
        try:
            return _api_json(rem["url"], rem.get("token"),
                             f"/v1/projects/{rem['project']}/report",
                             insecure=rem.get("insecure"))
        except Exception:
            return None
    root = r["root"]
    if not store.exists(root):
        return None
    return _data(root)


def _zero_counts() -> dict:
    return {"tracked": 0, "needs_attention": 0, "high": 0, "medium": 0,
            "low": 0, "drifted": 0, "missing": 0, "file_deleted": 0,
            "reviewed": 0, "has_store": False}


def _repo_counts(r) -> dict:
    data = _report_for(r)
    if data is None:
        return _zero_counts()
    unrev = data.get("unreviewedLevels") or {}
    c = data["counts"]
    return {
        "tracked": len(data["vars"]),
        "needs_attention": (unrev.get("high", 0) + unrev.get("medium", 0)
                            + c["drifted"] + c["missing"]),
        "high": unrev.get("high", 0), "medium": unrev.get("medium", 0),
        "low": unrev.get("low", 0),
        "drifted": c["drifted"], "missing": c["missing"],
        "file_deleted": c.get("file_deleted", 0),
        "reviewed": data.get("reviewedPairs", 0), "has_store": True,
    }


# ----------------------------------------------------------- issue model

def _issues(r) -> list[dict]:
    """This repo's triage queue: duplicate pairs + drift/removal findings,
    deterministically sorted (unreviewed first, then confidence)."""
    data = _report_for(r)
    if data is None:
        return []
    out = []
    for d in data["dups"]:
        out.append({"kind": "duplicate", "key": d["pair_key"],
                    "level": d["level"], "score": d["score"],
                    "reason": d["reason"], "review": d["review"],
                    "a": d["a"], "b": d["b"],
                    "title": f"{d['a']['name']} ↔ {d['b']['name']}"})
    for v in data["vars"]:
        if v["status"] in _STATUS_SCORE:
            out.append({"kind": v["status"], "key": "s:" + v["id"],
                        "level": _STATUS_LEVEL[v["status"]],
                        "score": _STATUS_SCORE[v["status"]],
                        "reason": v["status"].replace("_", " "),
                        "review": None, "var": v, "title": v["name"]})
    out.sort(key=lambda i: (1 if i["review"] else 0, -i["score"], i["key"]))
    return out


def _filter_issues(items: list[dict], params: dict) -> list[dict]:
    q = (params.get("q") or "").lower()
    level = params.get("level") or ""
    kind = params.get("kind") or ""
    review = params.get("review") or ""
    out = []
    for it in items:
        if level and it["level"] != level:
            continue
        if kind:
            if kind == "status" and it["kind"] == "duplicate":
                continue
            if kind != "status" and it["kind"] != kind:
                continue
        if review == "unreviewed" and it["review"]:
            continue
        if review == "reviewed" and not it["review"]:
            continue
        if review in ("not_duplicate", "duplicate", "merged") and (
                not it["review"] or it["review"]["verdict"] != review):
            continue
        if q:
            if it["kind"] == "duplicate":
                hay = " ".join((it["title"], it["a"]["file"],
                                it["b"]["file"]))
            else:
                hay = " ".join((it["title"], it["var"]["file"]))
            if q not in hay.lower():
                continue
        out.append(it)
    return out


def _paginate(items: list, params: dict, default_size: int) -> dict:
    try:
        page = max(1, int(params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        size = min(MAX_PAGE_SIZE, max(1, int(params.get("size")
                                             or default_size)))
    except (TypeError, ValueError):
        size = default_size
    total = len(items)
    pages = max(1, -(-total // size))
    page = min(page, pages)
    start = (page - 1) * size
    return {"total": total, "page": page, "size": size, "pages": pages,
            "items": items[start:start + size]}


def _recent_events(r, limit: int = 8) -> list[dict]:
    if r.get("remote") is not None:
        return []  # remote events aren't exposed by the API surface
    p = r["root"] / ".varmem" / "events.jsonl"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append({"ts": e.get("ts"), "action": e.get("action"),
                    "name": e.get("name"), "file": e.get("file"),
                    "detail": e.get("detail"),
                    "session": (e.get("session_id") or "")[:12]})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- handler

class _Handler(BaseHTTPRequestHandler):
    extra_roots: list[Path] | None = None

    def log_message(self, *a):  # silence per-request stderr noise
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ------------------------------------------------------- repo lookup

    def _repos(self) -> list[dict]:
        return _repo_list(self.extra_roots) + _remote_repo_descriptors()

    def _resolve(self, ident) -> dict | None:
        """Repo by stable id; legacy numeric index still accepted."""
        rl = self._repos()
        for r in rl:
            if r["id"] == ident:
                return r
        try:
            return rl[int(ident)]
        except (ValueError, TypeError, IndexError):
            return None

    def _group_members(self, gid) -> tuple[dict | None, list[dict]]:
        rl = self._repos()
        by_id = {r["id"]: r for r in rl}
        if gid and gid.startswith("api-src:"):
            sid = gid.split(":", 1)[1]
            src = next((s for s in repos.api_source_entries()
                        if s["id"] == sid), None)
            if src is None:
                return None, []
            members = [r for r in rl
                       if (r.get("remote") or {}).get("source_id") == sid]
            return ({"id": gid, "name": src["name"], "confirmed": True,
                     "compose_projects": [], "remote": True}, members)
        if gid in ("standalone", "", None):
            grouped: set[str] = set()
            for g in repos.group_entries():
                grouped |= set(g["repo_ids"])
            # remote projects live under their source group, not standalone
            members = [r for r in rl if r["id"] not in grouped
                       and r.get("remote") is None]
            return ({"id": "standalone", "name": "Standalone repos",
                     "confirmed": True, "compose_projects": []}, members)
        g = repos.get_group(gid)
        if g is None:
            return None, []
        members = [by_id[i] for i in g["repo_ids"] if i in by_id]
        return g, members

    # -------------------------------------------------------------- GET

    def do_GET(self):
        try:
            self._get()
        except repos.RegistryError as e:
            self._json({"error": str(e)}, 500)
        except Exception as e:  # interactive server: never swallow
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _get(self):
        path, _, query = self.path.partition("?")
        params = dict(parse_qsl(query, keep_blank_values=True))

        if path == "/":
            self._send(200, live_ui.PAGE.encode("utf-8"),
                       "text/html; charset=utf-8")

        elif path == "/api/repos":  # legacy index-ordered listing
            out = []
            for r in self._repos():
                if r.get("remote") is None and store.exists(r["root"]):
                    try:
                        tracked = store.open_store(r["root"]).stats()["total"]
                    except Exception:
                        tracked = 0
                else:
                    tracked = len((_report_for(r) or {}).get("vars", []))
                out.append({"id": r["id"], "name": r["name"],
                            "path": str(r["root"]) if r.get("remote") is None
                            else r["path"], "tracked": tracked})
            self._json(out)

        elif path == "/api/tree":
            rl = self._repos()
            groups = repos.group_entries()
            grouped: set[str] = set()
            for g in groups:
                grouped |= set(g["repo_ids"])
            # each registered API source becomes a group of its projects
            src_groups = []
            for src in repos.api_source_entries():
                mem = [r["id"] for r in rl
                       if (r.get("remote") or {}).get("source_id") == src["id"]]
                src_groups.append({
                    "id": f"api-src:{src['id']}", "name": src["name"],
                    "repo_ids": mem, "compose_projects": [],
                    "confirmed": True, "remote": True})
            self._json({
                "groups": groups + src_groups,
                "repos": [{"id": r["id"], "name": r["name"],
                           "path": r["path"], "registered": r["registered"],
                           "remote": r.get("remote") is not None}
                          for r in rl],
                "standalone": [r["id"] for r in rl if r["id"] not in grouped
                               and r.get("remote") is None],
            })

        elif path == "/api/overview":
            meta, members = self._group_members(params.get("group"))
            if meta is None:
                self._json({"error": "unknown group id"}, 404)
                return
            rep_rows, queue, activity = [], [], []
            totals = _zero_counts()
            for r in members:
                counts = _repo_counts(r)
                rep_rows.append({"id": r["id"], "name": r["name"],
                                 "path": r["path"],
                                 "registered": r["registered"],
                                 "counts": counts})
                for k in totals:
                    if k != "has_store":
                        totals[k] += counts[k]
                label = {"id": r["id"], "name": r["name"]}
                for it in _issues(r)[:8]:
                    queue.append({**it, "repo": label})
                for e in _recent_events(r, limit=6):
                    activity.append({**e, "repo": label})
            queue.sort(key=lambda i: (1 if i["review"] else 0,
                                      -i["score"], i["key"]))
            activity.sort(key=lambda e: e.get("ts") or "", reverse=True)
            totals["has_store"] = True
            self._json({"group": meta, "repos": rep_rows,
                        "totals": totals, "queue": queue[:12],
                        "activity": activity[:10]})

        elif path == "/api/repo":
            r = self._resolve(params.get("id"))
            if r is None:
                self._json({"error": "unknown repo id"}, 404)
                return
            out = {"id": r["id"], "name": r["name"], "path": r["path"],
                   "registered": r["registered"], "counts": _repo_counts(r),
                   "remote": r.get("remote") is not None}
            data = _report_for(r)
            if data:
                out["levelCounts"] = data["levelCounts"]
                out["generated"] = data["generated"]
                out["omitted"] = data["omitted"]
            self._json(out)

        elif path == "/api/issues":
            r = self._resolve(params.get("repo"))
            if r is None:
                self._json({"error": "unknown repo id"}, 404)
                return
            items = _filter_issues(_issues(r), params)
            page = _paginate(items, params, ISSUES_PAGE_SIZE)
            label = {"id": r["id"], "name": r["name"]}
            page["items"] = [{**it, "repo": label} for it in page["items"]]
            self._json(page)

        elif path == "/api/vars":
            r = self._resolve(params.get("repo"))
            if r is None:
                self._json({"error": "unknown repo id"}, 404)
                return
            data = _report_for(r)
            if data is None:
                self._json(_paginate([], params, VARS_PAGE_SIZE))
                return
            q = (params.get("q") or "").lower()
            status = params.get("status") or ""
            var_level = data["varLevel"]
            items = []
            for v in data["vars"]:
                if status and v["status"] != status:
                    continue
                if q and q not in " ".join(
                        (v["name"], v["file"], v["value"] or "")).lower():
                    continue
                items.append({**v, "dup_level": var_level.get(v["id"])})
            self._json(_paginate(items, params, VARS_PAGE_SIZE))

        elif path == "/api/discovery":
            self._json(discovery.discover())

        elif path == "/api/data":  # legacy full-report endpoint
            r = self._resolve(params.get("repo", "0"))
            if r is None:
                self._json({"error": "unknown repo"}, 404)
                return
            if r.get("remote") is None and params.get("reconcile") == "1":
                reconcile.reconcile_project(r["root"], max_files=500,
                                            session_id="live")
            self._json(_report_for(r) or {})

        else:
            self._json({"error": "not found"}, 404)

    # -------------------------------------------------------------- POST

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad request body"}, 400)
            return
        try:
            self._post(body)
        except repos.RegistryError as e:
            self._json({"error": str(e)}, 500)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # interactive server: never swallow
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _post(self, body: dict):
        path = self.path.partition("?")[0]

        if path in ("/api/review", "/api/annotate", "/api/rescan",
                    "/api/reconcile", "/api/prompt"):
            r = self._resolve(body.get("repo", 0))
            if r is None:
                self._json({"error": "unknown repo id"}, 404)
                return
            if r.get("remote") is not None and path != "/api/prompt":
                self._json({"error": "this project is served by a remote "
                            "VarAlign API — reviews, notes, rescans and "
                            "reconciles are read-only here for now"}, 501)
                return
            root = r.get("root")
            if path == "/api/review":
                key = str(body.get("pair_key") or "")[:2000]
                verdict = body.get("verdict")
                if not key or verdict not in ("not_duplicate", "duplicate",
                                              "merged"):
                    self._json({"error": "bad review payload"}, 400)
                    return
                st = store.open_store(root)
                st.set_review(key, verdict, body.get("note"))
                st.save()
                self._json({"ok": True, "repo": r["id"]})
            elif path == "/api/annotate":
                name = str(body.get("name") or "")
                if not name:
                    self._json({"error": "name is required"}, 400)
                    return
                st = store.open_store(root)
                count = st.set_var_note(name=name, file=body.get("file"),
                                        note=str(body.get("note") or ""))
                st.save()
                self._json({"ok": True, "repo": r["id"],
                            "annotated": count})
            elif path == "/api/rescan":
                totals = scan.scan_project(root)
                self._json({"ok": True, "repo": r["id"], "totals": totals})
            elif path == "/api/reconcile":
                totals = reconcile.reconcile_project(
                    root, force=bool(body.get("force")), session_id="live")
                self._json({"ok": True, "repo": r["id"], "totals": totals})
            elif path == "/api/prompt":
                self._json(self._prompt_item(r, body))

        elif path == "/api/prompts":
            meta, members = self._group_members(body.get("group"))
            if meta is None:
                self._json({"error": "unknown group id"}, 404)
                return
            self._json({"group": meta["id"],
                        "items": [self._prompt_item(r, body)
                                  for r in members]})

        elif path == "/api/groups/create":
            g = repos.create_group(str(body.get("name") or ""),
                                   body.get("repo_ids") or [],
                                   body.get("compose_projects") or [])
            self._json({"ok": True, "group": g})

        elif path == "/api/groups/rename":
            g = repos.rename_group(str(body.get("id") or ""),
                                   str(body.get("name") or ""))
            self._json({"ok": True, "group": g})

        elif path == "/api/groups/delete":
            ok = repos.delete_group(str(body.get("id") or ""))
            if not ok:
                self._json({"error": "unknown group id"}, 404)
                return
            self._json({"ok": True})

        elif path == "/api/groups/members":
            g = repos.set_group_repos(str(body.get("id") or ""),
                                      body.get("repo_ids") or [])
            self._json({"ok": True, "group": g})

        elif path == "/api/discovery/confirm":
            g = discovery.confirm_suggestion(
                {"repo_ids": body.get("repo_ids") or [],
                 "compose_projects": body.get("compose_projects") or [],
                 "name": body.get("name")},
                name=body.get("name"))
            self._json({"ok": True, "group": g})

        else:
            self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------ prompts

    def _prompt_item(self, r: dict, body: dict) -> dict:
        """One remediation artifact for exactly one repository — computed
        locally, or fetched from the API for a remote project."""
        item = {"repo": r["id"], "name": r["name"]}
        rem = r.get("remote")
        if rem is not None:
            try:
                res = _api_json(
                    rem["url"], rem.get("token"),
                    f"/v1/projects/{rem['project']}/prompt", method="POST",
                    body={"min_level": body.get("min_level") or "medium",
                          "levels": body.get("levels"),
                          "statuses": body.get("statuses"),
                          "include_reviewed":
                              bool(body.get("include_reviewed"))},
                    insecure=rem.get("insecure"))
            except Exception as e:
                item["skipped"] = f"remote error: {e}"
                return item
            if res.get("skipped"):
                item["skipped"] = res["skipped"]
            else:
                item["filename"] = (res.get("filename")
                                    or prompts.prompt_filename(r["name"]))
                item["prompt"] = res.get("prompt")
            return item
        text, reason = prompts.repo_prompt(
            r["root"],
            min_level=body.get("min_level") or "medium",
            levels=body.get("levels"),
            statuses=body.get("statuses"),
            include_reviewed=bool(body.get("include_reviewed")))
        if text is None:
            item["skipped"] = reason
        else:
            item["filename"] = prompts.prompt_filename(r["name"])
            item["prompt"] = text
        return item


# ----------------------------------------------------------------- server

def make_server(port: int = 0, extra_roots: list[Path] | None = None
                ) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"extra_roots": extra_roots})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve(port: int = 7787, extra_roots: list[Path] | None = None,
          open_browser: bool = True) -> int:
    srv = make_server(port, extra_roots)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"varmem live: {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
