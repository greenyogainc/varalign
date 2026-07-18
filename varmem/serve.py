"""VarAlign HTTP API — the shared backend for CLI, skill, hook, and extension.

Zero-dependency stdlib server (same ThreadingHTTPServer as the dashboard),
purpose-built and token-gated for network exposure — deliberately NOT the
localhost dashboard opened to the world. Two surfaces:

  Stateless  POST /v1/analyze            submit {path: content} -> findings
             (no persistence, no clone, parse-only: ast.parse / regex never
              execute submitted code, and stored previews are already redacted)

  Stateful   POST /v1/projects/{id}/capture     persist AI-written assignments
             GET  /v1/projects/{id}/context      SessionStart recall block
             GET  /v1/projects/{id}/duplicates   scored suspects
             GET  /v1/projects/{id}/variables    query the registry
             POST /v1/projects/{id}/prompt       repo-scoped remediation prompt
             GET  /v1/projects                    list projects

Each project owns a directory on the data volume holding a working mirror of
the files the client has pushed plus that project's .varmem/ store, so ALL of
capture/reconcile/duplicates/context/prompt reuse the existing engine
unchanged — the mirror simply plays the role of "the repo on disk".

Auth: `Authorization: Bearer <token>` compared (constant-time) against the
token from --token / $VARALIGN_TOKEN. Health/readiness probes are unauthed.
Binding a non-loopback host without a token is refused (fail closed) unless
--no-auth is passed explicitly for local development.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import (__version__, config, context, duplicates, extractors, prompts,
               reconcile, scan, store)
from .capture import process_payload

MAX_BODY_BYTES = 32 * 1024 * 1024      # 32 MB request cap
MAX_FILES = 5000
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# per-project write serialization (the file store is not concurrency-safe
# for two writers touching the same project at once)
_locks_guard = threading.Lock()
_project_locks: dict[str, threading.Lock] = {}


def _project_lock(pid: str) -> threading.Lock:
    with _locks_guard:
        lk = _project_locks.get(pid)
        if lk is None:
            lk = _project_locks[pid] = threading.Lock()
        return lk


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ----------------------------------------------------------------- helpers

def _valid_id(pid: str) -> str:
    if not isinstance(pid, str) or not _ID_RE.match(pid) or ".." in pid:
        raise ApiError(400, "invalid project id (use [A-Za-z0-9._-], "
                            "1-128 chars)")
    return pid


def _validate_files(files) -> dict[str, str]:
    if not isinstance(files, dict) or not files:
        raise ApiError(400, "'files' must be a non-empty {path: content} map")
    if len(files) > MAX_FILES:
        raise ApiError(413, f"too many files (max {MAX_FILES})")
    out = {}
    for rel, content in files.items():
        if not isinstance(rel, str) or not isinstance(content, str):
            raise ApiError(400, "each file must be path:str -> content:str")
        norm = rel.replace("\\", "/").lstrip("/")
        # containment: no traversal out of the sandbox
        if ".." in norm.split("/") or norm == "":
            raise ApiError(400, f"unsafe path: {rel!r}")
        out[norm] = content
    return out


def _write_files(root: Path, files: dict[str, str]):
    for rel, content in files.items():
        p = (root / rel).resolve()
        if not str(p).startswith(str(root.resolve())):
            raise ApiError(400, f"path escapes sandbox: {rel!r}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _supported_languages() -> dict:
    langs: dict[str, list[str]] = {}
    for ext, (lang, _fn) in extractors.EXTRACTORS.items():
        langs.setdefault(lang, []).append(ext)
    return {lang: sorted(exts) for lang, exts in sorted(langs.items())}


# ------------------------------------------------------------- stateless op

def analyze(files: dict[str, str], *, min_level: str = "medium",
            include_prompt: bool = False,
            include_variables: bool = False) -> dict:
    """Scan submitted files in a throwaway sandbox; never persists."""
    tmp = Path(tempfile.mkdtemp(prefix="varalign-analyze-"))
    try:
        _write_files(tmp, files)
        totals = scan.scan_project(tmp)
        st = store.open_store(tmp)
        dups = duplicates.find_duplicates(st)
        floor = {"high": 3, "medium": 2, "low": 1}.get(min_level, 2)
        order = {"high": 3, "medium": 2, "low": 1}
        dups = [d for d in dups if order[d["level"]] >= floor]
        out = {
            "files": totals["files"],
            "variables_found": totals["vars"],
            "languages": _lang_counts(st),
            "duplicates": dups,
            "counts": {
                "high": sum(1 for d in dups if d["level"] == "high"),
                "medium": sum(1 for d in dups if d["level"] == "medium"),
                "low": sum(1 for d in dups if d["level"] == "low"),
            },
        }
        if include_variables:
            out["variables"] = [duplicates._side(r) for r in st.all_rows()]
        if include_prompt:
            text, reason = prompts.repo_prompt(tmp, min_level=min_level)
            out["prompt"] = text
            if text is None:
                out["prompt_skipped"] = reason
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _lang_counts(st) -> dict:
    counts: dict[str, int] = {}
    for r in st.all_rows():
        counts[r["lang"] or "?"] = counts.get(r["lang"] or "?", 0) + 1
    return counts


# ------------------------------------------------------------- stateful ops

class Projects:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.projects_dir = self.root / "projects"

    def mirror(self, pid: str) -> Path:
        return self.projects_dir / pid / "mirror"

    def exists(self, pid: str) -> bool:
        return store.exists(self.mirror(pid))

    def list(self) -> list[dict]:
        out = []
        if not self.projects_dir.exists():
            return out
        for d in sorted(self.projects_dir.iterdir()):
            mirror = d / "mirror"
            if not store.exists(mirror):
                continue
            try:
                stats = store.open_store(mirror).stats()
            except Exception:
                stats = {"total": 0, "files": 0, "sessions": 0}
            out.append({"id": d.name, "tracked": stats["total"],
                        "files": stats["files"],
                        "sessions": stats["sessions"]})
        return out

    def capture(self, pid: str, files: dict[str, str], *,
                session_id: str, tool: str = "Write") -> dict:
        mirror = self.mirror(pid)
        mirror.mkdir(parents=True, exist_ok=True)
        _write_files(mirror, files)
        summary = {"project": pid, "added": 0, "updated": 0, "refreshed": 0,
                   "removed": 0, "files": []}
        for rel in files:
            abs_path = str((mirror / rel).resolve())
            payload = {
                "session_id": session_id,
                "cwd": str(mirror),
                "tool_name": tool if tool in ("Write", "Edit") else "Write",
                "tool_input": {"file_path": abs_path,
                               "content": files[rel]},
            }
            s = process_payload(payload, explicit_root=str(mirror))
            if s.get("handled"):
                for k in ("added", "updated", "refreshed", "removed"):
                    summary[k] += s.get(k, 0)
                summary["files"].append(rel)
        return summary

    def context(self, pid: str) -> str:
        return context.build_context(self.mirror(pid))

    def duplicates(self, pid: str, min_level: str = "medium") -> list[dict]:
        st = store.open_store(self.mirror(pid))
        dups = duplicates.find_duplicates(st)
        order = {"high": 3, "medium": 2, "low": 1}
        floor = order.get(min_level, 2)
        return [d for d in dups if order[d["level"]] >= floor]

    def variables(self, pid: str, **q) -> list[dict]:
        st = store.open_store(self.mirror(pid))
        return [duplicates._side(r) for r in st.query(**q)]

    def prompt(self, pid: str, **kw) -> tuple[str | None, str]:
        return prompts.repo_prompt(self.mirror(pid), **kw)

    def reconcile(self, pid: str) -> dict:
        return reconcile.reconcile_project(self.mirror(pid), force=True,
                                           session_id="api")


# ---------------------------------------------------------------- handler

class _Handler(BaseHTTPRequestHandler):
    token: str | None = None
    projects: Projects = None  # type: ignore[assignment]
    server_version = f"varalign/{__version__}"

    def log_message(self, *a):
        pass

    # ----- io -----
    def _send(self, code: int, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_body(self) -> dict:
        raw = getattr(self, "_raw", b"")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(400, "body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "body must be a JSON object")
        return data

    def _auth(self):
        if self.token is None:
            return  # --no-auth mode
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = header[len(prefix):] if header.startswith(prefix) else ""
        if not supplied or not hmac.compare_digest(supplied, self.token):
            raise ApiError(401, "missing or invalid bearer token")

    # ----- routing -----
    def _route(self):
        path = self.path.partition("?")[0].rstrip("/")
        query = self.path.partition("?")[2]
        params = dict(kv.split("=", 1)
                      for kv in query.split("&") if "=" in kv)
        for k in list(params):
            params[k] = params[k].replace("%20", " ").replace("+", " ")
        segs = [s for s in path.split("/") if s]
        method = self.command
        p = self.projects

        # ---- unauthed health/meta ----
        if segs == ["v1", "health"] and method in ("GET", "HEAD"):
            return self._send(200, {"status": "ok",
                                    "version": __version__})
        if segs == ["v1", "languages"] and method == "GET":
            return self._send(200, {"languages": _supported_languages()})

        # ---- everything else requires a token ----
        self._auth()

        if segs == ["v1", "analyze"] and method == "POST":
            body = self._read_body()
            files = _validate_files(body.get("files"))
            return self._send(200, analyze(
                files,
                min_level=body.get("min_level") or "medium",
                include_prompt=bool(body.get("include_prompt")),
                include_variables=bool(body.get("include_variables"))))

        if segs == ["v1", "projects"] and method == "GET":
            return self._send(200, {"projects": p.list()})

        if len(segs) >= 3 and segs[:2] == ["v1", "projects"]:
            pid = _valid_id(segs[2])
            action = segs[3] if len(segs) > 3 else ""

            if action == "capture" and method == "POST":
                body = self._read_body()
                files = _validate_files(body.get("files"))
                with _project_lock(pid):
                    return self._send(200, p.capture(
                        pid, files,
                        session_id=str(body.get("session_id") or "api"),
                        tool=str(body.get("tool") or "Write")))

            # reads require the project to exist
            if action in ("context", "duplicates", "variables", "report",
                          "") and method == "GET" and not p.exists(pid):
                raise ApiError(404, f"unknown project {pid!r}")

            if action == "report" and method == "GET":
                # full report_data bundle (dups + vars + counts) for a UI to
                # render — the control tower consumes this for remote projects
                from . import report
                return self._send(200, report.report_data(p.mirror(pid)))
            if action == "context" and method == "GET":
                return self._send(200, {"project": pid,
                                        "context": p.context(pid)})
            if action == "duplicates" and method == "GET":
                return self._send(200, {"project": pid, "duplicates":
                                        p.duplicates(pid,
                                        params.get("min_level") or "medium")})
            if action == "variables" and method == "GET":
                q = {}
                if params.get("pattern"):
                    q["pattern"] = params["pattern"]
                for f in ("status", "session", "file", "lang"):
                    if params.get(f):
                        q[f] = params[f]
                try:
                    q["limit"] = min(5000, int(params.get("limit") or 200))
                except ValueError:
                    q["limit"] = 200
                return self._send(200, {"project": pid,
                                        "variables": p.variables(pid, **q)})
            if action == "reconcile" and method == "POST":
                if not p.exists(pid):
                    raise ApiError(404, f"unknown project {pid!r}")
                with _project_lock(pid):
                    return self._send(200, {"project": pid,
                                            "totals": p.reconcile(pid)})
            if action == "prompt" and method == "POST":
                if not p.exists(pid):
                    raise ApiError(404, f"unknown project {pid!r}")
                body = self._read_body()
                text, reason = p.prompt(
                    pid, min_level=body.get("min_level") or "medium",
                    levels=body.get("levels"),
                    statuses=body.get("statuses"),
                    include_reviewed=bool(body.get("include_reviewed")))
                if text is None:
                    return self._send(200, {"project": pid,
                                            "skipped": reason})
                return self._send(200, {
                    "project": pid,
                    "filename": prompts.prompt_filename(pid),
                    "prompt": text})

        raise ApiError(404, "no such route")

    def _dispatch(self):
        # Read the whole request body up front so the socket is always
        # drained before we respond — otherwise an early error (e.g. 401)
        # on a request that carried a body resets the connection on Windows.
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        self._raw = b""
        try:
            if n > MAX_BODY_BYTES:
                self.rfile.read(min(n, MAX_BODY_BYTES))  # partial drain
                raise ApiError(413, f"request too large (max "
                                    f"{MAX_BODY_BYTES} B)")
            if n:
                self._raw = self.rfile.read(n)
            self._route()
        except ApiError as e:
            self._send(e.code, {"error": e.message})
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            self._send(500, {"error": "internal server error"})

    do_GET = _dispatch
    do_POST = _dispatch
    do_HEAD = _dispatch


# ----------------------------------------------------------------- server

def make_server(host: str, port: int, *, data_dir: Path,
                token: str | None) -> ThreadingHTTPServer:
    projects = Projects(Path(data_dir))
    projects.projects_dir.mkdir(parents=True, exist_ok=True)
    handler = type("VarAlignHandler", (_Handler,),
                   {"token": token, "projects": projects})
    return ThreadingHTTPServer((host, port), handler)


def run(host: str = "127.0.0.1", port: int = 8080, *,
        data_dir: str | Path | None = None, token: str | None = None,
        no_auth: bool = False) -> int:
    data_dir = Path(data_dir or os.environ.get("VARALIGN_DATA_DIR")
                    or (Path.home() / ".varalign" / "data"))
    if token is None and not no_auth:
        token = os.environ.get("VARALIGN_TOKEN")
    loopback = host in ("127.0.0.1", "::1", "localhost")
    if not loopback and not token and not no_auth:
        print("REFUSING to bind a non-loopback host without a token. "
              "Set --token/$VARALIGN_TOKEN, or pass --no-auth to override "
              "(local/dev only).")
        return 2
    if no_auth:
        token = None
    srv = make_server(host, port, data_dir=data_dir, token=token)
    auth = "token-gated" if token else "NO AUTH"
    print(f"varalign API [{auth}] on http://{host}:{srv.server_address[1]}/  "
          f"data={data_dir}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
