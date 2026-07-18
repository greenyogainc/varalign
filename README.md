# VarAlign

Tracks every variable assignment an AI coding agent writes, remembers it
**across sessions**, and keeps each entry **reconciled against what the repo
actually holds** — drift, deletion, and file removal are detected and
surfaced at the start of the next session.

Beyond memory, it detects the LLM failure mode of **duplicate/misaligned
variables** — `MAX_RETRIES` in one session, `RETRY_LIMIT` invented for the
same concept in the next; the same connection string under two names; case
variants of one constant across deploy scripts — with a scored, color-coded
report and permanent override/notes.

This repo has two parts, each under its own license:

- **[`varmem.py` / `varmem/`](varmem/)** — the engine (CLI, Claude Code
  hooks, scan/duplicates/reconcile, HTML report). **Apache-2.0**, free, zero
  dependencies (Python 3.11+ stdlib).
- **[`extension/`](extension/)** — the VS Code extension (TreeViews, report
  panel, jump-to-fix, in-editor Pro features). **Business Source License
  1.1**, source-available — see [`extension/README.md`](extension/README.md).
  Install from the
  [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=greenyogainc.varalign)
  or [Open VSX](https://open-vsx.org/extension/greenyogainc/varalign).

## Works with any editor or AI

Duplicate, drift, and misalignment **detection is file-based** — the engine
scans the repo and reconciles on file changes, so it flags issues in code
written by **any** tool (Claude Code, Kilo, Cursor, Copilot, or by hand) with
zero per-tool setup; the VS Code extension re-runs it automatically as files
change. The only editor-specific piece is per-session **attribution** ("which
agent wrote this, when"): captured via a Claude Code `PostToolUse` hook and a
**Kilo Code plugin** — Kilo-written variables land as `kilo:<session>`
alongside Claude's. Without a hook, writes are still detected on the next scan
— just without session provenance.

## How it works

- **Capture** — a `PostToolUse` hook on `Write|Edit` re-parses the file the
  agent just changed and upserts attributed assignments with session
  provenance. Python is parsed with the stdlib `ast` (exact scopes); JS/TS,
  PowerShell, Bash, C#, Go and `.env` use honest heuristics. Detection is
  **language-aware**: casing roles per language (Go capitals = visibility,
  PowerShell is case-insensitive), aliases/derivations linked not flagged,
  per-language idioms suppressed.
- **Store** — plain files inside the repo at `<repo>/.varmem/` (sharded JSONL
  per source file + `reviews.json` + `events.jsonl`): committable so state
  travels with the repo, diffable in git, and readable by ANY tool — Claude
  Code, Kilo, the VS Code extension, CI — with zero database drivers.
- **Reconcile** — re-extracts tracked files and hash-compares values:
  `active` / `drifted` / `missing` / `file_deleted`. Runs bounded at session
  start, on demand via CLI, and implicitly for any file the agent rewrites.
- **Recall** — a `SessionStart` hook injects a compact block (≤6k chars) into
  the new session: drifted values (agent's vs repo's), missing names, most
  recent assignments.

Hooks always exit 0 — a varmem failure can never break a coding session
(errors land in `.claude/varmem/varmem.log`). Secret-looking values are
redacted by default (hashes kept, so drift detection still works).

## Install into a repo

### Claude Code plugin (no local clone needed)

```
/plugin marketplace add greenyogainc/varalign
/plugin install varalign
```

The plugin bundles the engine (`.claude-plugin/plugin.json` +
`hooks/hooks.json`, using `${CLAUDE_PLUGIN_ROOT}` — never a hardcoded path),
and its hooks apply to every project you use with Claude Code once installed,
with no per-repo setup.

### From a local clone

```bash
python /path/to/varalign/varmem.py --project /path/to/repo init --write-settings
```

Additive and idempotent merge into that repo's `.claude/settings.json`
(project hooks run alongside user-level hooks). Capture starts with the next
Claude Code session there. Omit `--write-settings` to just print the snippet.

### Kilo Code

```bash
python /path/to/varalign/varmem.py --project /path/to/repo init --kilo
```

Writes a plugin to `<repo>/.kilo/plugins/varmem.ts` (auto-loaded by Kilo's
CLI). Restart Kilo; its file writes then capture the same way, attributed as
`kilo:<session>`. Re-running regenerates the plugin; a hand-edited file is
never overwritten. Set `VARMEM_PYTHON` if `python` isn't the interpreter on
Kilo's PATH.

## CLI

```bash
python varmem.py repos add <path>           # register repos for the control tower
python varmem.py live                       # control-tower dashboard on 127.0.0.1:7787
                                            #   groups · triage queue · reviews · AI fix prompts
python varmem.py groups list                # groups (id, name, members, standalone repos)
python varmem.py groups create "Trading platform" --repo <path> [--repo <path>…]
python varmem.py groups rename <id|name> --name "New name"
python varmem.py groups add    <id|name> --repo <path>   # also: remove, delete
python varmem.py discover                   # Docker/Compose group suggestions (read-only)
python varmem.py discover --confirm 1       # persist suggestion #1 as a confirmed group
python varmem.py prompt [--out fix.md]      # repo-scoped AI remediation prompt (Markdown)
python varmem.py prompt --min-level high --status drifted   # narrow the selection
python varmem.py scan                       # baseline: ingest ALL existing code (origin=scan)
python varmem.py duplicates                 # scored duplicate suspects (default: medium+)
python varmem.py duplicates --level high    # only the red ones
python varmem.py dup-note "<pair>" --verdict not_duplicate --note "intentional"
python varmem.py ci --fail-on high          # pipeline gate: non-zero exit on high suspects
python varmem.py annotate NAME --note "canonical retry count"
python varmem.py report                     # self-contained HTML: color-coded list + override UI
python varmem.py query "MAX_*"              # wildcard name search
python varmem.py list --status drifted      # what changed behind the agent's back
python varmem.py reconcile [--force]        # re-sync registry vs repo
python varmem.py context                    # preview the session-start block
python varmem.py sessions                   # per-session activity
python varmem.py export --format md|json    # dump the registry
python varmem.py stats
```

All commands accept `--project <root>` (default: `CLAUDE_PROJECT_DIR`, then cwd).

## CI gate

Fail a pipeline when duplicate / misalignment suspects appear. `varmem ci`
re-scans the checked-out tree (respecting `.gitignore`), honours dismissals in
a committed `.varmem/reviews.json`, and exits non-zero when suspects at or
above `--fail-on` exceed `--max` — a baseline budget for gradual adoption on
an existing codebase. Zero-dependency stdlib, so there is nothing to install
beyond Python.

```yaml
# GitLab CI
varalign:
  image: python:3.14-slim
  script:
    - python varmem.py ci --fail-on high
```

```yaml
# GitHub Actions
      - run: python varmem.py ci --fail-on high --max 0
```

`--json` emits a machine-readable report; `--no-scan` evaluates the committed
`.varmem` store as-is (skip the re-scan).

## Control tower (groups + live dashboard)

`varmem live` serves a local-only (127.0.0.1) control tower over every
registered repo: a persistent sidebar of **groups** (with per-repo issue
counts), a group overview with aggregated metrics and a prioritized action
queue, repo drill-down with paginated/filterable issues and variables,
one-click review verdicts and notes, and **AI fix prompts** — one
independent Markdown artifact per repository (copy or download), never a
blended cross-repo prompt.

Groups live in the machine-local registry `~/.varmem/repos.json` (v2,
versioned; a v1 flat list migrates automatically with a one-time
`repos.json.v1.bak` backup). Repos get stable ids derived from their
canonical path; groups keep their id across renames; a repo can belong to
several groups; ungrouped repos appear under "Standalone repos".

`varmem discover` (and the dashboard's Discovery button) is optional,
best-effort container discovery: when the Docker CLI is present it inspects
running containers' Compose project labels and bind mounts, maps them onto
registered repo roots, and *suggests* groups. Nothing is saved until you
confirm; confirmed groups persist after containers stop; everything keeps
working with no Docker at all.

Isolation contract: duplicate detection runs per repository, reviews/notes
write only to that repo's `.varmem/`, rescan/reconcile/prompt each target
exactly one repo, and group views aggregate counts only — no
cross-repository duplicate pairs, ever.

## Per-project config (optional)

Drop a `<project>/.varmem/config.json` — it is shallow-merged over the
defaults, so list only the keys you want to override. Commit it so the whole
team (and CI) shares the same tuning.

```json
{
  "redact": true,
  "max_file_kb": 1024,
  "context_limit": 25,
  "session_start_reconcile_max_files": 200,
  "exclude_dirs": [".git", "node_modules", "dist", "build", ".venv", ".claude"]
}
```

### Tuning duplicate detection

Three keys shape what the duplicate/alignment scanner treats as noise vs a
real suspect. Reach for them when a repo's own conventions produce false
positives:

```json
{
  "dup_ignore_names": ["handler", "opts"],
  "dup_ignore_crossfile_names": ["DSN", "STATE", "TIMEOUT"],
  "standalone_units": ["lambda/*", "services/*"]
}
```

- **`dup_ignore_names`** — names that repeat everywhere as a matter of course
  in *this* repo (on top of the built-in list like `logger`, `client`,
  `href`). A pair where **both** sides carry an ignored name is never flagged.
  Dismissing a suspect via `varmem dup-note` with a "not a duplicate" verdict
  learns the family so the rest of it quiets automatically.
- **`dup_ignore_crossfile_names`** — names that legitimately recur across
  standalone entry-point scripts (a per-script `DSN`, `STATE`, `OUT`). A
  **cross-file** pair of these is never flagged; same-file collisions still
  surface.
- **`standalone_units`** — for repos whose subtrees are **independently
  deployable and cannot share code** (per-handler Lambdas, micro-service
  dirs, vendored sub-apps). Each pattern is a `/`-separated path prefix where
  `*` matches exactly one segment: `"lambda/*"` makes every child of
  `lambda/` its own unit. A duplicate pair whose two sides live in
  **different** units is never flagged. Auto-detected by default when a repo
  has ≥2 top-level directories that each carry a deploy/package manifest.

## Tests

```bash
python tests/run_tests.py     # extractors, capture (Claude + Kilo), reconcile,
                              # registry migration, groups, discovery, prompt
                              # determinism/redaction, two-repo isolation,
                              # live-server HTTP round-trips
```
