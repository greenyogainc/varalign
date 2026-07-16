<p align="center"><img src="media/icon.png" width="120" alt="VarAlign"></p>

> Public source-available mirror of the **VarAlign** VS Code extension (Business Source License 1.1). Install from the VS Code Marketplace / Open VSX.

# VarAlign

[![Open VSX](https://img.shields.io/open-vsx/v/greenyogainc/varalign?label=Open%20VSX&color=a60ee5)](https://open-vsx.org/extension/greenyogainc/varalign)
[![Open VSX Downloads](https://img.shields.io/open-vsx/dt/greenyogainc/varalign?label=downloads&color=0e7a3b)](https://open-vsx.org/extension/greenyogainc/varalign)
[![License: BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-blue)](LICENSE)

Also on the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=greenyogainc.varalign).

**Catch the duplicate, drifted, and misaligned variables AI coding agents scatter across sessions — right in your editor. 100% local: your code never leaves your machine.**

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

## Optional: read from a self-hosted API

Teams that run the engine centrally (`python varmem.py serve` on their own
infrastructure) can point the views at it by setting `varalign.apiUrl`,
`varalign.apiToken`, and `varalign.apiProject`. The views then read
`/v1/projects/{id}/report`; review verdicts stay read-only over the API. Leave
`apiUrl` empty (the default) for the fully local experience.

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
