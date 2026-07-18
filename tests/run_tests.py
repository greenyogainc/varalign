"""Zero-dependency test runner for varmem.

    python tests/run_tests.py

Covers extractors, redaction, capture attribution, reconciliation states,
context building, the CLI query path — including full simulated
PostToolUse / SessionStart hook payloads — plus the v2 repo/group registry,
container discovery, repo isolation, prompt generation, and the live server.

The machine registry (~/.varmem/repos.json) is NEVER touched: main() points
varmem.repos.REGISTRY at a temp file before any test runs.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from varmem import (config, context, discovery, duplicates,  # noqa: E402
                    extractors, live, prompts, redact, reconcile, report,
                    repos, scan, store)
from varmem.capture import process_payload, process_kilo_payload  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    return cond


def names(vars_, **filters):
    out = []
    for v in vars_:
        if all(v.get(k) == val for k, val in filters.items()):
            out.append(v["name"])
    return out


# ------------------------------------------------------------- extractors --

def test_python_extractor():
    src = '''
API_URL = "https://api.example.com"
TIMEOUT: int = 30
count = 0
count += 1
a, b = 1, 2

class Client:
    retries = 3
    def __init__(self):
        self.base_url = API_URL
        token = compute()
        if (n := len(token)) > 5:
            pass
'''
    got = extract_ok("t.py", src)
    check(set(names(got)) >= {"API_URL", "TIMEOUT", "count", "a", "b",
                              "retries", "self.base_url", "token", "n"},
          "python: all expected names")
    check("API_URL" in names(got, scope=""), "python: module scope")
    check("retries" in names(got, scope="Client"), "python: class scope")
    check("token" in names(got, scope="Client.__init__"), "python: func scope")
    check("n" in names(got, kind="walrus"), "python: walrus")
    check("count" in names(got, kind="augassign"), "python: augassign")
    by = {v["name"]: v for v in got}
    check(by["API_URL"]["value"] == '"https://api.example.com"',
          "python: RHS source captured")
    check(by["self.base_url"]["kind"] == "attr", "python: attr kind")


def test_js_extractor():
    src = '''
const API_URL = "https://x.dev";
export const MAX_RETRIES = 5, BACKOFF_MS = 200;
let counter = 0;
var legacy = true;
const { host, port: dbPort, opts = {} } = loadConfig();
const [first, second] = pair;
const handler = async (req) => { return req; };
appState = "ready";
config.debug = false;
let uninitialized;
// const commented = 1;
if (x === y) {}
'''
    got = extract_ok("t.ts", src)
    ns = set(names(got))
    check(ns >= {"API_URL", "MAX_RETRIES", "BACKOFF_MS", "counter", "legacy",
                 "host", "dbPort", "opts", "first", "second", "handler",
                 "appState", "config.debug"},
          f"js: expected names (got {sorted(ns)})")
    check("uninitialized" not in ns, "js: bare let not an assignment")
    check("commented" not in ns, "js: comment ignored")
    check("x" not in ns, "js: comparison not an assignment")
    by = {v["name"]: v for v in got}
    check(by["appState"]["kind"] == "reassign", "js: bare reassign kind")
    check(by["MAX_RETRIES"]["value"] == "5", "js: multi-declarator RHS split")
    check(by["API_URL"]["value"] == '"https://x.dev"',
          "js: '//' inside a string literal is not a comment (full URL kept)")


def test_misc_extractors():
    ps = extract_ok("t.ps1", '$ApiKey = "abc"\n$global:Mode = 2\n'
                             'Set-Variable -Name Retries -Value 9\n# $no = 1')
    check(set(names(ps)) == {"ApiKey", "Mode", "Retries"}, "ps1: names")

    sh = extract_ok("t.sh", 'export PATH_X=/usr/bin\nlocal tmp=/tmp/x\n'
                            'RETRIES=3\n# NOPE=1\nif [ "$a" = "$b" ]; then :; fi')
    check(set(names(sh)) == {"PATH_X", "tmp", "RETRIES"}, "sh: names")

    sh2 = extract_ok("h.sh", 'REAL=1\ncat > f.env <<EOF\nINSIDE=doc\n'
                             'ALSO_INSIDE=doc\nEOF\nAFTER=2\n')
    check(set(names(sh2)) == {"REAL", "AFTER"},
          "sh: heredoc body not extracted")

    ps2 = extract_ok("h.ps1", "$real = 1\n$msg = @'\n$inside = 2\n'@\n"
                              "$after = 3\n")
    check(set(names(ps2)) == {"real", "msg", "after"},
          "ps1: here-string body not extracted")

    env = extract_ok(".env.production", "DB_HOST=localhost\n# C=1\nDB_PORT=5432")
    check(set(names(env)) == {"DB_HOST", "DB_PORT"}, "env: names")

    cs = extract_ok("t.cs", "var total = 0;\nprivate const int MaxItems = 10;\n"
                            "return x = 5;\n")
    check(set(names(cs)) == {"total", "MaxItems"}, "cs: names")

    go = extract_ok("t.go", 'client, err := NewClient()\nvar retries = 3\n'
                            'for i := 0; i < n; i++ {\n}\n_, ok := m[k]\n')
    check(set(names(go)) == {"client", "err", "retries", "ok"}, "go: names")


def extract_ok(path, src):
    lang, got = extractors.extract(path, src)
    check(lang is not None, f"extract dispatch for {path}")
    return got


def test_redaction():
    p, red = redact.make_preview("AKIAIOSFODNN7EXAMPLE", True)
    check(red and "redacted" in p, "redact: AWS key")
    p, red = redact.make_preview('"sk-abc123def456ghi789jkl"', True)
    check(red, "redact: sk- token")
    p, red = redact.make_preview('"https://api.example.com/v1"', True)
    check(not red and "api.example.com" in p, "redact: plain URL untouched")
    p, red = redact.make_preview("AKIAIOSFODNN7EXAMPLE", False)
    check(not red, "redact: disabled passes through")
    check(redact.value_hash("a  b") == redact.value_hash("a b"),
          "hash: whitespace-normalized")


# ---------------------------------------------------- capture + reconcile --

def _payload(tool, root, file, session="sess-1", **tool_input):
    return {
        "session_id": session,
        "transcript_path": "/tmp/fake.jsonl",
        "cwd": str(root),
        "permission_mode": "default",
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(file), **tool_input},
        "tool_response": {"filePath": str(file), "success": True},
    }


def test_capture_write_and_edit(root: Path):
    f = root / "src" / "settings.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('MAX_CONN = 10\nDB_URL = "postgres://localhost/dev"\n',
                 encoding="utf-8")
    s = process_payload(_payload("Write", root, f))
    check(s["handled"] and s["added"] == 2, f"capture: Write adds 2 ({s})")

    # human-owned var pre-exists; Claude edits ONE var only
    f.write_text('MAX_CONN = 10\nDB_URL = "postgres://localhost/dev"\n'
                 "HUMAN_VAR = 42\n", encoding="utf-8")
    time.sleep(0.05)
    f.write_text('MAX_CONN = 25\nDB_URL = "postgres://localhost/dev"\n'
                 "HUMAN_VAR = 42\n", encoding="utf-8")
    s = process_payload(_payload("Edit", root, f, session="sess-2",
                                 old_string="MAX_CONN = 10",
                                 new_string="MAX_CONN = 25"))
    check(s["handled"] and s["updated"] == 1 and s["added"] == 0,
          f"capture: Edit attributes only MAX_CONN ({s})")
    rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
    check("HUMAN_VAR" not in rows, "capture: human var not claimed")
    check(rows["MAX_CONN"]["last_session"] == "sess-2"
          and rows["MAX_CONN"]["first_session"] == "sess-1",
          "capture: session provenance tracked")
    check(rows["MAX_CONN"]["value_preview"] == "25", "capture: value updated")

    # unsupported tool / outside root / unsupported ext are all no-ops
    s = process_payload(_payload("Bash", root, f))
    check(not s["handled"], "capture: non-file tool skipped")
    s = process_payload(_payload("Write", root, Path(tempfile.gettempdir()) / "x.py"))
    check(not s["handled"], "capture: outside-root skipped")
    s = process_payload(_payload("Write", root, root / "notes.txt"))
    check(not s["handled"], "capture: unsupported extension skipped")


def test_reconcile_states(root: Path):
    f = root / "src" / "settings.py"
    # out-of-band change: human edits MAX_CONN, deletes DB_URL
    time.sleep(1.1)  # ensure mtime advances past last_verified_at (1s res)
    f.write_text("MAX_CONN = 999\nHUMAN_VAR = 42\n", encoding="utf-8")
    totals = reconcile.reconcile_project(root, force=True)
    check(totals["drifted"] == 1 and totals["missing"] == 1,
          f"reconcile: drift + missing detected ({totals})")
    rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
    check(rows["MAX_CONN"]["status"] == "drifted"
          and rows["MAX_CONN"]["repo_value_preview"] == "999",
          "reconcile: drifted repo value recorded")
    check(rows["DB_URL"]["status"] == "missing", "reconcile: missing status")

    # Claude rewrites the drifted var -> active again
    f.write_text("MAX_CONN = 999\nHUMAN_VAR = 42\n", encoding="utf-8")
    process_payload(_payload("Edit", root, f, session="sess-3",
                             old_string="MAX_CONN = 999",
                             new_string="MAX_CONN = 999"))
    rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
    check(rows["MAX_CONN"]["status"] == "active",
          "reconcile: re-write by Claude returns to active")

    # deleted file
    g = root / "src" / "gone.py"
    g.write_text("DOOMED = 1\n", encoding="utf-8")
    process_payload(_payload("Write", root, g, session="sess-3"))
    g.unlink()
    totals = reconcile.reconcile_project(root, force=True)
    check(totals["file_deleted"] >= 1, f"reconcile: file_deleted ({totals})")

    # mtime fast-path skips unchanged files
    totals = reconcile.reconcile_project(root, force=False)
    check(totals["skipped_files"] >= 1, f"reconcile: mtime skip ({totals})")


def test_context_and_session_start(root: Path):
    ctx = context.build_context(root)
    check("varmem: variables Claude assigned" in ctx, "context: header")
    check("drifted" in ctx or "missing" in ctx.lower(),
          "context: anomaly sections present")
    check(len(ctx) < context.MAX_CONTEXT_CHARS + 20, "context: under cap")

    out = context.session_start_output(
        {"session_id": "sess-4", "cwd": str(root), "source": "startup",
         "hook_event_name": "SessionStart"})
    parsed = json.loads(out)
    hso = parsed.get("hookSpecificOutput", {})
    check(hso.get("hookEventName") == "SessionStart"
          and "varmem" in hso.get("additionalContext", ""),
          "session-start: valid hook JSON")

    empty = tempfile.mkdtemp(prefix="varmem-empty-")
    try:
        check(context.session_start_output({"cwd": empty}) == "",
              "session-start: silent when nothing tracked")
    finally:
        shutil.rmtree(empty, ignore_errors=True)


def test_query_and_events(root: Path):
    st = store.open_store(root)
    rows = st.query("MAX_*")
    check(len(rows) == 1 and rows[0]["name"] == "MAX_CONN",
          "query: wildcard pattern")
    rows = st.query(session="sess-1")
    check({r["name"] for r in rows} >= {"MAX_CONN", "DB_URL"},
          "query: by session")
    n_events = len(st.events())
    check(n_events >= 5, f"events: audit trail populated ({n_events})")


def test_capture_never_raises(root: Path):
    s = process_payload({})  # empty payload
    check(not s["handled"], "robustness: empty payload")
    s = process_payload({"tool_name": "Write", "tool_input": {},
                         "cwd": str(root)})
    check(not s["handled"], "robustness: missing file_path")
    # syntactically broken python: extractor yields nothing, no crash
    f = root / "broken.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    s = process_payload(_payload("Write", root, f))
    check(s["handled"] and s["added"] == 0, "robustness: syntax error tolerated")


# ------------------------------------------------------- kilo capture ------

def test_capture_kilo_write_and_edit():
    root = Path(tempfile.mkdtemp(prefix="varmem-kilo-cap-"))
    try:
        f = root / "kilo" / "k.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("KILO_A = 1\nKILO_B = 2\n", encoding="utf-8")
        s = process_kilo_payload(_payload("Write", root, f, session="ksess-1"))
        check(s["handled"] and s["added"] == 2,
              f"capture-kilo: Write adds 2 ({s})")
        rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
        check(rows["KILO_A"]["origin"] == "kilo"
              and rows["KILO_B"]["origin"] == "kilo",
              "capture-kilo: rows tagged origin=kilo")
        check(rows["KILO_A"]["first_session"] == "kilo:ksess-1"
              and rows["KILO_A"]["last_session"] == "kilo:ksess-1",
              "capture-kilo: session namespaced kilo:<id>")

        # edit ONE var; the other must not be re-attributed to the new session
        f.write_text("KILO_A = 99\nKILO_B = 2\n", encoding="utf-8")
        s = process_kilo_payload(_payload(
            "Edit", root, f, session="ksess-2",
            old_string="KILO_A = 1", new_string="KILO_A = 99"))
        check(s["handled"] and s["updated"] == 1 and s["added"] == 0,
              f"capture-kilo: Edit attributes only KILO_A ({s})")
        rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
        check(rows["KILO_A"]["origin"] == "kilo"
              and rows["KILO_A"]["last_session"] == "kilo:ksess-2",
              "capture-kilo: edited var re-attributed to new kilo session")
        check(rows["KILO_B"]["last_session"] == "kilo:ksess-1",
              "capture-kilo: untouched var keeps its prior session")

        # non-file tool is a no-op
        s = process_kilo_payload(_payload("Bash", root, f, session="ksess-3"))
        check(not s["handled"], "capture-kilo: non-file tool skipped")

        # double-prefix guard: an already-namespaced session id is left as-is
        f.write_text("KILO_A = 100\nKILO_B = 2\n", encoding="utf-8")
        s = process_kilo_payload(_payload(
            "Edit", root, f, session="kilo:already",
            old_string="KILO_A = 99", new_string="KILO_A = 100"))
        check(s["handled"], f"capture-kilo: double-prefix payload handled ({s})")
        rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
        check(rows["KILO_A"]["last_session"] == "kilo:already",
              "capture-kilo: double-prefix guard "
              f"({rows['KILO_A']['last_session']!r})")

        # a non-string session id must be coerced, not raise (never-raises)
        f.write_text("KILO_A = 101\nKILO_B = 2\n", encoding="utf-8")
        s = process_kilo_payload({**_payload(
            "Edit", root, f, old_string="KILO_A = 100",
            new_string="KILO_A = 101"), "session_id": 12345})
        check(s["handled"], f"capture-kilo: non-string session handled ({s})")
        rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
        check(rows["KILO_A"]["last_session"] == "kilo:12345",
              "capture-kilo: non-string session id coerced to kilo:<id>")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_kilo_claims_and_survives_scan():
    root = _make_repo("varmem-kilo-scan-", {"k.py": "X = 1\nOLD = 2\n"})
    try:
        rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
        check(rows["X"]["origin"] == "scan" and rows["OLD"]["origin"] == "scan",
              "kilo-scan: baseline rows start as scan")

        s = process_kilo_payload(_payload(
            "Edit", root, root / "k.py", session="ks",
            old_string="X = 1", new_string="X = 1"))
        check(s["handled"] and s["updated"] == 1,
              f"kilo-scan: kilo edit claims X ({s})")
        rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
        check(rows["X"]["origin"] == "kilo"
              and rows["X"]["last_session"] == "kilo:ks",
              "kilo-scan: X row claimed by kilo")

        # OLD deleted from disk; rescan must preserve the kilo baseline on X
        # and still prune the now-gone scan-origin OLD row
        (root / "k.py").write_text("X = 1\n", encoding="utf-8")
        scan.scan_project(root)
        rows = {r["name"]: r for r in store.open_store(root).query(limit=100)}
        check(rows["X"]["origin"] == "kilo",
              "kilo-scan: rescan preserves kilo baseline")
        check("OLD" not in rows, "kilo-scan: scan-only row pruned after deletion")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_kilo_report_counts():
    root = _make_repo("varmem-kilo-rep-", {"r.py": "R_ONE = 1\nR_TWO = 2\n"})
    try:
        f = root / "r.py"
        process_kilo_payload(_payload(
            "Edit", root, f, session="krep",
            old_string="R_ONE = 1", new_string="R_ONE = 1"))
        data = report.report_data(root)
        check(data["counts"]["kilo"] >= 1,
              f"report: kilo count present ({data['counts']})")
        check("claude" in data["counts"] and "scan" in data["counts"],
              "report: claude and scan counts still present")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_init_kilo_idempotent():
    from varmem import cli
    import io
    root = Path(tempfile.mkdtemp(prefix="varmem-kilo-init-"))
    try:
        plugin = root / ".kilo" / "plugins" / "varmem.ts"
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = cli.main(["--project", str(root), "init", "--kilo"])
        finally:
            sys.stdout = old_stdout
        check(rc == 0 and plugin.exists(), "init --kilo: plugin file created")
        text1 = plugin.read_text(encoding="utf-8")
        check("varmem.py" in text1 and "capture-kilo" in text1
              and "varmem init --kilo" in text1,
              "init --kilo: plugin content contains expected markers")

        sys.stdout = io.StringIO()
        try:
            rc2 = cli.main(["--project", str(root), "init", "--kilo"])
        finally:
            sys.stdout = old_stdout
        text2 = plugin.read_text(encoding="utf-8")
        check(rc2 == 0 and text2 == text1,
              "init --kilo: second call is idempotent")

        plugin.write_text("// hand written\n", encoding="utf-8")
        sys.stdout = io.StringIO()
        try:
            rc3 = cli.main(["--project", str(root), "init", "--kilo"])
        finally:
            sys.stdout = old_stdout
        check(rc3 == 1 and plugin.read_text(encoding="utf-8")
              == "// hand written\n",
              "init --kilo: refuses to overwrite a hand-authored file")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_capture_kilo_cli_never_raises():
    from varmem import cli
    import io

    class _FakeStdin(io.StringIO):
        def isatty(self):
            return False

    old_stdin, old_stdout = sys.stdin, sys.stdout
    try:
        sys.stdin = _FakeStdin("{not json")
        sys.stdout = io.StringIO()
        rc1 = cli.main(["capture-kilo"])
        check(rc1 == 0, "capture-kilo cli: broken JSON on stdin never raises")

        sys.stdin = _FakeStdin('{"foo": 1}')
        sys.stdout = io.StringIO()
        rc2 = cli.main(["capture-kilo"])
        check(rc2 == 0, "capture-kilo cli: wrong-shape JSON returns 0")

        # a non-string session_id is the input that used to raise on .startswith
        sys.stdin = _FakeStdin('{"session_id": 5, "tool_name": "Write"}')
        sys.stdout = io.StringIO()
        rc3 = cli.main(["capture-kilo"])
        check(rc3 == 0, "capture-kilo cli: non-string session_id never raises")
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout


def test_init_kilo_marker_and_settings():
    """#3: a hand-authored plugin that merely MENTIONS `varmem init --kilo` is
    not clobbered — only our @generated sentinel authorizes a rewrite. #5: a
    refused plugin does not block the independent --write-settings merge."""
    from varmem import cli
    import io
    root = Path(tempfile.mkdtemp(prefix="varmem-kilo-mrk-"))
    try:
        plugin = root / ".kilo" / "plugins" / "varmem.ts"
        plugin.parent.mkdir(parents=True, exist_ok=True)
        hand = ("// see docs: run `varmem init --kilo` to enable\n"
                "export const X = 1;\n")
        plugin.write_text(hand, encoding="utf-8")
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = cli.main(["--project", str(root), "init", "--kilo",
                           "--write-settings"])
        finally:
            sys.stdout = old
        check(plugin.read_text(encoding="utf-8") == hand and rc == 1,
              "init --kilo: file mentioning the phrase (no sentinel) is kept")
        sp = root / ".claude" / "settings.json"
        check(sp.exists() and "capture" in sp.read_text(encoding="utf-8"),
              "init --kilo --write-settings: settings written despite refuse")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_store_file_lock():
    """The cross-process capture lock must acquire/release cleanly, be
    re-acquirable (no self-deadlock), and never break a capture write."""
    root = Path(tempfile.mkdtemp(prefix="varmem-lock-"))
    try:
        with store.file_lock(root):
            pass
        with store.file_lock(root, timeout=0.2):  # re-acquire after release
            pass
        check(store._lock_path(root).exists(),
              "store: file_lock creates its lock file outside .varmem")
        check(not (root / config.VARMEM_DIRNAME).joinpath(".lock").exists(),
              "store: lock file is not written inside the committed store")
        # a capture routes through the lock; it must still persist the write
        f = root / "a.py"
        f.write_text("LOCK_VAR = 1\n", encoding="utf-8")
        s = process_kilo_payload(_payload("Write", root, f, session="lk"))
        check(s["handled"] and s["added"] == 1,
              f"store: capture under lock persists ({s})")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------- registry v2 / groups --

@contextlib.contextmanager
def _temp_registry():
    """Point varmem.repos at a scratch registry file; restore afterwards."""
    old = repos.REGISTRY
    tmp = Path(tempfile.mkdtemp(prefix="varmem-reg-"))
    repos.REGISTRY = tmp / "repos.json"
    try:
        yield repos.REGISTRY
    finally:
        repos.REGISTRY = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_registry_migration():
    with _temp_registry() as reg_path:
        a = tempfile.mkdtemp(prefix="varmem-repoA-")
        b = tempfile.mkdtemp(prefix="varmem-repoB-")
        try:
            # v1 flat list reads transparently, without writing anything
            reg_path.write_text(json.dumps([a, b]), encoding="utf-8")
            v1_bytes = reg_path.read_bytes()
            paths = repos.list_repos()
            check(len(paths) == 2 and paths[0] == repos.canon_path(a),
                  "registry: v1 list readable via list_repos")
            check(reg_path.read_bytes() == v1_bytes,
                  "registry: pure read leaves v1 file untouched")

            # first mutation migrates to v2 with a one-time backup
            c = tempfile.mkdtemp(prefix="varmem-repoC-")
            repos.add_repo(c)
            bak = reg_path.parent / (reg_path.name + ".v1.bak")
            check(bak.exists() and bak.read_text(encoding="utf-8").strip()
                  == json.dumps(json.loads(v1_bytes), indent=1).strip(),
                  "registry: v1 backup preserved before migration")
            on_disk = json.loads(reg_path.read_text(encoding="utf-8"))
            check(on_disk.get("version") == 2
                  and len(on_disk["repos"]) == 3
                  and on_disk["groups"] == [],
                  "registry: migrated file is v2")

            # ids are stable for the same canonical path, across reorders
            ida = repos.repo_id_for(a)
            check(ida == repos.repo_id_for(Path(a))
                  and ida == repos.repo_id_for(a.replace("\\", "/")),
                  "registry: repo id stable across path spellings")
            on_disk["repos"].reverse()
            reg_path.write_text(json.dumps(on_disk), encoding="utf-8")
            check(repos.get_repo(ida)["path"] == repos.canon_path(a),
                  "registry: id lookup survives reordering")

            # round trip: unchanged after load+save cycle
            before = repos.load_registry()
            repos.add_repo(a)  # no-op mutation, still persists
            check(repos.load_registry()["repos"] == before["repos"],
                  "registry: idempotent add round-trips")
            shutil.rmtree(c, ignore_errors=True)
        finally:
            shutil.rmtree(a, ignore_errors=True)
            shutil.rmtree(b, ignore_errors=True)


def test_registry_malformed():
    with _temp_registry() as reg_path:
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text("{this is not json", encoding="utf-8")
        garbage = reg_path.read_bytes()
        check(repos.list_repos() == [], "registry: malformed read tolerated")
        raised = False
        try:
            repos.add_repo(tempfile.gettempdir())
        except repos.RegistryError as e:
            raised = "refusing to overwrite" in str(e)
        check(raised, "registry: mutation on malformed file raises")
        check(reg_path.read_bytes() == garbage,
              "registry: malformed file preserved byte-for-byte")

        reg_path.write_text(json.dumps({"version": 99, "what": 1}),
                            encoding="utf-8")
        raised = False
        try:
            repos.load_registry()
        except repos.RegistryError:
            raised = True
        check(raised, "registry: unknown structure rejected loudly")


def test_groups_crud():
    with _temp_registry():
        a = tempfile.mkdtemp(prefix="varmem-ga-")
        b = tempfile.mkdtemp(prefix="varmem-gb-")
        try:
            repos.add_repo(a)
            repos.add_repo(b)
            ida, idb = repos.repo_id_for(a), repos.repo_id_for(b)

            g = repos.create_group("Trading platform", [ida],
                                   compose_projects=["trading"])
            check(g["id"].startswith("g-") and g["confirmed"]
                  and g["repo_ids"] == [ida],
                  "groups: create with membership")
            check(repos.standalone_repo_ids() == [idb],
                  "groups: standalone excludes grouped repos")

            gid = g["id"]
            g2 = repos.rename_group(gid, "Trading stack")
            check(g2["id"] == gid and g2["name"] == "Trading stack",
                  "groups: rename keeps stable id")
            repos.add_repo_to_group(gid, idb)
            check(set(repos.get_group(gid)["repo_ids"]) == {ida, idb},
                  "groups: add member")

            # a repo may belong to more than one group
            g3 = repos.create_group("Second view", [idb])
            check(idb in repos.get_group(gid)["repo_ids"]
                  and idb in repos.get_group(g3["id"])["repo_ids"],
                  "groups: multi-group membership allowed")

            raised = False
            try:
                repos.create_group("bad", ["r-nonexistent"])
            except ValueError:
                raised = True
            check(raised, "groups: unknown repo id rejected")

            repos.remove_repo_from_group(gid, ida)
            check(repos.get_group(gid)["repo_ids"] == [idb],
                  "groups: remove member")
            # removing a repo from the registry cleans group refs
            repos.remove_repo(b)
            check(repos.get_group(gid)["repo_ids"] == [],
                  "groups: registry removal cleans memberships")
            check(repos.delete_group(gid)
                  and repos.get_group(gid) is None,
                  "groups: delete")
            check(repos.find_group("Second view")["id"] == g3["id"],
                  "groups: find by name")
        finally:
            shutil.rmtree(a, ignore_errors=True)
            shutil.rmtree(b, ignore_errors=True)


# ------------------------------------------------------ container discovery --

def test_discovery():
    with _temp_registry() as reg_path:
        a = tempfile.mkdtemp(prefix="varmem-da-")
        b = tempfile.mkdtemp(prefix="varmem-db-")
        try:
            repos.add_repo(a)
            repos.add_repo(b)
            ida, idb = repos.repo_id_for(a), repos.repo_id_for(b)

            # docker CLI missing -> clean degradation
            orig_which = discovery.shutil.which
            discovery.shutil.which = lambda _: None
            try:
                res = discovery.discover()
            finally:
                discovery.shutil.which = orig_which
            check(res["docker"] == "unavailable"
                  and res["suggestions"] == [],
                  "discovery: docker missing degrades cleanly")

            # docker erroring -> structured error, no crash
            def broken(cmd):
                raise discovery.DiscoveryError("daemon not running")
            res = discovery.discover(runner=broken)
            check(res["docker"] == "error" and "daemon" in res["error"],
                  "discovery: docker error surfaces structurally")

            # mocked compose project with bind mounts onto both repos
            inspect_payload = [
                {"Id": "c1", "Name": "/trading-api-1",
                 "Config": {"Labels": {
                     discovery.COMPOSE_PROJECT_LABEL: "trading"}},
                 "Mounts": [{"Type": "bind", "Source": a,
                             "Destination": "/app"}]},
                {"Id": "c2", "Name": "/trading-worker-1",
                 "Config": {"Labels": {
                     discovery.COMPOSE_PROJECT_LABEL: "trading"}},
                 "Mounts": [{"Type": "bind",
                             "Source": str(Path(b) / "src"),
                             "Destination": "/work"}]},
                {"Id": "c3", "Name": "/unrelated",
                 "Config": {"Labels": {}},
                 "Mounts": [{"Type": "bind", "Source": "/nowhere/else",
                             "Destination": "/x"}]},
            ]

            def fake_runner(cmd):
                if cmd[:3] == ["docker", "ps", "-q"]:
                    return "c1\nc2\nc3\n"
                if cmd[:2] == ["docker", "inspect"]:
                    return json.dumps(inspect_payload)
                raise AssertionError(f"unexpected cmd {cmd}")

            before_bytes = reg_path.read_bytes()
            res = discovery.discover(runner=fake_runner)
            check(res["docker"] == "ok" and len(res["suggestions"]) == 1,
                  f"discovery: compose suggestion produced ({res})")
            sug = res["suggestions"][0]
            check(sug["name"] == "trading"
                  and set(sug["repo_ids"]) == {ida, idb}
                  and sug["compose_projects"] == ["trading"]
                  and not sug["already_grouped"],
                  "discovery: suggestion maps mounts to registered repos")
            check(reg_path.read_bytes() == before_bytes,
                  "discovery: suggestions never mutate the registry")

            # explicit confirmation persists a confirmed group
            g = discovery.confirm_suggestion(sug)
            check(g["confirmed"] and set(g["repo_ids"]) == {ida, idb},
                  "discovery: confirm creates confirmed group")
            g2 = discovery.confirm_suggestion(sug)
            check(g2["id"] == g["id"] and len(repos.group_entries()) == 1,
                  "discovery: re-confirm is idempotent")
            res2 = discovery.discover(runner=fake_runner)
            check(res2["suggestions"][0]["already_grouped"],
                  "discovery: existing membership marked already_grouped")

            # containers stopped -> confirmed group survives untouched
            res3 = discovery.discover(runner=lambda cmd: ""
                                      if cmd[:3] == ["docker", "ps", "-q"]
                                      else "[]")
            check(res3["docker"] == "ok" and res3["suggestions"] == []
                  and repos.get_group(g["id"])["repo_ids"],
                  "discovery: confirmed group survives stopped containers")
        finally:
            shutil.rmtree(a, ignore_errors=True)
            shutil.rmtree(b, ignore_errors=True)


# ------------------------------------------------- prompts + repo isolation --

def _make_repo(prefix: str, files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    for rel, src in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    scan.scan_project(root)
    return root


def test_prompts_and_isolation():
    with _temp_registry():
        # sentinel-loaded twin repos: findings must never cross the boundary
        ra = _make_repo("varmem-isoA-", {
            "a1.py": 'ALPHA_TOKEN_LIMIT = 4141\n'
                     'AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"\n',
            "a2.py": "ALPHA_TOKEN_LIMIT = 4242\n",
        })
        rb = _make_repo("varmem-isoB-", {
            "b1.py": "BRAVO_RATE_CAP = 9191\n",
            "b2.py": "BRAVO_RATE_CAP = 9292\n",
        })
        try:
            # duplicate scoring is per-repo by construction: each store only
            # ever holds its own rows
            dups_a = duplicates.find_duplicates(store.open_store(ra))
            check(dups_a and all(
                "ALPHA" in d["a"]["name"] and "ALPHA" in d["b"]["name"]
                for d in dups_a
                if "TOKEN" in d["a"]["name"]),
                "isolation: repo A pairs stay inside repo A")
            all_names_a = {d[s]["name"] for d in dups_a for s in ("a", "b")}
            check("BRAVO_RATE_CAP" not in all_names_a,
                  "isolation: no cross-repo duplicate pairs")

            # prompt determinism: same input + pinned timestamp -> identical
            p1, _ = prompts.repo_prompt(ra, generated="2026-07-15T00:00:00")
            p2, _ = prompts.repo_prompt(ra, generated="2026-07-15T00:00:00")
            check(p1 is not None and p1 == p2, "prompt: deterministic output")

            # ordering is stable and repo-scoped
            check("ALPHA_TOKEN_LIMIT" in p1 and str(ra.name) in p1,
                  "prompt: contains own repo evidence")
            check("BRAVO_RATE_CAP" not in p1,
                  "prompt: other repo's sentinel absent")
            pb, _ = prompts.repo_prompt(rb, generated="x")
            check("ALPHA_TOKEN_LIMIT" not in pb
                  and "BRAVO_RATE_CAP" in pb,
                  "prompt: repo B scoped to repo B")

            # redaction: raw secret and its sha1 fragment never appear
            pa_all, _ = prompts.repo_prompt(
                ra, min_level="low", statuses=list(prompts.STATUS_KINDS),
                include_reviewed=True, generated="x")
            joined = p1 + (pa_all or "")
            check("AKIA" not in joined, "prompt: raw secret never included")
            check("sha1" not in joined.lower(),
                  "prompt: no hash substitutes for redacted values")

            # drift finding carries both redacted projections
            (ra / "a1.py").write_text(
                'ALPHA_TOKEN_LIMIT = 5555\n'
                'AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
            reconcile.reconcile_project(ra, force=True)
            pd, _ = prompts.repo_prompt(ra, generated="x")
            check("drifted" in pd and "5555" in pd,
                  "prompt: drift section shows repo-side value")

            # required guardrail text is present
            for needle in ("inspect the actual code", "Do NOT blindly",
                           "`.varmem/`", "Stop conditions",
                           "Final report format", "Evidence files",
                           "unrelated changes"):
                check(needle in pd, f"prompt: guardrail present ({needle})")

            # empty selection is skipped with a reason
            empty = Path(tempfile.mkdtemp(prefix="varmem-isoE-"))
            try:
                p, reason = prompts.repo_prompt(empty)
                check(p is None and "store" in reason,
                      "prompt: storeless repo skipped with reason")
                scan.scan_project(empty)
                p, reason = prompts.repo_prompt(empty)
                check(p is None and "selection" in reason,
                      "prompt: empty selection skipped with reason")
            finally:
                shutil.rmtree(empty, ignore_errors=True)
        finally:
            shutil.rmtree(ra, ignore_errors=True)
            shutil.rmtree(rb, ignore_errors=True)


def test_scan_and_duplicates():
    root = Path(tempfile.mkdtemp(prefix="varmem-dup-"))
    try:
        src = root / "src"
        src.mkdir(parents=True)
        (src / "a.py").write_text(
            "MAX_RETRIES = 5\n"
            'DB_CONN_STRING = "postgres://db.internal:5432/prod"\n',
            encoding="utf-8")
        (src / "b.py").write_text("RETRY_LIMIT = 5\n", encoding="utf-8")
        (src / "c.py").write_text("max_retries = 8\n", encoding="utf-8")
        (src / "c2.py").write_text("maxRetries = 7\n", encoding="utf-8")
        (src / "d.py").write_text("MAX_RETRIES = 9\n", encoding="utf-8")
        (src / "e.py").write_text(
            'DATABASE_URL = "postgres://db.internal:5432/prod"\n',
            encoding="utf-8")
        (src / "e2.py").write_text(
            '_BASE_URL = "https://svc.internal"\n'
            "base_url = _BASE_URL\n"
            "TIMEOUT = 30\n"
            "timeout = 99\n",
            encoding="utf-8")
        (src / "f2.py").write_text(
            "from typing import Literal\n"
            'Severity = Literal["critical", "warning"]\n'
            "class Model:\n"
            '    severity: Severity = "warning"\n',
            encoding="utf-8")
        (src / "h1.py").write_text(
            '_PRODUCTS = ["greenyoga", "yacp"]\n', encoding="utf-8")
        (src / "h2.py").write_text(
            'PRODUCTS = ["greenyoga"]\n', encoding="utf-8")
        (src / "g1.py").write_text('DSN = "postgres://db-one/x"\n',
                                   encoding="utf-8")
        (src / "g2.py").write_text('DSN = "postgres://db-two/y"\n',
                                   encoding="utf-8")
        (src / "p1.ps1").write_text('$ConnHost = "db.internal.example"\n',
                                    encoding="utf-8")
        (src / "p2.ps1").write_text('$connhost = "db.internal.example"\n',
                                    encoding="utf-8")
        (src / "f.py").write_text(
            "import logging\nlogger = logging.getLogger(__name__)\n",
            encoding="utf-8")
        (src / "g.py").write_text(
            "import logging\nlogger = logging.getLogger(__name__)\n",
            encoding="utf-8")

        # a.py is Claude-written; the rest is pre-existing repo code
        process_payload(_payload("Write", root, src / "a.py",
                                 session="sess-A"))
        totals = scan.scan_project(root)
        check(totals["files"] >= 7 and totals["added"] >= 5,
              f"scan: baseline ingested ({totals})")

        st = store.open_store(root)
        if True:
            rows = {r["name"]: r for r in st.query(limit=100)}
            check(rows["MAX_RETRIES"]["origin"] == "claude"
                  and rows["MAX_RETRIES"]["last_session"] == "sess-A",
                  "scan: does not steal claude provenance")
            check(rows["RETRY_LIMIT"]["origin"] == "scan",
                  "scan: baseline rows origin=scan")

            dups = duplicates.find_duplicates(st)
            by_reason = {}
            for d in dups:
                pair = {d["a"]["name"], d["b"]["name"]}
                by_reason.setdefault(d["reason"], []).append(
                    (d["level"], pair))
            check(any(p == {"max_retries", "maxRetries"} and lvl == "high"
                      for lvl, p in by_reason.get("name-variant", [])),
                  f"dup: same-role name-variant stays high ({by_reason})")
            check(any(p == {"MAX_RETRIES", "max_retries"} and lvl == "medium"
                      for lvl, p in by_reason.get("case-variant", [])),
                  "dup: cross-file CONST-vs-var demoted to medium")
            all_pairs = [{d["a"]["name"], d["b"]["name"]} for d in dups]
            check({"TIMEOUT", "timeout"} not in all_pairs,
                  "lang: same-file CONST-vs-var suppressed (convention)")
            check(any(p == {"_BASE_URL", "base_url"} and lvl == "low"
                      for lvl, p in by_reason.get("alias-derived", [])),
                  "dup: RHS-references-twin classified alias-derived low")
            check(any(p == {"Severity", "severity"} and lvl == "low"
                      for lvl, p in by_reason.get("alias-derived", [])),
                  "lang: typed field links to its Literal via annotation")
            check(any(p == {"MAX_RETRIES"} and lvl == "high"
                      for lvl, p in by_reason.get("value-mismatch", [])),
                  "dup: same name different value cross-file = HIGH")
            check(any(p == {"_PRODUCTS", "PRODUCTS"} and lvl == "high"
                      for lvl, p in by_reason.get("name-variant", [])),
                  "dup: cross-file same-role underscore twin stays high")
            check({"DSN"} not in all_pairs,
                  "lang: per-script convention name (DSN) not paired cross-file")
            check(any(p == {"ConnHost", "connhost"}
                      for _, p in by_reason.get("same-name-same-value", []))
                  and not any(p == {"ConnHost", "connhost"}
                              for _, p in by_reason.get("name-variant", [])),
                  "lang: PowerShell case pair treated as SAME variable")
            check(any(p == {"DB_CONN_STRING", "DATABASE_URL"}
                      for _, p in by_reason.get("same-value", [])),
                  "dup: same value under different names")
            check(any(p == {"MAX_RETRIES", "RETRY_LIMIT"}
                      for _, p in by_reason.get("shared-token", [])),
                  "dup: MAX_RETRIES/RETRY_LIMIT related")
            check(not any({"logger"} == {d["a"]["name"], d["b"]["name"]}
                          for d in dups),
                  "dup: idiomatic logger pair ignored")

            # override + dismiss
            target = next(d for d in dups if d["reason"] == "name-variant")
            st.set_review(target["pair_key"], "not_duplicate",
                          "intentional: script-local lowercase copy")
            dups2 = duplicates.find_duplicates(st)
            check(all(d["pair_key"] != target["pair_key"] for d in dups2),
                  "dup: dismissed pair excluded")
            dups3 = duplicates.find_duplicates(st, include_dismissed=True)
            rev = next(d for d in dups3
                       if d["pair_key"] == target["pair_key"])
            check(rev["review"]["note"].startswith("intentional"),
                  "dup: review note persisted")

            n = st.set_var_note(name="MAX_RETRIES", file="a.py",
                                note="canonical retry count")
            check(n == 1, "annotate: one row noted")
            st.save()
            fresh = store.open_store(root)
            check(fresh.reviews.get(target["pair_key"], {}).get("verdict")
                  == "not_duplicate",
                  "store: review persisted to reviews.json")
            check(any(r["note"] == "canonical retry count"
                      for r in fresh.query("MAX_RETRIES")),
                  "store: note persisted to shard file")

        out = report.build_report(root)
        html = out.read_text(encoding="utf-8")
        check(out.exists() and "varmem report" in html
              and "MAX_RETRIES" in html and "name-variant" in html
              and "canonical retry count" in html,
              "report: HTML contains vars, dups, notes")
        check("https://" not in html.split("</style>")[0]
              and "@import" not in html and "<link" not in html,
              "report: self-contained, no network references")
        for hook in ('<meta name="viewport"', "@media (max-width:900px)",
                     "aria-label", ":focus-visible", 'role="tablist"'):
            check(hook in html, f"report: a11y/responsive hook ({hook})")

        ctx = context.build_context(root)
        check("Duplicate-variable suspects" in ctx,
              "context: duplicate summary line present")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_duplicate_tuning():
    """Dogfood-driven precision fixes: idiomatic no-arg constructors, bare
    aliases, single generic tokens, receiver-token overlap, and numeric-suffix
    siblings must NOT reach the medium tier, while real signals survive."""
    root = _make_repo("varmem-tune-", {
        "svc/a.py": (
            "import threading\n"
            "_cache_lock = threading.Lock()\n"
            "do_GET = _dispatch\n"
            "do_POST = _dispatch\n"
            "MAX_ITEMS = 100\n"
            'ALPHA_STORE = "postgres://db.internal:5432/prod"\n'
        ),
        "svc/b.py": (
            "import threading\n"
            "_locks_guard = threading.Lock()\n"
            "do_alias = _dispatch\n"
            "items = load_items()\n"
            'BETA_CACHE = "postgres://db.internal:5432/prod"\n'
        ),
        "svc/c.py": (
            "class S:\n"
            "    def __init__(self):\n"
            "        self.reviews = {}\n"
            "        self.reviews_dirty = False\n"
            "    def run(self):\n"
            "        payload_data = build(1)\n"
            "        payload_data2 = build(2)\n"
        ),
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root))
        medplus = {frozenset((d["a"]["name"], d["b"]["name"]))
                   for d in dups if d["level"] in ("high", "medium")}
        check(frozenset(("_cache_lock", "_locks_guard")) not in medplus,
              "tune: no-arg constructor is not a meaningful shared value")
        check(not any("do_GET" in p or "do_POST" in p or "do_alias" in p
                      for p in medplus),
              "tune: bare-alias dispatch handlers not flagged")
        check(frozenset(("MAX_ITEMS", "items")) not in medplus,
              "tune: single generic token is not token-overlap")
        check(not any("self.reviews" in n for p in medplus for n in p),
              "tune: receiver-token (self) overlap dropped")
        check(frozenset(("payload_data", "payload_data2")) not in medplus,
              "tune: numeric-suffix siblings suppressed")
        check(any(d["reason"] == "same-value"
                  and frozenset((d["a"]["name"], d["b"]["name"]))
                  == frozenset(("ALPHA_STORE", "BETA_CACHE"))
                  for d in dups),
              "tune: real cross-file same-value signal preserved")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fp_false_positive_suppression():
    """Dogfood 2026-07-16: member-assignment targets (obj.attr = …), names that
    share only generic scaffolding tokens (data/uri/dirname), and generic
    transient names (text/url) must never surface as duplicates."""
    root = _make_repo("varmem-fp-", {
        "ext/a.py": (
            "statusBar = make_status()\n"
            "statusBar.command = 'focus'\n"
            "statusBar.text = 'VarAlign'\n"
            "statusBar.tooltip = 'open'\n"
            "FAVICON_DATA_URI = 'data:image/png;base64,AAAAipmn0000zzzz'\n"
            "LOGO_DATA_URI = 'data:image/png;base64,BBBBxyzq1111wwww'\n"
            "text = fetch_left_side_payload()\n"
        ),
        "ext/b.py": (
            "text = render_right_side_output()\n"
            "url = build_target_endpoint()\n"
        ),
        "ext/c.py": (
            "url = compute_other_endpoint()\n"
        ),
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root))
        pairs = {frozenset((d["a"]["name"], d["b"]["name"]))
                 for d in dups if d["level"] in ("high", "medium")}
        names = {n for p in pairs for n in p}
        check(not any("." in n for n in names),
              "fp: member-assignment targets (statusBar.command) never paired")
        check("statusBar" not in names,
              "fp: object base not paired against its own attributes")
        check(frozenset(("FAVICON_DATA_URI", "LOGO_DATA_URI")) not in pairs,
              "fp: names sharing only generic tokens (data,uri) not overlap")
        check(frozenset(("text",)) not in pairs,
              "fp: generic transient name 'text' not flagged")
        check(frozenset(("url",)) not in pairs,
              "fp: generic transient name 'url' not flagged")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_attribute_and_env_noise_suppression():
    """Dogfood 2026-07-17 (production repos): SVG/HTML
    attribute names, coincidental bare numbers, .env/.env.example key mirrors, and
    identical env-var reads are scanner noise, not drift. None may reach high/
    medium; the env-read link is discoverable only at --min-level low."""
    root = _make_repo("varmem-noise-", {
        # SVG presentation attrs + href/metadata as captured by older/markup-ish
        # stores (the live JSX extractor already skips them; the ignore list is
        # belt-and-suspenders for stores captured before that landed)
        "ui/a.py": (
            'strokeLinecap = "round"\n'
            'strokeLinejoin = "round"\n'
            'href = "/contact"\n'
            "metadata = build_stripe_meta()\n"
            "MAX_OUTPUT_CHARS = 4000\n"
        ),
        "ui/b.py": (
            'strokeLinecap = "round"\n'
            'strokeLinejoin = "round"\n'
            'href = "/privacy"\n'
            "metadata = build_page_meta()\n"
            "MAX_DESCRIPTION_CHARS = 4000\n"
        ),
        # per-module env accessors reading the SAME key+default in isolated
        # standalone units — the env var is the single source, not a copy-paste
        "svc/one.py": (
            "import os\n"
            'LOG_FILE = os.environ.get("VARMEM_LOG", "/var/log/vm.log")\n'
        ),
        "svc/two.py": (
            "import os\n"
            'LOG_FILE = os.environ.get("VARMEM_LOG", "/var/log/vm.log")\n'
        ),
        # committed .env seed mirroring the live .env key-for-key
        ".env": "API_TOKEN=set-me-in-production\nSERVICE_PORT=48022\n",
        ".env.example": "API_TOKEN=\nSERVICE_PORT=48022\n",
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        medplus = {frozenset((d["a"]["name"], d["b"]["name"]))
                   for d in dups if d["level"] in ("high", "medium")}
        allpairs = {frozenset((d["a"]["name"], d["b"]["name"])) for d in dups}
        for attr in ("strokeLinecap", "strokeLinejoin", "href", "metadata"):
            check(frozenset((attr,)) not in allpairs,
                  f"noise: SVG/HTML attribute name '{attr}' never pairs")
        check(not any(d["reason"].startswith("same-value")
                      and {"MAX_OUTPUT_CHARS", "MAX_DESCRIPTION_CHARS"}
                      == {d["a"]["name"], d["b"]["name"]} for d in dups),
              "noise: coincidental bare number 4000 does not anchor same-value")
        env = [d for d in dups
               if {"LOG_FILE"} == {d["a"]["name"], d["b"]["name"]}]
        check(env and all(d["level"] == "low"
                          and d["reason"] == "shared-env-source" for d in env)
              and frozenset(("LOG_FILE",)) not in medplus,
              f"noise: identical env-var read is a shared source, sinks to low ({env})")
        check(frozenset(("API_TOKEN",)) not in allpairs
              and frozenset(("SERVICE_PORT",)) not in allpairs,
              f"noise: .env/.env.example key mirror never pairs ({allpairs})")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_standalone_units_config():
    """Repo-level standalone_units: assignments in DIFFERENT declared isolated
    units are independent by architecture, so cross-unit same-value constants
    and value-mismatch tuning knobs are suppressed — while WITHIN-unit dedup
    signals and pairs OUTSIDE any unit still surface (dogfood 2026-07-17,
    a production repo: 1,118 copy-paste constants across standalone-deployable
    handlers that cannot share a module)."""
    root = _make_repo("varmem-units-", {
        ".varmem/config.json": json.dumps({"standalone_units": ["handlers/*"]}),
        # same constant copy-pasted across two isolated handler units, plus a
        # per-unit tuning knob that legitimately differs
        "handlers/alpha/app.py":
            'TASK_STORE = "redis://localhost:6379/0"\nRETRY_MAX = 5\n',
        "handlers/bravo/app.py":
            'TASK_STORE = "redis://localhost:6379/0"\nRETRY_MAX = 9\n',
        # a second file WITHIN one unit — a real dedup opportunity, still flagged
        "handlers/alpha/worker.py": 'TASK_STORE = "redis://localhost:6379/0"\n',
        # shared code OUTSIDE any declared unit — normal detection applies
        "common/one.py": 'SHARED_FLAG = "enabled-and-active"\n',
        "common/two.py": 'SHARED_FLAG = "enabled-and-active"\n',
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        triples = [(frozenset((d["a"]["name"], d["b"]["name"])),
                    frozenset((d["a"]["file"], d["b"]["file"]))) for d in dups]

        def paired(nameset, files):
            return (frozenset(nameset), frozenset(files)) in triples

        check(not paired({"TASK_STORE"},
                         {"handlers/alpha/app.py", "handlers/bravo/app.py"}),
              "units: cross-unit same-value constant suppressed")
        check(not any(p[0] == frozenset({"RETRY_MAX"}) for p in triples),
              "units: cross-unit value-mismatch tuning knob suppressed")
        check(paired({"TASK_STORE"},
                     {"handlers/alpha/app.py", "handlers/alpha/worker.py"}),
              "units: WITHIN-unit same-value dedup still flagged")
        check(any(p[0] == frozenset({"SHARED_FLAG"}) for p in triples),
              "units: pairs outside any declared unit still flagged")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_auto_detect_standalone_units():
    """Dogfood 2026-07-17: a multi-component repo (>=2 top-level dirs each with a
    deploy/package manifest) auto-suppresses cross-component constant copies with
    NO config; a single-package repo is unaffected (cross-dir copies still show)."""
    # multi-component: worker/ (wrangler.toml) + ext/ (package.json) each copy a
    # runtime constant — expected across independently-shipped subtrees
    multi = _make_repo("varmem-autounit-", {
        "worker/wrangler.toml": "name = 'edge'\n",
        "worker/index.ts": 'const RUNTIME_TAG = "shared-runtime-constant";\n',
        "ext/package.json": '{"name": "ext"}\n',
        "ext/client.ts": 'const RUNTIME_TAG = "shared-runtime-constant";\n',
    })
    try:
        pairs = {frozenset((d["a"]["name"], d["b"]["name"]))
                 for d in duplicates.find_duplicates(store.open_store(multi),
                                                     include_dismissed=True)}
        check(frozenset(("RUNTIME_TAG",)) not in pairs,
              f"auto-units: cross-component copy suppressed with no config ({pairs})")
    finally:
        shutil.rmtree(multi, ignore_errors=True)

    # single-component: only a root manifest -> heuristic inactive -> a real
    # cross-directory duplicate still surfaces
    single = _make_repo("varmem-single-", {
        "package.json": '{"name": "root"}\n',
        "a/one.ts": 'const RUNTIME_TAG = "shared-runtime-constant";\n',
        "b/two.ts": 'const RUNTIME_TAG = "shared-runtime-constant";\n',
    })
    try:
        pairs = {frozenset((d["a"]["name"], d["b"]["name"]))
                 for d in duplicates.find_duplicates(store.open_store(single),
                                                     include_dismissed=True)}
        check(frozenset(("RUNTIME_TAG",)) in pairs,
              f"auto-units: single-component repo unaffected ({pairs})")
    finally:
        shutil.rmtree(single, ignore_errors=True)


def test_scan_prunes_out_of_scope_rows():
    """Dogfood 2026-07-17: baseline (scan-origin) rows for a file that later
    leaves scan scope — gitignored, excluded, or deleted — must be pruned
    AUTOMATICALLY on the next scan, so the drift report never accumulates
    unactionable entries for out-of-scope files (a gitignored build artifact
    left 672 orphaned file_deleted rows = 82% of the report)."""
    root = Path(tempfile.mkdtemp(prefix="varmem-prune-"))
    try:
        gen = root / "gen"
        gen.mkdir(parents=True)
        (root / "app.py").write_text("KEEP_CONST = 'in-scope-value'\n",
                                     encoding="utf-8")
        (gen / "bundled.py").write_text("ARTIFACT_CONST = 'built-value'\n",
                                        encoding="utf-8")
        scan.scan_project(root)
        st = store.open_store(root)
        check(any(r["file"] == "gen/bundled.py" for r in st.all_rows()),
              "prune: artifact scanned into baseline on first pass")
        # the dir is now gitignored (as a build artifact would be) and rescanned
        (root / ".gitignore").write_text("gen/\n", encoding="utf-8")
        totals = scan.scan_project(root)
        after = store.open_store(root)
        check(not any(r["file"] == "gen/bundled.py" for r in after.all_rows()),
              "prune: rows for a now-out-of-scope file auto-removed on rescan")
        check(totals["pruned"] >= 1, f"prune: pruned count reported ({totals})")
        check(any(r["file"] == "app.py" for r in after.all_rows()),
              "prune: in-scope file untouched")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_prompt_actionable_only():
    """Dogfood 2026-07-17: the remediation prompt is actionable-only — value
    DRIFT (a concrete conflict to reconcile), never removal churn. A gone
    variable (missing / file_deleted) is recall, not a defect to fix, so it is
    excluded by default and available only on an explicit --status."""
    root = _make_repo("varmem-actionable-", {
        "drift.py": "DRIFT_CONST = 'value-x'\n",
        "gone.py": "DELETED_CONST = 'value-y'\n",
        "moved.py": "MOVED_CONST = 'value-z'\n",
    })
    try:
        st = store.open_store(root)
        for r in st.all_rows():
            if r["file"] == "drift.py":
                st.mark_status(r, "drifted")
            elif r["file"] == "gone.py":
                st.mark_status(r, "file_deleted")
            elif r["file"] == "moved.py":
                st.mark_status(r, "missing")
        st.save()
        fresh = store.open_store(root)
        statuses = {r["status"]
                    for r in prompts.select_findings(fresh)["status_rows"]}
        check(statuses == {"drifted"},
              f"prompt: default remediation is drift-only ({statuses})")
        est = {r["status"] for r in
               prompts.select_findings(
                   fresh, statuses=["file_deleted", "missing"])["status_rows"]}
        check(est == {"file_deleted", "missing"},
              f"prompt: removal statuses available on explicit --status ({est})")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_prompt_records_verdicts():
    """Dogfood 2026-07-17 (a production repo: no varmem CLI on PATH, so the agent
    hand-wrote reviews.json and GUESSED the pair-key order/path form). The
    generated prompt must carry each pair's EXACT verdict key and instruct
    persisting it — with a CLI-optional reviews.json fallback — so dismissals
    are automatic and never reconstructed."""
    root = _make_repo("varmem-verdict-", {
        "a.py": "MAX_RETRIES = 5\n",
        "b.py": "max_retries = 8\n",  # cross-file role pair -> a suspect
    })
    try:
        text, reason = prompts.repo_prompt(root, min_level="low")
        check(text is not None, f"verdicts: prompt generated ({reason})")
        check("Recording your verdicts" in text,
              "verdicts: prompt includes the recording section")
        check("dup-note" in text and ".varmem/reviews.json" in text,
              "verdicts: prompt gives both the CLI and the no-CLI fallback")
        check("verdict key:" in text,
              "verdicts: each finding shows a verdict key")
        dups = duplicates.find_duplicates(store.open_store(root))
        check(bool(dups) and dups[0]["pair_key"] in text,
              "verdicts: the exact pair_key is embedded verbatim (no guessing)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_pair_key_normalized():
    """Dogfood 2026-07-17: a verdict recorded under a NON-canonical key (swapped
    A/B order, backslash path, padding) must still suppress the pair — a
    hand-written ledger must not silently miss."""
    bs = chr(92)  # backslash
    root = _make_repo("varmem-normkey-", {
        "sub/a.py": 'SHARED_DSN = "postgres://db:5432/prod"\n',
        "sub/b.py": 'SHARED_DSN = "postgres://db:5432/prod"\n',
    })
    try:
        st = store.open_store(root)
        d0 = next(d for d in duplicates.find_duplicates(st)
                  if {d["a"]["name"], d["b"]["name"]} == {"SHARED_DSN"})
        canonical = d0["pair_key"]
        check(canonical == "sub/a.py||SHARED_DSN||sub/b.py||SHARED_DSN",
              f"normkey: canonical key form ({canonical})")
        check(store.normalize_pair_key(
                  "sub/b.py||SHARED_DSN||sub/a.py||SHARED_DSN") == canonical,
              "normkey: swapped A/B order folds to canonical")
        check(store.normalize_pair_key(canonical.replace("/", bs)) == canonical,
              "normkey: backslash paths fold to canonical")
        # record under a deliberately messy key; the pair must then be gone
        messy = ("sub" + bs + "b.py||SHARED_DSN||sub" + bs
                 + "a.py||  SHARED_DSN  ")
        st.set_review(messy, "not_duplicate", "intentional (messy key)")
        st.save()
        still = [d for d in duplicates.find_duplicates(store.open_store(root))
                 if {d["a"]["name"], d["b"]["name"]} == {"SHARED_DSN"}]
        check(not still,
              "normkey: verdict under a non-canonical key suppresses the pair")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_prompt_batches_large_findings():
    """Dogfood 2026-07-17: a large finding set is chunked so an agent records
    verdicts a batch at a time and resumes — never all at once (where, at scale,
    some get dropped). limit=0 disables the cap."""
    files = {f"m{i}.py": 'SHARED_FLAG = "on-and-active-value"\n'
             for i in range(8)}  # 8 files, same const -> C(8,2)=28 pairs
    root = _make_repo("varmem-batch-", files)
    try:
        capped, _ = prompts.repo_prompt(root, min_level="low", limit=5)
        n = capped.count("### D")
        check(n <= 5, f"batch: at most `limit` suspects per pass ({n})")
        check("next batch" in capped and " of 28" in capped,
              "batch: capped prompt shows a resume banner with the total")
        full, _ = prompts.repo_prompt(root, min_level="low", limit=0)
        check(full.count("### D") > 5,
              f"batch: limit=0 shows the full set ({full.count('### D')})")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_accept_drift_rebaselines():
    """Dogfood 2026-07-17: a drifted variable from a completed refactor had no
    ledger to clear it, so it persisted every run. `accept` re-baselines it to
    the current repo value (active) and it drops from the prompt — but a LATER
    change re-drifts, so detection is not blinded."""
    root = Path(tempfile.mkdtemp(prefix="varmem-accept-"))
    try:
        f = root / "svc.py"
        f.write_text("HANDLER = old_impl_value()\n", encoding="utf-8")
        process_payload(_payload("Write", root, f))  # claude-origin row
        time.sleep(0.05)
        f.write_text("HANDLER = new_impl_value()\n", encoding="utf-8")  # refactor
        reconcile.reconcile_project(root, force=True)
        st = store.open_store(root)
        row = next(r for r in st.all_rows() if r["name"] == "HANDLER")
        check(row["status"] == "drifted",
              f"accept: precondition is drifted ({row['status']})")
        text, _ = prompts.repo_prompt(root, statuses=["drifted"])
        check(text and "varmem.py accept 'HANDLER'" in text,
              "accept: prompt shows an accept command per drift")
        check(st.accept_drift(row), "accept: returns True for a drifted row")
        st.save()
        row2 = next(r for r in store.open_store(root).all_rows()
                    if r["name"] == "HANDLER")
        check(row2["status"] == "active",
              "accept: row is active after re-baseline")
        check(row2["value_hash"] == row2["repo_value_hash"],
              "accept: Claude value now matches the current repo value")
        time.sleep(0.05)
        f.write_text("HANDLER = third_impl_value()\n", encoding="utf-8")
        reconcile.reconcile_project(root, force=True)
        row3 = next(r for r in store.open_store(root).all_rows()
                    if r["name"] == "HANDLER")
        check(row3["status"] == "drifted",
              "accept: a later change re-drifts (detection not blinded)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_accept_cli_pattern():
    """The `accept` CLI with a NAME pattern compiles a regex — cli.py must
    import `re`. Regression for a NameError that broke `varmem accept <name>`
    (the store-level accept test never exercised the CLI path)."""
    from varmem import cli
    import io
    root = Path(tempfile.mkdtemp(prefix="varmem-acceptcli-"))
    try:
        f = root / "svc.py"
        f.write_text("HANDLER = old_impl_value()\n", encoding="utf-8")
        process_payload(_payload("Write", root, f))
        time.sleep(0.05)
        f.write_text("HANDLER = new_impl_value()\n", encoding="utf-8")
        reconcile.reconcile_project(root, force=True)
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = cli.main(["--project", str(root), "accept", "HANDLER"])
        finally:
            sys.stdout = old
        check(rc == 0, f"accept-cli: `accept HANDLER` (pattern path) runs ({rc})")
        row = next(r for r in store.open_store(root).all_rows()
                   if r["name"] == "HANDLER")
        check(row["status"] == "active",
              "accept-cli: the pattern-matched drift was re-baselined")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_orm_column_boilerplate():
    """Dogfood 2026-07-16 (ai-models-pricing): sibling ORM columns that share
    only a type+constraint declaration — `mapped_column(Float, nullable=False)`
    — are boilerplate, not a shared value, and must never anchor a same-value /
    same-value-related-name pair. A genuine shared literal default still does."""
    root = _make_repo("varmem-orm-", {
        "models.py": (
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from sqlalchemy import Float, String, BigInteger\n"
            "class ModelPricingSnapshot(Base):\n"
            "    model_id: Mapped[str] = mapped_column(String(255), nullable=False)\n"
            "    name: Mapped[str] = mapped_column(String(255), nullable=False)\n"
            "    prompt_usd_per_mtok: Mapped[float] = mapped_column(Float, nullable=False)\n"
            "    completion_usd_per_mtok: Mapped[float] = mapped_column(Float, nullable=False)\n"
            '    request_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")\n'
            '    image_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")\n'
            "    context_length: Mapped[int] = mapped_column(BigInteger, nullable=True)\n"
            "    max_completion_tokens: Mapped[int] = mapped_column(BigInteger, nullable=True)\n"
            "    supports_tools: Mapped[bool] = mapped_column(default=False)\n"
            "    supports_vision: Mapped[bool] = mapped_column(default=False)\n"
        ),
        "plans.py": (
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from sqlalchemy import Float, String\n"
            "class KiloPlanSnapshot(Base):\n"
            "    tier: Mapped[str] = mapped_column(String(64), nullable=False)\n"
            "    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)\n"
            "    monthly_usd: Mapped[float] = mapped_column(Float, nullable=False)\n"
            "    paid_credits_usd: Mapped[float] = mapped_column(Float, nullable=False)\n"
            "    max_bonus_pct: Mapped[float] = mapped_column(Float, nullable=False)\n"
            # a REAL shared literal default (not type+constraint scaffolding)
            # must still surface — the unwrap judges the INSIDE, not suppress
            '    cache_namespace: Mapped[str] = mapped_column(String, default="models:normalized:v3")\n'
            '    shard_prefix: Mapped[str] = mapped_column(String, default="models:normalized:v3")\n'
        ),
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        sv = {frozenset((d["a"]["name"], d["b"]["name"])) for d in dups
              if d["reason"].startswith("same-value")}
        boiler = {
            "model_id", "name", "prompt_usd_per_mtok", "completion_usd_per_mtok",
            "request_usd", "image_usd", "context_length", "max_completion_tokens",
            "supports_tools", "supports_vision", "tier", "source_hash",
            "monthly_usd", "paid_credits_usd", "max_bonus_pct",
        }
        check(not any(p <= boiler for p in sv),
              f"orm: type+constraint mapped_column never anchors same-value ({sv})")
        check(frozenset(("cache_namespace", "shard_prefix")) in sv,
              "orm: a genuine shared literal default is still flagged")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_trivial_container_value_suppression():
    """Dogfood 2026-07-17 (a production repo): two distinct-named accumulators both
    `defaultdict(list)` (`by_tool` keyed by tool name, `signature_groups` keyed
    by error signature) paired as same-value even after a deliberate rename —
    the shared RHS is a bare empty-container idiom carrying no dedup signal. A
    container constructor seeded with a builtin factory must not anchor a
    same-value pair; a genuinely meaningful shared value still must."""
    root = _make_repo("varmem-trivctor-", {
        "app/report.py": (
            "from collections import defaultdict\n"
            "def render(records):\n"
            "    by_tool = defaultdict(list)\n"
            "    signature_groups = defaultdict(list)\n"
            "    tally_by_kind = defaultdict(int)\n"
            "    running_totals = defaultdict(int)\n"
            "    return by_tool, signature_groups, tally_by_kind, running_totals\n"
        ),
        # unrelated names, one genuinely meaningful shared value -> must pair
        "app/cfg.py": (
            'alpha_endpoint = "https://api.internal.example.com/v2/ingest"\n'
            'beta_channel = "https://api.internal.example.com/v2/ingest"\n'
        ),
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        sv = {frozenset((d["a"]["name"], d["b"]["name"])) for d in dups
              if d["reason"].startswith("same-value")}
        check(frozenset(("by_tool", "signature_groups")) not in sv,
              f"trivial-value: defaultdict(list) accumulators not paired ({sv})")
        check(frozenset(("tally_by_kind", "running_totals")) not in sv,
              "trivial-value: defaultdict(int) accumulators not paired")
        check(frozenset(("alpha_endpoint", "beta_channel")) in sv,
              "trivial-value: a real shared value still flagged (no over-suppression)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_shared_source_derivation_not_mismatch():
    """Dogfood 2026-07-18 (ai-optimization): after a correct dedup, two same-
    named consts that both derive from the shared SEVERITY_ORDER (one bare, one
    `new Set(...)`) were still flagged value-mismatch. Two views of one tracked
    source of truth are a shared-source link, not drift; an unrelated same-name
    / different-value pair must still flag."""
    root = _make_repo("varmem-sharedsrc-", {
        "lib/types.ts": ('export const SEVERITY_ORDER = '
                         '["critical", "high", "medium", "low"];\n'),
        "api/route.ts": ('import { SEVERITY_ORDER } from "@/lib/types";\n'
                         'const SEVERITIES = new Set(SEVERITY_ORDER);\n'),
        "ui/Queue.tsx": ('import { SEVERITY_ORDER } from "@/lib/types";\n'
                         'const SEVERITIES = SEVERITY_ORDER;\n'),
        # a genuine same-name / different-value mismatch must still surface
        "a/cfg.py": "RETRY_LIMIT = 3\n",
        "b/cfg.py": "RETRY_LIMIT = 9\n",
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        by = {frozenset((d["a"]["name"], d["b"]["name"])): d for d in dups}
        sev = by.get(frozenset(("SEVERITIES", "SEVERITIES")))
        check(sev is not None and sev["reason"] == "shared-source",
              "shared-source: SEVERITIES both from SEVERITY_ORDER -> link "
              f"({sev and sev['reason']})")
        rl = by.get(frozenset(("RETRY_LIMIT", "RETRY_LIMIT")))
        check(rl is not None and rl["reason"] == "value-mismatch",
              "shared-source: unrelated same-name mismatch still flags "
              f"({rl and rl['reason']})")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_gitignore_keeps_verdicts_and_config():
    """The generated .varmem/.gitignore keeps the human-curated files
    (reviews.json, config.json) while ignoring the churny machine registry, so
    a `.varmem/*` repo-root rule is never needed. An unmodified legacy default
    migrates in place; a hand-edited .gitignore is left untouched."""
    root = Path(tempfile.mkdtemp(prefix="varmem-gi-"))
    try:
        store.open_store(root).save()
        gp = root / config.VARMEM_DIRNAME / ".gitignore"
        gi = gp.read_text(encoding="utf-8")
        check("!reviews.json" in gi and "!config.json" in gi,
              "gitignore: verdicts + config kept tracked")
        check(any(ln.strip() == "*" for ln in gi.splitlines()),
              "gitignore: churny registry ignored (catch-all '*')")
        gp.write_text(store._LEGACY_GITIGNORE, encoding="utf-8")
        store.open_store(root)  # _load migrates the unmodified legacy default
        check("!reviews.json" in gp.read_text(encoding="utf-8"),
              "gitignore: unmodified legacy default upgraded in place")
        custom = "varmem.log\n# my own notes\nsecret.txt\n"
        gp.write_text(custom, encoding="utf-8")
        store.open_store(root)
        check(gp.read_text(encoding="utf-8") == custom,
              "gitignore: a hand-edited .gitignore is never touched")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_truncated_multiline_call_not_same_value():
    """Dogfood 2026-07-18 (varmem): two DIFFERENT multi-line calls are each
    captured as just the truncated head `foo(` (the args continue on the next
    line), so distinct calls look identical and pair as same-value. A value
    ending in an unclosed opener is incomplete and must not anchor same-value
    pairing; a complete shared value still does."""
    root = _make_repo("varmem-trunc-", {
        "app/a.ts": ("const watcher = vscode.workspace.createFileSystemWatcher(\n"
                     "  new RelativePattern(root, '.varmem/**'));\n"),
        "app/b.ts": ("const srcWatcher = vscode.workspace.createFileSystemWatcher(\n"
                     "  new RelativePattern(root, '**/*.py'));\n"),
        # a complete shared value under unrelated names must still pair
        "app/c.ts": 'const INGEST_ENDPOINT = "https://api.example.com/ingest/v2";\n',
        "app/d.ts": 'const PUBLISH_TARGET = "https://api.example.com/ingest/v2";\n',
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        sv = {frozenset((d["a"]["name"], d["b"]["name"])) for d in dups
              if d["reason"].startswith("same-value")}
        check(frozenset(("watcher", "srcWatcher")) not in sv,
              f"truncated: multi-line `foo(` heads not same-value paired ({sv})")
        check(frozenset(("INGEST_ENDPOINT", "PUBLISH_TARGET")) in sv,
              "truncated: a complete shared value still pairs (no over-suppression)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_namespaced_family_token_overlap_damped():
    """Dogfood 2026-07-18 (varmem): _KILO_PLUGIN_MARKER / _KILO_PLUGIN_TEMPLATE —
    a same-file namespaced family (shared prefix, one discriminator token) was
    flagged token-overlap MEDIUM. A systematic one-token-apart family sinks a
    tier to low (off the default report); a token-overlap that is NOT such a
    family (differs in more than one position) stays medium."""
    root = _make_repo("varmem-family-", {
        "svc/kilo.py": (
            'KILO_PLUGIN_MARKER = "varmem-kilo-plugin sentinel string here"\n'
            'KILO_PLUGIN_TEMPLATE = "a long distinct template body goes here"\n'
            # NOT a family (differs in >1 ordered token) -> stays token-overlap
            'FETCH_USER_DATA = "one two three four five"\n'
            'USER_FETCH_LIST = "six seven eight nine ten"\n'
        ),
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        by = {frozenset((d["a"]["name"], d["b"]["name"])): d for d in dups}
        fam = by.get(frozenset(("KILO_PLUGIN_MARKER", "KILO_PLUGIN_TEMPLATE")))
        check(fam is not None and fam["level"] == "low",
              "family: shared-prefix sibling sunk to low "
              f"({fam and (fam['reason'], fam['level'])})")
        other = by.get(frozenset(("FETCH_USER_DATA", "USER_FETCH_LIST")))
        check(other is not None and other["level"] == "medium",
              "family: a non-family token-overlap stays medium "
              f"({other and (other['reason'], other['level'])})")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_destructured_default_param_trivial():
    """Dogfood 2026-07-16 (ai-models-pricing): a destructured default param
    captured line-by-line keeps its trailing separator (`false,`), which is
    neither in TRIVIAL_VALUES nor len < 4. Multi-char trivials with a trailing
    `,`/`;` must collapse before the triviality test, so `false,`/`true,`/
    `null,` defaults never anchor same-value pairing."""
    root = _make_repo("varmem-defparam-", {
        "web/Table.tsx": (
            "function Th({\n"
            "  numeric = false,\n"
            "  center = false,\n"
            "  sortable = true,\n"
            "  filterable = true,\n"
            "}: ThProps) {\n"
            "  return null;\n"
            "}\n"
        ),
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        sv = {frozenset((d["a"]["name"], d["b"]["name"])) for d in dups
              if d["reason"].startswith("same-value")}
        check(frozenset(("numeric", "center")) not in sv,
              f"defparam: `false,` default never anchors same-value ({sv})")
        check(frozenset(("sortable", "filterable")) not in sv,
              "defparam: `true,` default never anchors same-value")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_parallel_sibling_dampers():
    """Dogfood 2026-07-16 (ai-models-pricing): sibling families declared
    together in ONE module — antonym twins (input/output), React useState
    setters (setX/changeX), sibling secret fields (…_api_key), cache-namespace
    constants (…_cache_key/…_cache_ttl) — are deliberate parallel naming, not
    drift, and sink medium->low. The SAME shape ACROSS FILES is never damped
    (a rotated secret or cross-service constant diverging), so it stays
    visible — the damper deprioritizes, it does not exclude."""
    root = _make_repo("varmem-sibling-", {
        "app/config.py": (
            "class Settings:\n"
            '    anthropic_api_key = Field(default="")\n'
            '    openai_api_key = Field(default="")\n'
            '    kilo_api_key = Field(default="")\n'
            "    rank_input_weight = Field(default=0.30)\n"
            "    rank_output_weight = Field(default=0.70)\n"
        ),
        "app/cache.py": (
            'USAGE_CACHE_KEY = "accounts:usage"\n'
            "USAGE_CACHE_TTL = 120\n"
            'MODELS_CACHE_KEY = "models:normalized"\n'
            "MODELS_CACHE_TTL = 900\n"
        ),
        "web/Table.tsx": (
            "function Table() {\n"
            "  const [pageSize, setPageSize] = useState(25);\n"
            "  const [page, setPage] = useState(0);\n"
            "  const changePageSize = (v) => setPageSize(v);\n"
            "  return null;\n"
            "}\n"
        ),
        # SAME credential/token-overlap shape SPLIT ACROSS FILES: a rotated
        # secret drifting is real misalignment and must NOT be damped
        "svc/a.py": 'STRIPE_LIVE_KEY = "prod-key-alpha"\n',
        "svc/b.py": 'STRIPE_TEST_KEY = "prod-key-omega"\n',
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        med = {frozenset((d["a"]["name"], d["b"]["name"]))
               for d in dups if d["level"] in ("high", "medium")}
        low = {frozenset((d["a"]["name"], d["b"]["name"]))
               for d in dups if d["level"] == "low"}
        for kind, pair in [("secret", ("anthropic_api_key", "openai_api_key")),
                           ("antonym", ("rank_input_weight", "rank_output_weight")),
                           ("cache", ("USAGE_CACHE_KEY", "USAGE_CACHE_TTL")),
                           ("react", ("changePageSize", "setPageSize"))]:
            fp = frozenset(pair)
            check(fp not in med and fp in low,
                  f"sibling: same-file {kind} family damped to low ({pair})")
        # the reviewer's safety property: cross-file same-shape stays visible
        check(frozenset(("STRIPE_LIVE_KEY", "STRIPE_TEST_KEY")) in med,
              "sibling: cross-file secret-suffix drift is NOT damped (visible)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_engine_build_marker():
    """report_data surfaces the engine version + build so a stale bundled engine
    is visible in the extension UI (Bug 4). Running from source reports
    'source'; bundle-engine.js stamps the real commit into the packaged copy."""
    import varmem
    bi = varmem.build_info()
    check(bi["version"] == varmem.__version__ and bi.get("build") == "source",
          "engine: build_info reports version + 'source' when run from source")
    root = _make_repo("varmem-engine-", {"a.py": "X = 1\n"})
    try:
        eng = report.report_data(root).get("engine") or {}
        check(eng.get("version") == varmem.__version__ and "build" in eng,
              "engine: report_data includes engine version + build")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_dunder_metadata_ignored():
    """Dunder module-metadata (__all__, __version__) legitimately repeats in
    every module and must never pair (dogfood 2026-07-16, a production repo:
    __all__ flagged 148x across agent modules)."""
    root = _make_repo("varmem-dunder-", {
        "a/m.py": '__all__ = ["x"]\n__version__ = "1.0"\nGREETING = "hello there world"\n',
        "b/m.py": '__all__ = ["y"]\n__version__ = "2.0"\nGREETING = "hello there world"\n',
    })
    try:
        dups = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        names = {n for d in dups for n in (d["a"]["name"], d["b"]["name"])}
        check("__all__" not in names and "__version__" not in names,
              "dunder: __all__/__version__ metadata never pairs")
        check(any({"GREETING"} == {d["a"]["name"], d["b"]["name"]} for d in dups),
              "dunder: a genuine same-value constant still pairs (rule is targeted)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_learned_family_suppression():
    """Dismissing one member of a naming family quiets the rest — and future
    members — without suppressing unrelated families; a config toggle disables
    the generalization."""
    root = _make_repo("varmem-learn-", {
        "pg.py": "ISSUE_PAGE_SIZE = 50\nUSER_PAGE_SIZE = 100\n"
                 "ITEM_PAGE_SIZE = 25\n",
        "tok.py": "AUTH_TOKEN_TTL = 3600\nSESSION_TOKEN_TTL = 7200\n",
    })
    try:
        def fams(dl):
            return {frozenset((d["a"]["name"], d["b"]["name"])) for d in dl
                    if d["level"] in ("high", "medium")}

        st = store.open_store(root)
        before = fams(duplicates.find_duplicates(st))
        pg_before = [p for p in before if "PAGE_SIZE" in " ".join(p)]
        check(len(pg_before) == 3
              and frozenset(("AUTH_TOKEN_TTL", "SESSION_TOKEN_TTL")) in before,
              f"learn: family members all shown initially ({len(pg_before)})")

        seed = next(d for d in duplicates.find_duplicates(st)
                    if {d["a"]["name"], d["b"]["name"]}
                    == {"ISSUE_PAGE_SIZE", "USER_PAGE_SIZE"})
        st.set_review(seed["pair_key"], "not_duplicate", "intentional knobs")
        st.save()

        after = fams(duplicates.find_duplicates(store.open_store(root)))
        check(not any("PAGE_SIZE" in " ".join(p) for p in after),
              "learn: dismissing one member auto-quiets the whole family")
        check(frozenset(("AUTH_TOKEN_TTL", "SESSION_TOKEN_TTL")) in after,
              "learn: a different family (token,ttl) is NOT suppressed")

        alld = duplicates.find_duplicates(store.open_store(root),
                                          include_dismissed=True)
        learned = [d for d in alld if (d.get("review") or {}).get("learned")]
        check(len(learned) == 2
              and all("PAGE_SIZE" in d["a"]["name"] for d in learned),
              "learn: siblings marked learned + visible with include_dismissed")

        # a member added AFTER the dismissal is quiet from the start
        (root / "pg.py").write_text(
            "ISSUE_PAGE_SIZE = 50\nUSER_PAGE_SIZE = 100\n"
            "ITEM_PAGE_SIZE = 25\nADMIN_PAGE_SIZE = 5\n", encoding="utf-8")
        scan.scan_project(root)
        after2 = fams(duplicates.find_duplicates(store.open_store(root)))
        check(not any("PAGE_SIZE" in " ".join(p) for p in after2),
              "learn: a newly-added family member is quiet from the start")

        (config.varmem_dir(root) / "config.json").write_text(
            json.dumps({"learn_from_reviews": False}), encoding="utf-8")
        after3 = fams(duplicates.find_duplicates(store.open_store(root)))
        check(any("PAGE_SIZE" in " ".join(p) for p in after3),
              "learn: config learn_from_reviews=false disables generalization")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sqlite_migration():
    root = Path(tempfile.mkdtemp(prefix="varmem-mig-"))
    try:
        import sqlite3
        legacy_dir = root / ".claude" / "varmem"
        legacy_dir.mkdir(parents=True)
        conn = sqlite3.connect(legacy_dir / "varmem.db")
        conn.execute(
            "CREATE TABLE variables (name TEXT, lang TEXT, file TEXT, "
            "scope TEXT DEFAULT '', kind TEXT, line INTEGER, "
            "value_preview TEXT, value_hash TEXT, redacted INTEGER DEFAULT 0, "
            "repo_value_preview TEXT, repo_value_hash TEXT, repo_line INTEGER, "
            "status TEXT, first_session TEXT, last_session TEXT, "
            "first_seen_at TEXT, last_written_at TEXT, last_verified_at TEXT, "
            "origin TEXT DEFAULT 'claude', note TEXT)")
        conn.execute(
            "INSERT INTO variables (name, lang, file, scope, kind, line, "
            "value_preview, value_hash, status, first_session, last_session, "
            "last_written_at, origin) VALUES ('OLD_VAR','python','x.py','',"
            "'assign',1,'42','h1','active','s1','s1',"
            "'2026-07-15T00:00:00','claude')")
        conn.execute("CREATE TABLE dup_reviews (pair_key TEXT PRIMARY KEY, "
                     "verdict TEXT, note TEXT, ts TEXT)")
        conn.execute("INSERT INTO dup_reviews VALUES "
                     "('k1','not_duplicate','old note','2026-07-15T00:00:00')")
        conn.commit()
        conn.close()

        st = store.open_store(root)
        check(any(r["name"] == "OLD_VAR" for r in st.all_rows()),
              "migration: sqlite rows imported")
        check(st.reviews.get("k1", {}).get("note") == "old note",
              "migration: reviews imported")
        check((root / ".varmem" / "vars").exists(),
              "migration: file store created")
        st2 = store.open_store(root)
        check(sum(1 for r in st2.all_rows() if r["name"] == "OLD_VAR") == 1,
              "migration: idempotent on reopen")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_live_server():
    root = Path(tempfile.mkdtemp(prefix="varmem-live-"))
    try:
        (root / "m.py").write_text(
            "ALPHA_TIMEOUT = 30\nALPHA_TIME_OUT = 30\n", encoding="utf-8")
        scan.scan_project(root)
        srv = live.make_server(0, extra_roots=[root])
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            listed = json.loads(urllib.request.urlopen(
                base + "/api/repos", timeout=15).read())
            idx = next((i for i, r in enumerate(listed)
                        if r["path"] == str(root)), None)
            check(idx is not None, "live: repo listed")
            data = json.loads(urllib.request.urlopen(
                f"{base}/api/data?repo={idx}", timeout=15).read())
            check(len(data["dups"]) >= 1
                  and data["vars"][0]["name"].startswith("ALPHA"),
                  "live: data served with duplicates")
            pair = data["dups"][0]["pair_key"]
            req = urllib.request.Request(
                f"{base}/api/review",
                data=json.dumps({"repo": idx, "pair_key": pair,
                                 "verdict": "not_duplicate",
                                 "note": "via live"}).encode(),
                method="POST")
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
            check(resp.get("ok") is True, "live: review POST accepted")
            reviews = json.loads((root / ".varmem" / "reviews.json")
                                 .read_text(encoding="utf-8"))
            check(reviews.get(pair, {}).get("note") == "via live",
                  "live: override persisted to reviews.json")
            html = urllib.request.urlopen(base + "/", timeout=15).read()
            check(b"varmem live" in html, "live: page served")
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cli_surface():
    from varmem import cli
    import io
    with _temp_registry():
        ra = _make_repo("varmem-cliA-", {
            "c1.py": "CLI_SENTINEL = 111\n",
            "c2.py": "CLI_SENTINEL = 222\n",
        })
        out_dir = Path(tempfile.mkdtemp(prefix="varmem-cliout-"))
        try:
            repos.add_repo(str(ra))
            cap = io.StringIO()
            old = sys.stdout
            sys.stdout = cap
            try:
                rc1 = cli.main(["groups", "create", "CLI stack",
                                "--repo", str(ra)])
                rc2 = cli.main(["groups", "list"])
                out_file = out_dir / "sub" / "prompt.md"
                rc3 = cli.main(["--project", str(ra), "prompt",
                                "--out", str(out_file)])
            finally:
                sys.stdout = old
            text = cap.getvalue()
            check(rc1 == 0 and rc2 == 0 and "CLI stack" in text,
                  "cli: groups create + list")
            check(rc3 == 0 and out_file.exists()
                  and "CLI_SENTINEL" in out_file.read_text(encoding="utf-8"),
                  "cli: prompt --out writes requested path")
            g = repos.find_group("CLI stack")
            cap2 = io.StringIO()
            sys.stdout = cap2
            try:
                rc4 = cli.main(["groups", "rename", g["id"],
                                "--name", "CLI stack v2"])
                rc5 = cli.main(["groups", "delete", "CLI stack v2"])
            finally:
                sys.stdout = old
            check(rc4 == 0 and rc5 == 0
                  and repos.group_entries() == [],
                  "cli: groups rename + delete")
        finally:
            shutil.rmtree(ra, ignore_errors=True)
            shutil.rmtree(out_dir, ignore_errors=True)


def _get_json(base, path):
    try:
        return (json.loads(urllib.request.urlopen(
            base + path, timeout=15).read()), 200)
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def _post_json(base, path, payload):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(), method="POST")
    try:
        return (json.loads(urllib.request.urlopen(req, timeout=15).read()),
                200)
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def test_live_server_v2():
    with _temp_registry() as reg_path:
        ra = _make_repo("varmem-v2A-", {
            "a1.py": "ALPHA_TOKEN_LIMIT = 4141\nALPHA_WINDOW = 7\n",
            "a2.py": "ALPHA_TOKEN_LIMIT = 4242\nALPHA_WINDOW_SIZE = 7\n",
        })
        rb = _make_repo("varmem-v2B-", {
            "b1.py": "BRAVO_RATE_CAP = 9191\n",
            "b2.py": "BRAVO_RATE_CAP = 9292\n",
        })
        rempty = Path(tempfile.mkdtemp(prefix="varmem-v2E-"))
        srv = None
        try:
            repos.add_repo(str(ra))
            repos.add_repo(str(rb))
            repos.add_repo(str(rempty))
            ida, idb = repos.repo_id_for(ra), repos.repo_id_for(rb)
            ide = repos.repo_id_for(rempty)
            grp = repos.create_group("Stack", [ida, idb, ide],
                                     compose_projects=["stack"])

            srv = live.make_server(0)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"

            # ------------------------------------------------ page markup
            html = urllib.request.urlopen(base + "/", timeout=15) \
                .read().decode("utf-8")
            check("varmem live" in html, "v2: page served")
            for hook in ('aria-label', 'Skip to content',
                         '<meta name="viewport"', '@media (max-width:900px)',
                         ':focus-visible', '<nav', 'role="status"',
                         'aria-expanded'):
                check(hook in html, f"v2: a11y/responsive hook ({hook})")

            # ------------------------------------------------------- tree
            tree, code = _get_json(base, "/api/tree")
            check(code == 200 and len(tree["groups"]) == 1
                  and {r["id"] for r in tree["repos"]} == {ida, idb, ide}
                  and tree["standalone"] == [],
                  "v2: tree lists group + repos by stable id")

            # stable-id lookup survives on-disk reordering
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
            reg["repos"].reverse()
            reg_path.write_text(json.dumps(reg), encoding="utf-8")
            summ, code = _get_json(base, f"/api/repo?id={ida}")
            check(code == 200 and summ["name"] == ra.name
                  and summ["counts"]["tracked"] >= 4,
                  "v2: repo summary by stable id after reorder")
            _, code = _get_json(base, "/api/repo?id=r-nonexistent")
            check(code == 404, "v2: unknown repo id -> 404")

            # ------------------------------------- issues: paging + filter
            p1, code = _get_json(base, f"/api/issues?repo={ida}&size=1")
            check(code == 200 and p1["total"] >= 2 and len(p1["items"]) == 1
                  and p1["pages"] == p1["total"],
                  f"v2: issues paginated (total {p1['total']})")
            p2, _ = _get_json(base, f"/api/issues?repo={ida}&size=1&page=2")
            check(p2["items"][0]["key"] != p1["items"][0]["key"],
                  "v2: issues page 2 differs")
            flt, _ = _get_json(base,
                               f"/api/issues?repo={ida}&q=window")
            check(flt["total"] >= 1 and all(
                "WINDOW" in i["title"] for i in flt["items"]),
                "v2: issues search filter")

            # strict isolation across the API surface
            ia, _ = _get_json(base, f"/api/issues?repo={ida}&size=100")
            ib, _ = _get_json(base, f"/api/issues?repo={idb}&size=100")
            a_txt, b_txt = json.dumps(ia), json.dumps(ib)
            check("BRAVO" not in a_txt and "ALPHA" not in b_txt,
                  "v2: issue lists are strictly repo-scoped")
            check(all(i["repo"]["id"] == ida for i in ia["items"]),
                  "v2: every issue row labelled with its repo")

            # ------------------------------------------- vars: paging
            v1, _ = _get_json(base, f"/api/vars?repo={ida}&size=2")
            check(v1["total"] == 4 and len(v1["items"]) == 2
                  and v1["pages"] == 2, "v2: vars paginated")
            vq, _ = _get_json(base,
                              f"/api/vars?repo={ida}&q=token")
            check(vq["total"] == 2, "v2: vars search filter")
            vb, _ = _get_json(base, f"/api/vars?repo={idb}&size=100")
            check("ALPHA" not in json.dumps(vb),
                  "v2: vars strictly repo-scoped")

            # --------------------------------------------- group overview
            ov, code = _get_json(base, f"/api/overview?group={grp['id']}")
            check(code == 200 and len(ov["repos"]) == 3
                  and ov["totals"]["tracked"] == 6
                  and ov["totals"]["needs_attention"]
                  == sum(r["counts"]["needs_attention"]
                         for r in ov["repos"]),
                  "v2: overview aggregates counts only")
            check(all("repo" in q for q in ov["queue"])
                  and not any("ALPHA" in q["title"] and "BRAVO" in q["title"]
                              for q in ov["queue"]),
                  "v2: queue rows labelled, never cross-repo pairs")
            _, code = _get_json(base, "/api/overview?group=g-bogus")
            check(code == 404, "v2: unknown group -> 404")

            # ------------------------------------- review write isolation
            pair = ia["items"][0]["key"]
            resp, code = _post_json(base, "/api/review",
                                    {"repo": ida, "pair_key": pair,
                                     "verdict": "not_duplicate",
                                     "note": "via v2 api"})
            check(code == 200 and resp["ok"] and resp["repo"] == ida,
                  "v2: review accepted by stable id")
            reviews_a = json.loads((ra / ".varmem" / "reviews.json")
                                   .read_text(encoding="utf-8"))
            check(reviews_a.get(pair, {}).get("note") == "via v2 api",
                  "v2: review persisted in repo A")
            check(not (rb / ".varmem" / "reviews.json").exists(),
                  "v2: repo B reviews untouched")
            resp, code = _post_json(base, "/api/review",
                                    {"repo": "r-bogus", "pair_key": "x",
                                     "verdict": "merged"})
            check(code == 404, "v2: review rejects unknown repo id")

            # ---------------------------------------- annotate + reconcile
            resp, code = _post_json(base, "/api/annotate",
                                    {"repo": idb, "name": "BRAVO_RATE_CAP",
                                     "file": "b1.py", "note": "canonical"})
            check(code == 200 and resp["annotated"] == 1,
                  "v2: annotate round-trip")
            resp, code = _post_json(base, "/api/reconcile",
                                    {"repo": ida, "force": True})
            check(code == 200 and resp["totals"]["files"] >= 2,
                  "v2: reconcile round-trip")
            resp, code = _post_json(base, "/api/rescan", {"repo": ide})
            check(code == 200 and resp["totals"]["files"] == 0,
                  "v2: rescan round-trip (empty repo)")

            # ---------------------------------------------- prompt (HTTP)
            resp, code = _post_json(base, "/api/prompt", {"repo": ida})
            check(code == 200 and resp["filename"].endswith(".md")
                  and resp["prompt"].startswith("# Variable alignment")
                  and "BRAVO" not in resp["prompt"],
                  "v2: prompt contract (filename + markdown, repo-scoped)")
            resp, code = _post_json(base, "/api/prompts",
                                    {"group": grp["id"]})
            items = resp["items"]
            check(code == 200 and len(items) == 3,
                  "v2: group prompts produce one artifact per repo")
            by_name = {i["name"]: i for i in items}
            check("prompt" in by_name[ra.name]
                  and "prompt" in by_name[rb.name]
                  and by_name[rempty.name].get("skipped"),
                  "v2: empty repo skipped with a reason")
            check("ALPHA" not in by_name[rb.name]["prompt"]
                  and "BRAVO" not in by_name[ra.name]["prompt"],
                  "v2: group prompts never blend repositories")

            # ------------------------------------------ groups via HTTP
            resp, code = _post_json(base, "/api/groups/create",
                                    {"name": "HTTP group",
                                     "repo_ids": [ida]})
            gid2 = resp["group"]["id"]
            check(code == 200 and resp["group"]["repo_ids"] == [ida],
                  "v2: group create via HTTP")
            resp, code = _post_json(base, "/api/groups/rename",
                                    {"id": gid2, "name": "Renamed"})
            check(code == 200 and resp["group"]["name"] == "Renamed"
                  and resp["group"]["id"] == gid2,
                  "v2: group rename via HTTP")
            resp, code = _post_json(base, "/api/groups/members",
                                    {"id": gid2, "repo_ids": [ida, idb]})
            check(code == 200
                  and set(resp["group"]["repo_ids"]) == {ida, idb},
                  "v2: group membership via HTTP")
            resp, code = _post_json(base, "/api/groups/create",
                                    {"name": "bad", "repo_ids": ["r-nope"]})
            check(code == 400 and "unknown repo id" in resp["error"],
                  "v2: group create rejects unknown repo ids")
            resp, code = _post_json(base, "/api/groups/delete",
                                    {"id": gid2})
            check(code == 200 and resp["ok"], "v2: group delete via HTTP")

            # ------------------------------------------------ discovery
            disc, code = _get_json(base, "/api/discovery")
            check(code == 200 and "docker" in disc
                  and isinstance(disc["suggestions"], list),
                  "v2: discovery endpoint structured")

            # --------------------------------------------- legacy compat
            lst, code = _get_json(base, "/api/repos")
            check(code == 200 and any(r["path"] == str(ra) for r in lst)
                  and all("id" in r for r in lst),
                  "v2: legacy /api/repos keeps native paths, adds ids")
            data, code = _get_json(base, "/api/data?repo=0")
            check(code == 200 and "dups" in data and "vars" in data,
                  "v2: legacy /api/data by index still works")
        finally:
            if srv is not None:
                srv.shutdown()
                srv.server_close()
            shutil.rmtree(ra, ignore_errors=True)
            shutil.rmtree(rb, ignore_errors=True)
            shutil.rmtree(rempty, ignore_errors=True)


def _api(base, method, path, token=None, payload=None):
    from varmem import serve  # noqa: F401
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read() or b"{}"), resp.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}"), e.code
        except Exception:
            return {}, e.code


def test_api_server():
    from varmem import serve
    data_dir = Path(tempfile.mkdtemp(prefix="varalign-data-"))
    token = "test-secret-token-123"
    srv = serve.make_server("127.0.0.1", 0, data_dir=data_dir, token=token)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        # health is unauthed
        h, code = _api(base, "GET", "/v1/health")
        check(code == 200 and h["status"] == "ok", "api: health unauthed")
        langs, code = _api(base, "GET", "/v1/languages")
        check(code == 200 and "python" in langs["languages"],
              "api: languages listed")

        # auth is enforced
        _, code = _api(base, "POST", "/v1/analyze",
                       payload={"files": {"a.py": "X = 1\n"}})
        check(code == 401, "api: analyze without token -> 401")
        _, code = _api(base, "POST", "/v1/analyze", token="wrong",
                       payload={"files": {"a.py": "X = 1\n"}})
        check(code == 401, "api: analyze with bad token -> 401")

        # stateless analyze finds cross-file duplicates, never persists
        res, code = _api(base, "POST", "/v1/analyze", token=token, payload={
            "files": {
                "svc/a.py": 'MAX_RETRIES = 5\n'
                            'AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"\n',
                "svc/b.py": "MAX_RETRIES = 9\n",
            },
            "include_prompt": True, "include_variables": True})
        check(code == 200 and res["files"] == 2, "api: analyze ran")
        names = {d["a"]["name"] for d in res["duplicates"]}
        check("MAX_RETRIES" in names, "api: analyze found duplicate")
        check(any(d["reason"] == "value-mismatch"
                  for d in res["duplicates"]),
              "api: analyze value-mismatch flagged")
        check("AKIA" not in json.dumps(res),
              "api: analyze redacts secret-shaped values")
        check(res["prompt"] and "AKIA" not in res["prompt"],
              "api: analyze prompt included and redacted")
        check(not list(data_dir.glob("projects/*")),
              "api: analyze persisted nothing")

        # input validation
        _, code = _api(base, "POST", "/v1/analyze", token=token,
                       payload={"files": {}})
        check(code == 400, "api: empty files rejected")
        _, code = _api(base, "POST", "/v1/analyze", token=token,
                       payload={"files": {"../evil.py": "X=1"}})
        check(code == 400, "api: path traversal rejected")

        # stateful project lifecycle
        cap, code = _api(base, "POST", "/v1/projects/proj-A/capture",
                         token=token, payload={
                             "session_id": "sess-1",
                             "files": {"cfg.py": "TOKEN_TTL = 30\n"}})
        check(code == 200 and cap["added"] == 1, "api: project capture")
        cap2, _ = _api(base, "POST", "/v1/projects/proj-A/capture",
                       token=token, payload={
                           "session_id": "sess-2",
                           "files": {"cfg.py": "TOKEN_TTL = 60\n"}})
        check(cap2["updated"] == 1, "api: project re-capture updates")

        ctx, code = _api(base, "GET", "/v1/projects/proj-A/context",
                         token=token)
        check(code == 200 and "TOKEN_TTL" in ctx["context"],
              "api: project context recall")
        v, code = _api(base, "GET",
                       "/v1/projects/proj-A/variables?pattern=TOKEN_*",
                       token=token)
        check(code == 200 and any(x["name"] == "TOKEN_TTL"
                                  for x in v["variables"]),
              "api: project variables query")

        # second project is isolated from the first
        _api(base, "POST", "/v1/projects/proj-B/capture", token=token,
             payload={"session_id": "s", "files": {"z.py": "OTHER = 1\n"}})
        va, _ = _api(base, "GET", "/v1/projects/proj-A/variables", token=token)
        check("OTHER" not in json.dumps(va),
              "api: projects strictly isolated")
        pl, _ = _api(base, "GET", "/v1/projects", token=token)
        check({p["id"] for p in pl["projects"]} == {"proj-A", "proj-B"},
              "api: project listing")

        # unknown project + bad id
        _, code = _api(base, "GET", "/v1/projects/nope/context", token=token)
        check(code == 404, "api: unknown project -> 404")
        _, code = _api(base, "GET", "/v1/projects/bad..id/context",
                       token=token)
        check(code == 400, "api: invalid project id -> 400")
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)


def test_control_tower_remote_source():
    """The local control tower can register a remote VarAlign API as a source
    and browse its projects (tree/overview/repo/issues/prompt), read-only."""
    with _temp_registry():
        data_dir = Path(tempfile.mkdtemp(prefix="varalign-remote-"))
        token = "remote-token-xyz"
        from varmem import serve
        apisrv = serve.make_server("127.0.0.1", 0, data_dir=data_dir,
                                   token=token)
        threading.Thread(target=apisrv.serve_forever, daemon=True).start()
        api_base = f"http://127.0.0.1:{apisrv.server_address[1]}"
        ct = live.make_server(0)
        threading.Thread(target=ct.serve_forever, daemon=True).start()
        ct_base = f"http://127.0.0.1:{ct.server_address[1]}"
        try:
            _api(api_base, "POST", "/v1/projects/demo/capture", token=token,
                 payload={"session_id": "s1",
                          "files": {"a.py": "MAX_RETRIES = 5\n"}})
            _api(api_base, "POST", "/v1/projects/demo/capture", token=token,
                 payload={"session_id": "s2",
                          "files": {"b.py": "MAX_RETRIES = 9\n"}})
            repos.add_api_source(api_base, token=token, name="home-api")
            sid = repos.api_source_entries()[0]["id"]
            gid, proj = f"api-src:{sid}", f"api:{sid}:demo"

            tree, code = _get_json(ct_base, "/api/tree")
            check(code == 200 and any(g["id"] == gid for g in tree["groups"]),
                  "remote: API source appears as a group in the tree")
            check(any(r["id"] == proj and r.get("remote")
                      for r in tree["repos"])
                  and all("api:" not in x for x in tree["standalone"]),
                  "remote: project is a remote repo, not standalone")

            summ, code = _get_json(ct_base, f"/api/repo?id={proj}")
            check(code == 200 and summ["remote"]
                  and summ["counts"]["tracked"] == 2,
                  "remote: repo summary fetched from the API")
            iss, code = _get_json(ct_base, f"/api/issues?repo={proj}")
            check(code == 200 and any(i["kind"] == "duplicate"
                                      and "MAX_RETRIES" in i["title"]
                                      for i in iss["items"]),
                  "remote: issues fetched + labelled from the API")
            ov, code = _get_json(ct_base, f"/api/overview?group={gid}")
            check(code == 200 and len(ov["repos"]) == 1
                  and ov["totals"]["tracked"] == 2,
                  "remote: group overview aggregates the source's projects")
            pr, code = _post_json(ct_base, "/api/prompt", {"repo": proj})
            check(code == 200
                  and pr.get("prompt", "").startswith("# Variable alignment"),
                  "remote: prompt proxied from the API")
            _, code = _post_json(ct_base, "/api/review",
                                 {"repo": proj, "pair_key": "x",
                                  "verdict": "merged"})
            check(code == 501, "remote: writes are read-only (501)")
        finally:
            ct.shutdown(); ct.server_close()
            apisrv.shutdown(); apisrv.server_close()
            shutil.rmtree(data_dir, ignore_errors=True)


def test_scan_respects_gitignore():
    """scan prunes gitignored directories — bare names (node_modules) and
    anchored build paths (build/out) — so committed build output never inflates
    the registry or the duplicate gate."""
    from varmem import scan, store
    root = _make_repo("varmem-gi-", {
        ".gitignore": "node_modules/\nbuild/out/\n",
        "app.py": "REAL_SETTING = 1\n",
        "build/out/gen.py": "GENERATED_SETTING = 2\n",
        "node_modules/pkg/index.js": "const dep = 3;\n",
    })
    try:
        scan.scan_project(root)
        files = {r["file"] for r in store.open_store(root).all_rows()}
        check("app.py" in files, "gitignore: real source is still scanned")
        check(not any(f.startswith("build/out") for f in files),
              "gitignore: anchored path build/out is pruned")
        check(not any("node_modules" in f for f in files),
              "gitignore: bare name node_modules is pruned")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_jsx_and_scope_extraction():
    """React/TSX dogfood (2026-07-16): JSX attributes are NOT assignments,
    component locals get function scope, and new Rust/Java extractors work."""
    from varmem import extractors
    tsx = (
        'import { useState } from "react";\n'
        'const CHART_BLUE = "#3b82f6";\n'
        'export function Panel() {\n'
        '  const [selected, setSelected] = useState<string | null>(null);\n'
        '  const { data, isLoading } = useQuery();\n'
        '  const label = data?.name ?? "";\n'
        '  return (\n'
        '    <button\n'
        '      type="button"\n'
        '      className={`rounded px-3 ${label}`}\n'
        '      onClick={() => setSelected(label)}\n'
        '      disabled={isLoading}\n'
        '    >\n'
        '      <Line type="monotone" stroke={CHART_BLUE} dot={false} />\n'
        '    </button>\n'
        '  );\n'
        '}\n'
    )
    _, found = extractors.extract("web/Panel.tsx", tsx)
    names = {v["name"] for v in found}
    check(not names & {"type", "className", "onClick", "disabled", "stroke",
                       "dot"},
          "jsx: attribute lines are never captured as assignments")
    by = {v["name"]: v for v in found}
    check(by["CHART_BLUE"]["scope"] == "", "jsx: module const stays top-level")
    check(by["label"]["scope"] == "Panel",
          "jsx: component locals carry the function scope")
    check(by["selected"]["kind"] == "const-destructure",
          "jsx: useState bindings are destructures")

    _, rs = extractors.extract("src/main.rs", (
        'const MAX_RETRIES: u32 = 5;\n'
        'fn run() {\n'
        '    let mut attempts = 0;\n'
        '}\n'
    ))
    rby = {v["name"]: v for v in rs}
    check(rby["MAX_RETRIES"]["scope"] == "" and rby["MAX_RETRIES"]["kind"] == "rust-const",
          "rust: const captured at top level")
    check(rby["attempts"]["scope"] == "run", "rust: let scoped to fn")

    _, jv = extractors.extract("App.java", (
        'public class App {\n'
        '    private static final int MAX_RETRIES = 5;\n'
        '    public void run() {\n'
        '        var attempts = 0;\n'
        '    }\n'
        '}\n'
    ))
    jby = {v["name"]: v for v in jv}
    check(jby["MAX_RETRIES"]["scope"] == "", "java: class field stays class-level")
    check(jby["attempts"]["scope"] == "run", "java: method local scoped")


def test_destructure_and_samename_learning():
    """Hook destructures never anchor same-value pairing, and dismissing ONE
    same-name pair auto-quiets every other pair of that name."""
    root = _make_repo("varmem-react-", {
        # same name, different values, three files -> 3 value-mismatch pairs
        "a/x.ts": 'const panelTitle = "alpha panel settings";\n',
        "b/x.ts": 'const panelTitle = "beta panel settings";\n',
        "c/x.ts": 'const panelTitle = "gamma panel settings";\n',
        # module-level destructures sharing one exotic RHS: the shared value
        # must NOT anchor a same-value pair between the two files
        "d/x.ts": 'const [alphaWidget, setAlphaWidget] = useState("zq_shared_9741");\n',
        "e/x.ts": 'const [betaGadget, setBetaGadget] = useState("zq_shared_9741");\n',
        # pydantic settings: shared Field(default="") boilerplate
        "f/settings.py": (
            "class Settings(BaseSettings):\n"
            '    vendor_api_token: str = Field(default="")\n'
            '    backup_api_secret: str = Field(default="")\n'
        ),
    })
    try:
        st = store.open_store(root)
        dups = duplicates.find_duplicates(st)
        same = [d for d in dups if d["a"]["name"] == "panelTitle"
                and d["b"]["name"] == "panelTitle"]
        check(len(same) == 3, "learning setup: 3 same-name pairs exist")
        check(not any(d["reason"].startswith("same-value")
                      and "Widget" in d["a"]["name"] + d["b"]["name"]
                      for d in dups),
              "destructure: shared RHS never anchors same-value pairing")

        # pydantic Settings boilerplate: Field(default="") shared by every
        # field must not anchor same-value pairing…
        check(not any("apitoken" in norm or "apisecret" in norm
                      for d in dups
                      for norm in [d["a"]["name"].lower() + d["b"]["name"].lower()]
                      if d["reason"].startswith("same-value")),
              "pydantic: Field(default=\"\") boilerplate never pairs")

        st.set_review(same[0]["pair_key"], "not_duplicate", "intentional")
        st.save()
        after = duplicates.find_duplicates(store.open_store(root),
                                           include_dismissed=True)
        rest = [d for d in after if d["a"]["name"] == "panelTitle"
                and d["pair_key"] != same[0]["pair_key"]]
        check(len(rest) == 2 and all(
            d["review"] and d["review"].get("learned") for d in rest),
            "learning: one same-name dismissal auto-quiets the other pairs")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ci_gate():
    """`varmem ci` exits non-zero when suspects at/above --fail-on exceed --max,
    honors --max as a baseline budget, and emits machine-readable --json."""
    import argparse as _argparse
    import contextlib as _ctx
    import io as _io
    import json as _json
    from varmem import cli
    root = _make_repo("varmem-ci-", {
        "cfg.py": 'DATABASE_URL = "postgres://db:5432/prod"\n'
                  'DB_URL = "postgres://db:5432/prod"\n',
    })

    def run(**kw):
        ns = _argparse.Namespace(project=str(root), fail_on="high", max=0,
                                 no_scan=False, json=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            rc = cli.cmd_ci(ns)
        return rc, buf.getvalue()

    try:
        rc, out = run(fail_on="high")
        check(rc == 1 and "FAIL" in out and "DATABASE_URL" in out,
              "ci: fails and names the high suspect")
        rc2, _ = run(fail_on="high", max=5, no_scan=True)
        check(rc2 == 0, "ci: passes when suspects are within the --max budget")
        rc3, out3 = run(fail_on="low", json=True, no_scan=True)
        data = _json.loads(out3)
        check(data["found"] >= 1 and data["passed"] is False,
              "ci: --json reports offenders and passed=false")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="varmem-test-"))
    print(f"test project: {root}")
    # never touch the user's real machine registry from automated tests
    _guard = Path(tempfile.mkdtemp(prefix="varmem-guard-reg-"))
    repos.REGISTRY = _guard / "repos.json"
    try:
        print("== extractors ==")
        test_python_extractor()
        test_js_extractor()
        test_misc_extractors()
        print("== redaction ==")
        test_redaction()
        print("== capture ==")
        test_capture_write_and_edit(root)
        print("== reconcile ==")
        test_reconcile_states(root)
        print("== context / session-start ==")
        test_context_and_session_start(root)
        print("== query / events / robustness ==")
        test_query_and_events(root)
        test_capture_never_raises(root)
        print("== kilo capture ==")
        test_capture_kilo_write_and_edit()
        test_kilo_claims_and_survives_scan()
        test_kilo_report_counts()
        test_init_kilo_idempotent()
        test_capture_kilo_cli_never_raises()
        test_init_kilo_marker_and_settings()
        test_store_file_lock()
        print("== registry v2 / groups ==")
        test_registry_migration()
        test_registry_malformed()
        test_groups_crud()
        print("== container discovery ==")
        test_discovery()
        print("== prompts / isolation ==")
        test_prompts_and_isolation()
        print("== scan / duplicates / report ==")
        test_scan_and_duplicates()
        test_duplicate_tuning()
        test_fp_false_positive_suppression()
        test_attribute_and_env_noise_suppression()
        test_standalone_units_config()
        test_auto_detect_standalone_units()
        test_scan_prunes_out_of_scope_rows()
        test_prompt_actionable_only()
        test_prompt_records_verdicts()
        test_pair_key_normalized()
        test_prompt_batches_large_findings()
        test_accept_drift_rebaselines()
        test_accept_cli_pattern()
        test_orm_column_boilerplate()
        test_trivial_container_value_suppression()
        test_shared_source_derivation_not_mismatch()
        test_gitignore_keeps_verdicts_and_config()
        test_truncated_multiline_call_not_same_value()
        test_namespaced_family_token_overlap_damped()
        test_destructured_default_param_trivial()
        test_parallel_sibling_dampers()
        test_engine_build_marker()
        test_dunder_metadata_ignored()
        test_learned_family_suppression()
        print("== cli surface ==")
        test_cli_surface()
        print("== store migration / live server ==")
        test_sqlite_migration()
        test_live_server()
        test_live_server_v2()
        print("== api server ==")
        test_api_server()
        test_control_tower_remote_source()
        test_scan_respects_gitignore()
        test_jsx_and_scope_extraction()
        test_destructure_and_samename_learning()
        test_ci_gate()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(_guard, ignore_errors=True)
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
