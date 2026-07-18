"""varmem command-line interface.

Hook entrypoints (read the hook JSON payload from stdin, always exit 0):
    python varmem.py capture        # Claude Code PostToolUse
    python varmem.py capture-kilo   # Kilo Code tool.execute.after plugin
    python varmem.py session-start

Human commands:
    python varmem.py scan | duplicates | report | live
    python varmem.py query CONFIG_*         # wildcard name lookup
    python varmem.py list --status drifted
    python varmem.py repos add <path>       # register repos for `live`
    python varmem.py groups create NAME --repo <path>   # control-tower groups
    python varmem.py discover [--confirm N] # Docker/Compose group suggestions
    python varmem.py prompt [--out FILE]    # repo-scoped AI remediation prompt
    python varmem.py reconcile [--force]
    python varmem.py context | stats | sessions | export | init
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import (config, context, discovery, duplicates, live, prompts,
               reconcile, report, repos, scan, store)
from .capture import process_payload, process_kilo_payload


def _read_stdin_payload() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _root(args, payload: dict | None = None) -> Path:
    return config.resolve_project_root(payload, getattr(args, "project", None))


def _print_rows(rows, as_json: bool):
    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        print("no matches")
        return
    for r in rows:
        scope = f" [{r['scope']}]" if r["scope"] else ""
        drift = ""
        if r["status"] == "drifted":
            drift = f"  << repo now: {r['repo_value_preview']}"
        print(f"{r['status']:12} {r['name']}{scope} = {r['value_preview']}"
              f"  ({r['file']}:{r['repo_line'] or r['line']})"
              f"  [{r['lang']}/{r['kind']}] last {r['last_written_at']}"
              f" by {r['last_session'][:8] if r['last_session'] else '?'}{drift}")


def cmd_capture(args) -> int:
    payload = _read_stdin_payload()
    summary = process_payload(payload, args.project)
    if args.verbose:
        print(json.dumps(summary), file=sys.stderr)
    return 0  # a capture problem must never block the session


def cmd_capture_kilo(args) -> int:
    payload = _read_stdin_payload()
    summary = process_kilo_payload(payload, args.project)
    if args.verbose:
        print(json.dumps(summary), file=sys.stderr)
    return 0  # a capture problem must never block the Kilo session


def cmd_session_start(args) -> int:
    payload = _read_stdin_payload()
    try:
        out = context.session_start_output(payload, args.project)
        if out:
            print(out)
    except Exception:
        pass
    return 0


def cmd_reconcile(args) -> int:
    totals = reconcile.reconcile_project(_root(args), force=args.force)
    print(json.dumps(totals, indent=2))
    return 0


def cmd_query(args) -> int:
    st = store.open_store(_root(args))
    rows = st.query(args.pattern, status=args.status, session=args.session,
                    file=args.file, lang=args.lang, limit=args.limit)
    _print_rows(rows, args.json)
    return 0


def cmd_list(args) -> int:
    args.pattern = None
    return cmd_query(args)


def cmd_context(args) -> int:
    print(context.build_context(_root(args), limit=args.limit) or
          "(registry empty — nothing tracked yet)")
    return 0


def cmd_stats(args) -> int:
    print(json.dumps(store.open_store(_root(args)).stats(), indent=2))
    return 0


def cmd_sessions(args) -> int:
    summary: dict[str, dict] = {}
    for e in store.open_store(_root(args)).events():
        s = summary.setdefault(e.get("session_id") or "?", {
            "events": 0, "started": e["ts"], "last": e["ts"]})
        s["events"] += 1
        s["last"] = max(s["last"], e["ts"])
        s["started"] = min(s["started"], e["ts"])
    for sid, s in sorted(summary.items(), key=lambda kv: kv[1]["last"],
                         reverse=True)[:50]:
        print(f"{sid}: {s['events']} events, {s['started'][:19]} .. "
              f"{s['last'][:19]}")
    return 0


def cmd_export(args) -> int:
    st = store.open_store(_root(args))
    rows = st.query(limit=10 ** 9)
    if args.format == "json":
        print(json.dumps(rows, indent=2, default=str))
        return 0
    print("| name | scope | value | status | location | last session |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        val = (r["value_preview"] or "").replace("|", "\\|")
        print(f"| `{r['name']}` | {r['scope']} | `{val}` "
              f"| {r['status']} | {r['file']}:{r['repo_line'] or r['line']} "
              f"| {r['last_session'][:8] if r['last_session'] else ''} |")
    return 0


def cmd_scan(args) -> int:
    totals = scan.scan_project(
        _root(args), progress=lambda n: print(f"  …{n} files",
                                              file=sys.stderr))
    print(json.dumps(totals, indent=2))
    return 0


_LEVEL_ANSI = {"high": "\x1b[91m", "medium": "\x1b[93m", "low": "\x1b[33m"}


def cmd_duplicates(args) -> int:
    st = store.open_store(_root(args))
    dups = duplicates.find_duplicates(st, include_dismissed=args.all)
    total_by = {lv: sum(1 for d in dups if d["level"] == lv)
                for lv in duplicates.LEVEL_ORDER}
    learned_n = sum(1 for d in (dups if args.all else
                    duplicates.find_duplicates(st, include_dismissed=True))
                    if (d.get("review") or {}).get("learned"))
    if args.level:
        dups = [d for d in dups if d["level"] == args.level]
    else:
        floor = duplicates.LEVEL_ORDER[args.min_level]
        dups = [d for d in dups if duplicates.LEVEL_ORDER[d["level"]] >= floor]
    if args.json:
        print(json.dumps(dups, indent=2))
        return 0
    print(f"all suspects: high={total_by['high']} "
          f"medium={total_by['medium']} low={total_by['low']} "
          f"(showing {'level=' + args.level if args.level else args.min_level + '+'}"
          f", {len(dups)} pairs)"
          + (f" · {learned_n} auto-quieted by learned family rules"
             f"{' (shown)' if args.all else ' (--all to show)'}"
             if learned_n else ""))
    if not dups:
        print("no duplicate suspects")
        return 0
    color = sys.stdout.isatty()
    for d in dups:
        tag = d["level"].upper()
        if color:
            tag = _LEVEL_ANSI[d["level"]] + tag + "\x1b[0m"
        dis = f"  [reviewed: {d['review']['verdict']}]" if d["review"] else ""
        print(f"{tag:18} {d['score']:.2f} {d['reason']}{dis}")
        for s in (d["a"], d["b"]):
            scope = f" [{s['scope']}]" if s["scope"] else ""
            print(f"    {s['name']}{scope} = {s['value']}  "
                  f"({s['file']}:{s['line']}, {s['origin']})")
        print(f"    pair: {d['pair_key']}")
    print(f"\n{len(dups)} suspect pair(s). Dismiss with: "
          f"varmem dup-note \"<pair>\" --verdict not_duplicate --note …")
    return 0


def cmd_dup_note(args) -> int:
    st = store.open_store(_root(args))
    st.set_review(args.pair_key, args.verdict, args.note)
    st.save()
    print(f"recorded: {args.verdict}" +
          (f" — {args.note}" if args.note else ""))
    return 0


def cmd_annotate(args) -> int:
    st = store.open_store(_root(args))
    n = st.set_var_note(name=args.name, file=args.file, note=args.note)
    st.save()
    print(f"annotated {n} row(s)")
    return 0 if n else 1


def cmd_accept(args) -> int:
    st = store.open_store(_root(args))
    drifted = [r for r in st.all_rows() if r["status"] == "drifted"]
    if not args.all:
        if not (args.pattern or args.file):
            print("specify a NAME pattern, --file, or --all", file=sys.stderr)
            return 2
        rx = (re.compile("^" + re.escape(args.pattern).replace("\\*", ".*") + "$")
              if args.pattern else None)
        drifted = [r for r in drifted
                   if (rx is None or rx.match(r["name"]))
                   and (args.file is None or args.file in r["file"])
                   and (args.scope is None or r["scope"] == args.scope)]
    n = sum(1 for r in drifted if st.accept_drift(r))
    st.save()
    print(f"accepted (re-baselined) {n} drifted variable(s)")
    return 0 if n else 1


def cmd_report(args) -> int:
    if getattr(args, "json", False):
        # single machine-readable bundle (dups + vars + counts + varLevel)
        # the VS Code extension consumes this to build its tree views
        print(json.dumps(report.report_data(_root(args)), default=str))
        return 0
    out = report.build_report(_root(args),
                              Path(args.out) if args.out else None)
    print(f"report written: {out}")
    return 0


def cmd_repos(args) -> int:
    if args.action == "add":
        added = repos.add_repo(args.path or str(_root(args)))
        print(f"registered: {added}")
    elif args.action == "remove":
        repos.remove_repo(args.path or str(_root(args)))
        print("removed")
    for p in repos.list_repos():
        marker = "" if store.exists(p) else "  (no registry yet — run scan)"
        print(f"- {p}{marker}")
    return 0


def cmd_api(args) -> int:
    if args.action == "add":
        if not args.url:
            print("error: api add needs a URL", file=sys.stderr)
            return 1
        src = repos.add_api_source(args.url, token=args.token, name=args.name,
                                   insecure=args.insecure)
        print(f"registered API source: {src['name']} ({src['url']})")
    elif args.action == "remove":
        if not args.url:
            print("error: api remove needs a URL/id/name", file=sys.stderr)
            return 1
        print("removed" if repos.remove_api_source(args.url) else "no match")
    for s in repos.api_source_entries():
        flags = (" [token]" if s.get("token") else " [no token]") \
            + (" insecure" if s.get("insecure") else "")
        print(f"- {s['id']}  {s['name']}  {s['url']}{flags}")
    return 0


def _print_group(g: dict):
    extras = []
    if g.get("compose_projects"):
        extras.append("compose: " + ", ".join(g["compose_projects"]))
    extras.append("confirmed" if g.get("confirmed") else "unconfirmed")
    print(f"{g['id']}  {g['name']}  ({'; '.join(extras)})")
    by_id = {r["id"]: r for r in repos.repo_entries()}
    for rid in g["repo_ids"]:
        r = by_id.get(rid)
        print(f"    {rid}  {r['name'] if r else '?'}  "
              f"{r['path'] if r else '(missing from registry)'}")


def _resolve_repo_ids(idents: list[str]) -> list[str]:
    out = []
    for ident in idents or []:
        r = repos.find_repo(ident)
        if r is None:
            raise SystemExit(f"error: unknown repo {ident!r} — register it "
                             "first with: varmem repos add <path>")
        out.append(r["id"])
    return out


def cmd_groups(args) -> int:
    if args.action == "list":
        groups = repos.group_entries()
        if args.json:
            print(json.dumps(groups, indent=2))
            return 0
        if not groups:
            print("no groups defined — create one with: "
                  "varmem groups create NAME --repo <path>")
        for g in groups:
            _print_group(g)
        standalone = repos.standalone_repo_ids()
        if standalone:
            by_id = {r["id"]: r for r in repos.repo_entries()}
            print("standalone repos:")
            for rid in standalone:
                print(f"    {rid}  {by_id[rid]['name']}  {by_id[rid]['path']}")
        return 0

    if args.action == "create":
        if not args.target:
            print("error: groups create needs a NAME", file=sys.stderr)
            return 1
        g = repos.create_group(args.target,
                               _resolve_repo_ids(args.repo),
                               compose_projects=args.compose_project or [])
        _print_group(g)
        return 0

    g = repos.find_group(args.target or "")
    if g is None:
        print(f"error: unknown group {args.target!r}", file=sys.stderr)
        return 1
    if args.action == "rename":
        if not args.name:
            print("error: rename needs --name", file=sys.stderr)
            return 1
        _print_group(repos.rename_group(g["id"], args.name))
    elif args.action == "delete":
        repos.delete_group(g["id"])
        print(f"deleted group {g['id']} ({g['name']})")
    elif args.action == "add":
        for rid in _resolve_repo_ids(args.repo):
            g = repos.add_repo_to_group(g["id"], rid)
        _print_group(g)
    elif args.action == "remove":
        for rid in _resolve_repo_ids(args.repo):
            g = repos.remove_repo_from_group(g["id"], rid)
        _print_group(g)
    return 0


def cmd_discover(args) -> int:
    res = discovery.discover()
    if args.json and args.confirm is None:
        print(json.dumps(res, indent=2))
        return 0
    if res["docker"] != "ok":
        print(f"docker: {res['docker']}"
              + (f" — {res['error']}" if res.get("error") else ""))
        print("confirmed groups are unaffected; manual `varmem groups "
              "create` always works.")
        return 0 if res["docker"] == "unavailable" else 1
    if not res["suggestions"]:
        print(f"docker: ok — {res['containers_seen']} running container(s), "
              "no multi-repo group suggestions")
        return 0
    for i, s in enumerate(res["suggestions"], start=1):
        mark = "  [already grouped]" if s["already_grouped"] else ""
        print(f"{i}. {s['name']}{mark}")
        print(f"   repos: {', '.join(s['repo_names'])}")
        print(f"   containers: {', '.join(s['containers'])}")
    if args.confirm is None:
        print("\nconfirm one with: varmem discover --confirm N "
              "[--name 'Group name']")
        return 0
    idx = args.confirm
    if not 1 <= idx <= len(res["suggestions"]):
        print(f"error: --confirm {idx} is out of range", file=sys.stderr)
        return 1
    g = discovery.confirm_suggestion(res["suggestions"][idx - 1],
                                     name=args.name)
    print("confirmed:")
    _print_group(g)
    return 0


def cmd_prompt(args) -> int:
    text, reason = prompts.repo_prompt(
        _root(args), min_level=args.min_level, levels=args.level,
        statuses=args.status, include_reviewed=args.include_reviewed,
        limit=args.limit)
    if text is None:
        print(f"nothing to generate: {reason}", file=sys.stderr)
        return 1
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"prompt written: {out}")
    else:
        print(text)
    return 0


def cmd_live(args) -> int:
    return live.serve(port=args.port, extra_roots=[_root(args)]
                      if args.project else None, open_browser=not args.no_open)


def cmd_serve(args) -> int:
    from . import serve
    return serve.run(host=args.host, port=args.port, data_dir=args.data_dir,
                     token=args.token, no_auth=args.no_auth)


def cmd_ignore_name(args) -> int:
    """Persist 'this name legitimately repeats' — every current and future
    duplicate pair on these names is suppressed (config dup_ignore_names)."""
    result = config.add_ignored_names(_root(args), args.names)
    print(f"ignored names now: {', '.join(result)}")
    return 0


def cmd_ci(args) -> int:
    """Pipeline gate: exit non-zero when duplicate/misalignment suspects at or
    above --fail-on exceed --max. Dismissals in .varmem/reviews.json are
    honored, so intentional pairs a team has accepted never break the build."""
    root = _root(args)
    if not args.no_scan:
        scan.scan_project(root)
        reconcile.reconcile_project(root, force=False)
    st = store.open_store(root)
    dups = duplicates.find_duplicates(st)
    floor = duplicates.LEVEL_ORDER.get(args.fail_on, 3)
    offenders = [d for d in dups
                 if duplicates.LEVEL_ORDER[d["level"]] >= floor]
    passed = len(offenders) <= args.max
    if args.json:
        print(json.dumps({"fail_on": args.fail_on, "max": args.max,
                          "found": len(offenders), "passed": passed,
                          "offenders": offenders}, default=str))
        return 0 if passed else 1
    counts = {lv: sum(1 for d in offenders if d["level"] == lv)
              for lv in duplicates.LEVEL_ORDER}
    print(f"VarAlign CI gate [{'PASS' if passed else 'FAIL'}] "
          f"fail-on={args.fail_on} max={args.max}: {len(offenders)} suspect(s) "
          f"(high={counts['high']} medium={counts['medium']} low={counts['low']})")
    for d in offenders[:50]:
        a, b = d["a"], d["b"]
        print(f"  [{d['level']:<6}] {a['name']} <-> {b['name']}  "
              f"({a['file']}:{a['line']} / {b['file']}:{b['line']})  "
              f"{d['reason']} {d['score']}")
    if len(offenders) > 50:
        print(f"  ... and {len(offenders) - 50} more")
    if not passed:
        print('Consolidate the concept, or accept an intentional pair with: '
              'varmem dup-note "<pair_key>" --verdict not_duplicate')
    return 0 if passed else 1


_HOOK_SNIPPET = {
    "hooks": {
        "PostToolUse": [{
            "matcher": "Write|Edit",
            "hooks": [{
                "type": "command",
                "command": 'python "{varmem}" capture',
                "timeout": 15,
            }],
        }],
        "SessionStart": [{
            "hooks": [{
                "type": "command",
                "command": 'python "{varmem}" session-start',
                "timeout": 30,
            }],
        }],
    }
}


def _render_snippet() -> dict:
    varmem_path = (Path(__file__).resolve().parent.parent / "varmem.py").as_posix()
    snip = json.loads(json.dumps(_HOOK_SNIPPET))
    for ev in snip["hooks"].values():
        for grp in ev:
            for h in grp["hooks"]:
                h["command"] = h["command"].format(varmem=varmem_path)
    return snip


# Kilo Code plugin (opencode-derived engine): a .ts file auto-loaded from
# .kilo/plugins/ fires on tool.execute.after and pipes a Claude-shaped payload
# to `capture-kilo`. The __VARMEM_PY__ placeholder is filled at render time
# (str.replace, not .format — the TS body is full of braces).
# A distinctive sentinel (not just "varmem init --kilo", which a hand-authored
# file could plausibly mention in a comment) marks a file as ours to regenerate.
_KILO_PLUGIN_MARKER = "varmem-kilo-plugin @generated"
_KILO_PLUGIN_TEMPLATE = """\
// __VARMEM_MARKER__ — do not hand-edit; `varmem init --kilo` regenerates it.
// Captures Kilo-written variable assignments into varmem, giving them the same
// per-session provenance as the Claude Code hook. Failures are swallowed: a
// capture problem must never break the Kilo session.

