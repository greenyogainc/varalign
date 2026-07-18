# VarAlign

[![Open VSX](https://img.shields.io/open-vsx/v/greenyogainc/varalign?label=Open%20VSX&color=a60ee5)](https://open-vsx.org/extension/greenyogainc/varalign)
[![Open VSX Downloads](https://img.shields.io/open-vsx/dt/greenyogainc/varalign?label=downloads&color=0e7a3b)](https://open-vsx.org/extension/greenyogainc/varalign)
[![License: BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-blue)](LICENSE)

Also on the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=greenyogainc.varalign).

**Catch the duplicate, drifted, and misaligned variables AI coding agents scatter across sessions — right in your editor. 100% local: your code never leaves your machine.**

![VarAlign demo: duplicates detected, a pair reviewed and dismissed, and a fix prompt generated — all in VS Code](https://greenyogainc.com/images/varalign/demo.gif)

AI assistants forget. Across sessions they re-introduce a variable that already
exists under another name, let a value drift from the one they wrote last week,
or strand a definition when a file moves. VarAlign tracks every assignment your
assistant writes, scores the duplicates and drift, and hands you a ready-to-paste
fix prompt.

Native tree views — **Duplicates · Variables · Sessions** — over the VarAlign
engine. Detection, scoring, and persistence run in a zero-dependency Python
engine that ships *inside* the extension; the views are a thin, read-through
client. Everything runs on your machine — no cloud, no telemetry, no code
upload — so it works in locked-down and air-gapped environments.

## What's new

- **1.0.0** — New: a Claude Code plugin (`/plugin install varalign`) —
  capture/session-start hooks now resolve via `${CLAUDE_PLUGIN_ROOT}` instead
  of a path baked in at setup time, so they work on any machine.
- **0.4.6** — Fixed a crash when accepting a drift by name, and stopped
  truncated multi-line values (`createFileSystemWatcher(`, `useState(`) from
  being flagged as duplicates of one another.
- **0.4.5** — Live refresh: the views now auto-reconcile in the background when
  you edit source files, so a duplicate or drift you just fixed clears from the
  sidebar without a manual rescan — even when the edit came from an AI agent.
- **0.4.4** — Fewer false positives: a namespaced constant family — one shared
  prefix, a different final word (`HTTP_GET`/`HTTP_POST`,
  `KILO_PLUGIN_MARKER`/`KILO_PLUGIN_TEMPLATE`) — is recognized as a deliberate
  family instead of flagged as a duplicate.
- **0.4.3** — Two fixes: variables computed from the same shared constant
  (`SEVERITIES = SEVERITY_ORDER` vs `new Set(SEVERITY_ORDER)`) are no longer
  flagged as a mismatch; and review verdicts + config now commit by default
  while the churny registry is ignored — no `.varmem/*` rule that drops your
  dismissals.
- **0.4.2** — Docs: configuring large multi-service repos — declare
  `standalone_units` for scripts-style monorepos (no per-directory manifests) so
  cross-tool copy-paste stops flooding the report.
- **0.4.1** — Fewer false-positive duplicate flags: two variables that only
  share a bare empty-container idiom (`defaultdict(list)`, `OrderedDict()`…) are
  no longer paired as the same value; a genuinely meaningful shared value still
  flags.
- **0.4.0** — **Kilo Code capture.** Run `varmem init --kilo` and Kilo-written
  variables get the same per-session provenance as Claude Code, shown as
  `kilo:…`, so duplicate and drift tracking know which agent introduced each
  value.
- **0.3.4** — Resolve reviewed drift: **accept** an intentional change and it
  becomes the new baseline — it drops from the report and reappears only if it
  changes again, so old settled refactors stop lingering.
- **0.3.3** — Reliable, resumable dismissals: the fix prompt works one suspect
  at a time, records each verdict immediately (CLI or a committable ledger),
  chunks large finding sets into resumable batches, and matches a verdict even
  if its key was written with a different order or path style.
- **0.3.2** — Multi-component repos are auto-detected: constants intentionally
  copied across independently-shipped top-level folders (a worker, an extension,
  a signing lib…) are no longer flagged as duplicates, with no configuration.
- **0.3.1** — The views and fix prompt stay clean automatically: stale entries
  for deleted or ignored files are pruned on scan, and the fix prompt shows only
  actionable duplicates and value drift, not routine file removals.
- **0.3.0** — Fewer false-positive duplicate flags (truncated URLs, SVG/HTML
  attributes, mirrored `.env.example` keys, and coincidental shared numbers no
  longer trigger), plus a new `standalone_units` setting to silence expected
  cross-copy duplicates in repos whose subtrees deploy independently.
- **0.2.1** — Python module metadata (`__all__`, `__version__`, …) is no longer
  flagged as a duplicate.
- **0.2.0** — The status bar now shows the bundled engine's build, so a stale
  install is easy to spot.
- **0.1.14** — Fewer false-positive duplicate flags: ORM/dataclass column
  declarations and sibling config families (secrets, cache keys, symmetric
  knobs) no longer over-trigger.
- **0.1.13** — More precise variable extraction: JSX-aware parsing,
  brace-language function scopes, and Rust/Java support.
- **0.1.12** — The baseline scan now runs automatically the first time a
  project is opened.
- **0.1.11** — Demo GIF added to the listing.
- **0.1.10** — Fixed a Windows encoding crash in "Generate Fix Prompt."
- **0.1.9** — Fully local — no server or cloud dependency.
- **0.1.8** — Offline Pro license verification; smaller bundled extension.
- **0.1.7** — Detection tuning to reduce noise; cross-runtime license test
  coverage.
- **0.1.6** — Pro "Merge Variables" action (license-gated).
- **0.1.5** — Listing polish: gallery banner, keywords, category, and badges.
- **0.1.4** — Offline license signing and verification.
- **0.1.3** — "Fix with AI" — hand a duplicate's fix prompt straight to Claude
  Code or Kilo Code.
- **0.1.2** — Engine bundled into the extension (zero-config setup) and a brand
  icon.
- **0.1.0** — Initial release: Duplicates, Variables, and Sessions tree views.

The full changelog also renders in the **Changelog** tab of the Open VSX and
Marketplace listings (from [CHANGELOG.md](CHANGELOG.md)).

## Features

- **Duplicates** — High / Medium / Low groups. Expand a pair to see both sides;
  right-click to **Dismiss (not a duplicate)**, **Confirm**, or **Dismiss with
  note**. Verdicts persist, and dismissing one member auto-quiets the whole
  family so you review each pattern once.
- **Variables** — every tracked assignment, grouped by file and coloured by its
  worst duplicate level; click to jump to the definition.
- **Sessions** — what each AI session introduced or changed.
- **Status bar** — `VarAlign: N high`; click to focus the Duplicates view.
- **Generate Fix Prompt** — a repo-scoped remediation prompt in a new tab,
  ready to paste back to your assistant.
- **Fix with AI** — hands a targeted consolidation prompt to Claude Code or
  Kilo Code, whichever you have open.
- Auto-refreshes when the store changes (a hook or the CLI wrote to it).

## Getting started

1. Install VarAlign.
2. Make sure **Python 3.11+** is on your PATH — the extension bundles the engine,
   so there's nothing else to install or point at.
3. Open a repo and click the **VarAlign** chip in the activity bar. VarAlign
   scans the workspace and starts tracking.

That's it — you're running locally, and every byte stays on your machine.

> VarAlign keeps its tracking data in a `.varmem/` folder at your repo root. The
> extension adds `.varmem/` to your `.gitignore` automatically (git repos only).
> If it can't, add this line yourself:
> ```
> .varmem/
> ```

## VarAlign Pro

Pro unlocks **Merge Variables**: right-click a duplicate pair and VarAlign picks
the canonical name, rewrites the references, and removes the duplicate
definition — in your editor, on your machine.

Licenses are verified **offline** (an Ed25519-signed key, checked locally with a
14-day grace period past expiry — nothing is ever sent anywhere, so Pro works
air-gapped too). Activate with **VarAlign: Enter License**; check anytime with
**VarAlign: License Status**.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `varalign.pythonPath` | `python` | interpreter used to run the local engine |
| `varalign.corePath` | *(bundled)* | path to `varmem.py`; empty = the bundled engine |
| `varalign.minLevel` | `medium` | lowest duplicate level shown |
| `varalign.showDismissed` | `false` | include dismissed / auto-quieted pairs |
| `varalign.aiTool` | `auto` | assistant for **Fix with AI** (`auto`/`claude`/`kilo`) |
| `varalign.licenseKey` | `""` | Pro license key (`VL1.…`), verified offline |
| `varalign.apiUrl` | `""` | optional self-hosted API (see below) |
| `varalign.apiToken` | `""` | bearer token for that API |
| `varalign.apiProject` | `""` | project id on that API |
| `varalign.apiAllowInsecure` | `false` | skip TLS verify (internal CA only) |

## Large / multi-service repos (monorepos)

VarAlign treats independently-deployed components as separate **units** and does
not flag the same constant copied across them (a license key replicated in an
edge worker, a lambda, and a CLI is expected, not drift). It **auto-detects**
these units when it can see the boundaries — two or more top-level directories
that each contain a package/deploy manifest (`package.json`, `pyproject.toml`,
`go.mod`, `serverless.yml`, …). A bare `requirements.txt` is **not** recognized,
so a Python monorepo that uses only that won't auto-detect — switch to
`pyproject.toml` or declare the units explicitly (below).

**The quirk:** a *scripts-style* monorepo — many independent tools under
`scripts/`, `services/`, `lambdas/` with **no per-directory manifest** — gives
auto-detection nothing to latch onto, so the whole repo is treated as one
codebase and you get a very high finding count from cross-tool copy-paste.
Declare the units explicitly in `.varmem/config.json`:

```json
{
  "standalone_units": ["scripts/*", "services/*", "lambdas/*", "*"],
  "exclude_dirs": [
    ".git", "node_modules", "dist", "build", "out", "target",
    ".venv", "venv", "__pycache__", "vendor", ".next", ".claude",
    ".varmem", ".kilo",
    "tests", "migrations"
  ]
}
```

`scripts/*` makes each child of `scripts/` its own unit; **longest match wins**,
so nest as deep as your independent components go — e.g.
`anythingllm/agent-skills/*` when a folder holds many self-contained skills. The
trailing `"*"` makes every *remaining* top-level directory a unit too —
convenient, but it also isolates a **shared** top-level module (`common/`,
`lib/`), which would hide a constant a service copied instead of importing;
**omit `"*"`** (and list units explicitly) if you have such a shared directory.
Cross-unit pairs are suppressed; duplicates *within* a unit still surface.

`exclude_dirs` **replaces** the built-in list — it does not merge — so the block
above is the **complete** default set (copy it as-is) plus your own additions
(`tests`, `migrations`, …); drop an entry and that directory gets scanned again.
On one real 15k-variable monorepo, the units above took the default (medium+)
findings from ~4,400 down to ~1,900, and to ~1,200 once per-collection folders
(like `agent-skills/*`) were nested too.

## Optional: read from a self-hosted API

Teams that run the engine centrally (`python varmem.py serve` on their own
infrastructure) can point the views at it by setting `varalign.apiUrl`,
`varalign.apiToken`, and `varalign.apiProject`. The views then read
`/v1/projects/{id}/report`; review verdicts stay read-only over the API. Leave
`apiUrl` empty (the default) for the fully local experience.

## FAQ

<!-- FAQ:START (generated from https://www.greenyogainc.com/varalign/ at build time by scripts/build-faq.js — do not edit by hand) -->

### What is VarAlign?

VarAlign is a VS Code extension plus a zero-dependency engine that tracks every variable assignment AI coding agents write in your repositories, across sessions. It detects duplicate concepts (MAX_RETRIES vs RETRY_LIMIT), values that drifted apart, and misaligned naming — and gives you a review workflow to dismiss, confirm, or fix each finding.

### Does my code leave my machine?

No. Detection, scoring, and storage all run locally — the engine ships inside the extension, there is no cloud analysis, no telemetry, and no code upload. The analysis is deterministic (no LLM involved), so it also works in locked-down and air-gapped environments.

### Which AI coding tools does it work with?

VarAlign installs a capture hook for Claude Code today, with a Kilo Code adapter in progress — both agents share one per-repo registry. It also works with any workflow via a full-repo baseline scan, so you can use it even without a supported agent hook.

### How is it different from a linter or PR review bot?

Linters and PR bots see one file or one diff at a time. VarAlign's findings come from a persistent cross-session registry, so it catches the inconsistency that only exists across sessions and across agents — the exact class of problem diff-scoped tools miss. Its language-aware engine knows Python constant casing, Go export capitalization, and PowerShell case-insensitivity, so conventions are not flagged as duplicates.

### What does Pro add, and how does the license work?

Pro ($79 per year, one seat) unlocks Merge Variables — pick a duplicate pair and VarAlign consolidates it: canonical name, references rewritten, duplicate definition removed. The license is an offline Ed25519-signed key delivered by email: paste it into VS Code with the 'VarAlign: Enter License' command. Verification happens on your machine with a 14-day grace period past expiry — no account, no phone-home.

### What is the AI Code Alignment Audit?

A fixed-fee engagement ($1,500–$5,000 per repository, by size) where we run VarAlign across your codebase, triage every finding with notes, separate real issues from intentional differences, and deliver a prioritized report with a remediation path. It is the fastest way to know where naming and configuration drift are accumulating in an AI-heavy codebase.

### Is VarAlign open source?

The engine is Apache-2.0 (free forever, the adoption core). The VS Code extension is source-available under the Business Source License 1.1 — free to use and modify, no reselling or competing hosted offering, converting to Apache-2.0 in 2030. The extension source is public on GitHub.

<!-- FAQ:END -->

## Development

```bash
cd extension
npm install
npm run compile   # press F5 for an Extension Development Host
```

Build a `.vsix` (minified bundle + engine via `vscode:prepublish`):

```bash
npx @vscode/vsce package
```

## License

The VarAlign VS Code extension is source-available under the **Business Source
License 1.1** (see the `LICENSE` file): free to use and modify, no reselling or
competing hosted/embedded offering, converting to Apache-2.0 on 2030-07-15. The
underlying VarAlign engine is licensed separately under Apache-2.0.
