<p align="center"><img src="media/icon.png" width="120" alt="VarAlign"></p>

> Public source-available mirror of the **VarAlign** VS Code extension (Business Source License 1.1). Install from the VS Code Marketplace / Open VSX.

# VarAlign

[![VS Marketplace](https://img.shields.io/visual-studio-marketplace/v/greenyogainc.varalign?label=VS%20Marketplace&color=0e7a3b)](https://marketplace.visualstudio.com/items?itemName=greenyogainc.varalign)
[![Installs](https://img.shields.io/visual-studio-marketplace/i/greenyogainc.varalign?color=0e7a3b)](https://marketplace.visualstudio.com/items?itemName=greenyogainc.varalign)
[![Open VSX](https://img.shields.io/open-vsx/v/greenyogainc/varalign?label=Open%20VSX&color=a60ee5)](https://open-vsx.org/extension/greenyogainc/varalign)
[![License: BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-blue)](LICENSE)

**Catch the duplicate, drifted, and misaligned variables AI coding agents scatter across sessions — right in your editor.**

AI assistants forget. Across sessions they re-introduce a variable that already
exists under another name, let a value drift from the one they wrote last week,
or strand a definition when a file moves. VarAlign tracks every assignment your
assistant writes, scores the duplicates and drift, and hands you a ready-to-paste
fix prompt.

Native tree views — **Duplicates · Variables · Sessions** — over the VarAlign
engine. Detection, scoring, and persistence run in a zero-dependency Python
engine that ships *inside* the extension; the views are a thin, read-through
client.

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

## Connect to a hosted API (optional, licensed)

You don't need this to try VarAlign — local mode above works out of the box, so
test there first. When you're ready to read from a hosted VarAlign API instead of
the bundled engine (your own deployment, or the cloud), fill in these settings.
Point it at **your own** API to keep everything on your infrastructure:

| Setting | Example | Meaning |
|---|---|---|
| `varalign.apiUrl` | `https://varalign.andrea-house.com` | base URL of the API — setting this switches to API mode |
| `varalign.apiToken` | `vk_live_…` | bearer token / license key |
| `varalign.apiProject` | `my-service` | project id (`/v1/projects/{id}`) |
| `varalign.apiAllowInsecure` | `false` | skip TLS verify — only for a self-hosted internal CA |

In API mode the views read `/v1/projects/{id}/report`; **Generate Fix Prompt**
and **Reconcile** call the API. Review verdicts stay read-only over the API
(dismiss/confirm on the machine that owns the project). When `apiUrl` is empty,
VarAlign uses the bundled local engine.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `varalign.pythonPath` | `python` | interpreter used to run the local engine |
| `varalign.corePath` | *(bundled)* | path to `varmem.py`; empty = the bundled engine |
| `varalign.minLevel` | `medium` | lowest duplicate level shown |
| `varalign.showDismissed` | `false` | include dismissed / auto-quieted pairs |
| `varalign.apiUrl` | `""` | hosted API base URL (see above) |
| `varalign.apiToken` | `""` | API token / license key |
| `varalign.apiProject` | `""` | project id on the API |
| `varalign.apiAllowInsecure` | `false` | skip TLS verify (internal CA only) |

## Development

```bash
cd extension
npm install
npm run compile   # press F5 for an Extension Development Host
```

Build a `.vsix` (bundles the engine via `scripts/bundle-engine.js`):

```bash
npx @vscode/vsce package
```

## License

The VarAlign VS Code extension is source-available under the **Business Source
License 1.1** (see the `LICENSE` file): free to use and modify, no reselling or
competing hosted/embedded offering, converting to Apache-2.0 on 2030-07-15. The
underlying VarAlign engine is licensed separately under Apache-2.0.