const VARMEM_PY = "__VARMEM_PY__";

// Claude tool_input key -> candidate Kilo/opencode arg names (first present
// wins). The fallbacks keep capture working across Kilo arg-name drift instead
// of silently dropping writes. Only file-writing tools spawn a process.
const TOOL_MAP = {
  write: ["Write", { file_path: ["filePath", "path", "file"],
                     content: ["content", "text"] }],
  edit: ["Edit", { file_path: ["filePath", "path", "file"],
                   old_string: ["oldString", "old_string"],
                   new_string: ["newString", "new_string"] }],
};

export const VarmemPlugin = async ({ directory }) => ({
  "tool.execute.after": async (input, output) => {
    try {
      const mapped = TOOL_MAP[input?.tool];
      if (!mapped) return;                     // read/bash/etc. — ignore
      const [toolName, keymap] = mapped;
      const args = input?.args ?? output?.args ?? {};  // field moved upstream
      const pick = (cands) => {
        for (const c of cands) if (args[c] !== undefined) return args[c];
        return undefined;
      };
      const toolInput = {};
      for (const claudeKey in keymap) {
        const v = pick(keymap[claudeKey]);
        if (v !== undefined) toolInput[claudeKey] = v;
      }
      const payload = {
        hook_event_name: "PostToolUse",
        tool_name: toolName,
        tool_input: toolInput,
        session_id: input?.sessionID,
        cwd: directory,
      };
      const python = process.env.VARMEM_PYTHON ?? "python";
      // Await completion so a burst of sequential edits doesn't launch
      // overlapping capture processes that race (lost-update) on the store —
      // but bound the wait at 15s (matching the Claude hook timeout) so a
      // stuck capture can never hang the Kilo session.
      const proc = Bun.spawn({
        cmd: [python, VARMEM_PY, "capture-kilo"],
        stdin: new Blob([JSON.stringify(payload)]),
        stdout: "ignore",
        stderr: "ignore",
      });
      const timer = setTimeout(() => { try { proc.kill(); } catch {} }, 15000);
      try { await proc.exited; } finally { clearTimeout(timer); }
    } catch { /* never break the session */ }
  },
});
"""


def _render_kilo_plugin() -> str:
    varmem_path = (Path(__file__).resolve().parent.parent / "varmem.py").as_posix()
    # _KILO_PLUGIN_MARKER is the single source of truth for the sentinel — the
    # template embeds it via placeholder so the two can never drift out of sync
    # (a drift would make the regeneration guard reject varmem's own files).
    return (_KILO_PLUGIN_TEMPLATE
            .replace("__VARMEM_MARKER__", _KILO_PLUGIN_MARKER)
            .replace("__VARMEM_PY__", varmem_path))


def _write_kilo_plugin(root: Path) -> int:
    """Write .kilo/plugins/varmem.ts. Regenerate our own file freely; refuse to
    clobber a hand-authored one (mirrors the settings.json guard in cmd_init)."""
    plugin = root / ".kilo" / "plugins" / "varmem.ts"
    if plugin.exists():
        try:
            current = plugin.read_text(encoding="utf-8")
        except Exception:
            current = ""
        if _KILO_PLUGIN_MARKER not in current:
            print(f"ERROR: {plugin} exists and was not generated by varmem; "
                  "not overwriting it.", file=sys.stderr)
            return 1
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(_render_kilo_plugin(), encoding="utf-8")
    print(f"Kilo plugin written to {plugin}")
    print("Restart Kilo (or start a new session) to load it.")
    return 0


def cmd_init(args) -> int:
    """Prepare a target project: .varmem/ store + (optionally) the Claude
    settings hooks and/or the Kilo capture plugin."""
    root = _root(args)
    store.open_store(root).save()  # creates .varmem/ + .gitignore + meta
    repos.add_repo(str(root))
    kilo_requested = getattr(args, "kilo", False)
    # A refused (hand-authored) plugin must not abort the independent settings
    # merge below; remember it and surface it via the exit code at the end.
    kilo_rc = _write_kilo_plugin(root) if kilo_requested else 0
    if not args.write_settings:
        if not kilo_requested:
            print(f"varmem store created at {config.varmem_dir(root)}")
            print("Add to the project's .claude/settings.json:")
            print(json.dumps(_render_snippet(), indent=2))
        return kilo_rc
    sp = root / ".claude" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    settings = {}
    if sp.exists():
        try:
            settings = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            print(f"ERROR: {sp} is not valid JSON; not touching it.",
                  file=sys.stderr)
            return 1
    snip = _render_snippet()
    hooks = settings.setdefault("hooks", {})
    for event, groups in snip["hooks"].items():
        existing = hooks.setdefault(event, [])
        flat = json.dumps(existing)
        for grp in groups:
            marker = grp["hooks"][0]["command"]
            if marker not in flat:  # idempotent merge keyed on command string
                existing.append(grp)
    sp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"varmem hooks written to {sp}")
    print(f"store at {config.varmem_dir(root)}")
    return kilo_rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="varmem",
                                 description="Track AI-written variable "
                                             "assignments across sessions.")
    ap.add_argument("--project", help="project root (default: hook cwd / "
                                      "CLAUDE_PROJECT_DIR / cwd)")
    from . import build_info
    _bi = build_info()
    ap.add_argument("--version", action="version",
                    version=f"varmem {_bi['version']} (build {_bi['build']})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("capture", help="PostToolUse hook entrypoint (stdin JSON)")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_capture)

    p = sub.add_parser("capture-kilo",
                       help="Kilo plugin entrypoint (stdin JSON, Claude "
                            "payload shape; session tagged kilo:<id>)")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_capture_kilo)

    p = sub.add_parser("session-start", help="SessionStart hook entrypoint")
    p.set_defaults(fn=cmd_session_start)

    p = sub.add_parser("reconcile", help="re-sync registry against repo")
    p.add_argument("--force", action="store_true",
                   help="ignore mtime fast-path, recheck everything")
    p.set_defaults(fn=cmd_reconcile)

    for name in ("query", "list"):
        p = sub.add_parser(name, help="search tracked variables")
        if name == "query":
            p.add_argument("pattern", help="name or wildcard, e.g. 'CONFIG_*'")
        p.add_argument("--status", choices=["active", "drifted", "missing",
                                            "file_deleted"])
        p.add_argument("--session")
        p.add_argument("--file")
        p.add_argument("--lang")
        p.add_argument("--limit", type=int, default=50)
        p.add_argument("--json", action="store_true")
        p.set_defaults(fn=cmd_query if name == "query" else cmd_list)

    p = sub.add_parser("context", help="preview the session-start block")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(fn=cmd_context)

    p = sub.add_parser("scan", help="baseline-scan the whole repo "
                                    "(origin='scan', pre-existing code)")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("duplicates", help="detect duplicate/misaligned "
                                          "variables")
    p.add_argument("--all", action="store_true", help="include dismissed")
    p.add_argument("--level", choices=["high", "medium", "low"],
                   help="show exactly this level")
    p.add_argument("--min-level", choices=["high", "medium", "low"],
                   default="medium", help="floor (default medium)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_duplicates)

    p = sub.add_parser("dup-note", help="record an override/note for a pair")
    p.add_argument("pair_key")
    p.add_argument("--verdict", required=True,
                   choices=["not_duplicate", "duplicate", "merged"])
    p.add_argument("--note")
    p.set_defaults(fn=cmd_dup_note)

    p = sub.add_parser("annotate", help="attach a note to a variable")
    p.add_argument("name")
    p.add_argument("--file", help="restrict to file substring")
    p.add_argument("--note", required=True)
    p.set_defaults(fn=cmd_annotate)

    p = sub.add_parser("accept", help="re-baseline drifted variable(s) to the "
                                      "current repo value (resolves an "
                                      "intentional change; re-drifts if it "
                                      "changes again)")
    p.add_argument("pattern", nargs="?", help="variable name (supports *)")
    p.add_argument("--file", help="restrict to a file substring")
    p.add_argument("--scope", help="restrict to an exact scope")
    p.add_argument("--all", action="store_true",
                   help="accept ALL drifted variables in the repo")
    p.set_defaults(fn=cmd_accept)

    p = sub.add_parser("report", help="write self-contained HTML report")
    p.add_argument("--out")
    p.add_argument("--json", action="store_true",
                   help="print report_data JSON (for the VS Code extension) "
                        "instead of writing HTML")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("repos", help="manage the multi-repo registry for `live`")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_repos)

    p = sub.add_parser("api", help="register remote VarAlign APIs whose "
                                   "projects appear in the `live` dashboard")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("url", nargs="?", help="API base URL (add/remove)")
    p.add_argument("--token", help="bearer token for the API")
    p.add_argument("--name", help="display name for the source")
    p.add_argument("--insecure", action="store_true",
                   help="skip TLS verification (self-signed/internal CA)")
    p.set_defaults(fn=cmd_api)

    p = sub.add_parser("groups", help="manage repo groups (control tower)")
    p.add_argument("action", choices=["list", "create", "rename", "delete",
                                      "add", "remove"])
    p.add_argument("target", nargs="?",
                   help="group id or name (create: the new NAME)")
    p.add_argument("--name", help="new name (rename)")
    p.add_argument("--repo", action="append", metavar="PATH_OR_ID",
                   help="member repo (repeatable; create/add/remove)")
    p.add_argument("--compose-project", action="append", metavar="NAME",
                   help="compose project hint (repeatable; create)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_groups)

    p = sub.add_parser("discover", help="suggest groups from running "
                                        "Docker/Compose containers")
    p.add_argument("--json", action="store_true")
    p.add_argument("--confirm", type=int, metavar="N",
                   help="confirm suggestion #N as a persistent group")
    p.add_argument("--name", help="group name override when confirming")
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser("prompt", help="generate a repo-scoped AI "
                                      "remediation prompt (Markdown)")
    p.add_argument("--min-level", choices=["high", "medium", "low"],
                   default="medium", help="duplicate floor (default medium)")
    p.add_argument("--level", action="append",
                   choices=["high", "medium", "low"],
                   help="exact level(s) instead of a floor (repeatable)")
    p.add_argument("--status", action="append",
                   choices=["drifted", "missing", "file_deleted"],
                   help="status findings to include (default: all three)")
    p.add_argument("--include-reviewed", action="store_true",
                   help="also include dismissed/merged pairs")
    p.add_argument("--limit", type=int, default=25,
                   help="max duplicate suspects per pass (0 = no cap; "
                        "re-run to continue through the rest)")
    p.add_argument("--out", help="write to this path instead of stdout")
    p.set_defaults(fn=cmd_prompt)

    p = sub.add_parser("live", help="serve the live multi-repo dashboard "
                                    "(working override/note buttons)")
    p.add_argument("--port", type=int, default=7787)
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(fn=cmd_live)

    p = sub.add_parser("serve", help="run the VarAlign HTTP API "
                                     "(programmatic analyze + project memory)")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind host (k8s: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--data-dir", help="project store root "
                                      "(default: $VARALIGN_DATA_DIR)")
    p.add_argument("--token", help="bearer token (default: $VARALIGN_TOKEN)")
    p.add_argument("--no-auth", action="store_true",
                   help="disable auth (local/dev only; refused off-loopback)")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("ignore-name", help="never flag these names again "
                                           "(persists to .varmem/config.json)")
    p.add_argument("names", nargs="+", help="variable name(s) to ignore")
    p.set_defaults(fn=cmd_ignore_name)

    p = sub.add_parser("ci", help="pipeline gate: non-zero exit when duplicate "
                                  "suspects at/above --fail-on exceed --max")
    p.add_argument("--fail-on", choices=["high", "medium", "low"],
                   default="high", help="lowest level that fails (default high)")
    p.add_argument("--max", type=int, default=0,
                   help="suspects allowed at/above --fail-on before failing (0)")
    p.add_argument("--no-scan", action="store_true",
                   help="evaluate the committed .varmem store as-is (skip scan)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(fn=cmd_ci)

    p = sub.add_parser("stats")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("sessions", help="per-session event summary")
    p.set_defaults(fn=cmd_sessions)

    p = sub.add_parser("export")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("init", help="prepare a project (store + hook snippet)")
    p.add_argument("--write-settings", action="store_true",
                   help="merge hooks into <project>/.claude/settings.json")
    p.add_argument("--kilo", action="store_true",
                   help="write the Kilo Code capture plugin to "
                        "<project>/.kilo/plugins/varmem.ts")
    p.set_defaults(fn=cmd_init)

    args = ap.parse_args(argv)
    return args.fn(args)
