# Changelog

## 1.0.0
- New: a Claude Code plugin (`/plugin install varalign`) — the engine's hooks
  now resolve via `${CLAUDE_PLUGIN_ROOT}` instead of a path baked in at setup
  time, so capture/session-start work on any machine the plugin is installed
  on, not just the one it was configured from.

## 0.4.6
- Fixed a crash: accepting a drift by name (`varmem accept <name>`, as suggested in the fix prompts) failed with a `NameError` because the CLI didn't import `re`. It now works.
- Fewer false positives: a variable whose value is a multi-line call or collection was captured as only the truncated head (`createFileSystemWatcher(`, `useState(`, `[`), so two different multi-line expressions looked identical and paired as the same value. A truncated head no longer anchors a same-value match.

## 0.4.5
- Findings now clear automatically after you fix them. The views watch your source files and reconcile in the background (debounced, mtime-fast), so a duplicate or drift you just resolved drops off without a manual **Full Rescan** — no more stale sidebar after an edit, whether it came from you or an AI agent that writes files directly. (Previously the views only refreshed when the registry itself changed, so edits that weren't captured by a hook left the sidebar frozen at the last scan.)

## 0.4.4
- Fewer false positives: a namespaced family of constants — a shared prefix with a different final word (`KILO_PLUGIN_MARKER` / `KILO_PLUGIN_TEMPLATE`, `HTTP_GET` / `HTTP_POST`, `MAX_RETRIES` / `MAX_TIMEOUT`) — is recognized as one deliberate family and sunk to low instead of flagged as a medium token-overlap. (The mirror shape — a shared suffix with a different first word, like `ISSUE_PAGE_SIZE` / `USER_PAGE_SIZE` — still surfaces, since those may want aligning.)

## 0.4.3
- Fewer false positives: two variables computed from the same shared constant — e.g. `const SEVERITIES = SEVERITY_ORDER` and `new Set(SEVERITY_ORDER)` — are now recognized as one source of truth (a shared-source link), not a value-mismatch. Deduping into a shared constant no longer leaves the finding behind.
- Review verdicts (`reviews.json`) and per-repo config (`config.json`) are now committed by default while the machine-generated registry is ignored — so a `.varmem/*` rule in your repo-root `.gitignore` (which silently dropped your dismissals) is no longer needed.

## 0.4.2
- Docs: configuring large multi-service repos / monorepos — when auto-detection can't see per-directory manifests (a scripts-style monorepo), declare `standalone_units` so cross-tool copy-paste stops flooding the report.

## 0.4.1
- Fewer false-positive duplicate flags: two variables that merely share a bare empty-container idiom (`defaultdict(list)`, `defaultdict(int)`, `OrderedDict()`…) are no longer paired as the same value — the element type of an empty container carries no signal. A genuinely meaningful shared value still flags.

## 0.4.0
- Kilo Code sessions are now captured. Run `varmem init --kilo` in a repo and Kilo-written variables get the same per-session provenance as Claude Code — shown as `kilo:…` — so duplicate and drift tracking know which agent introduced each value.

## 0.3.4
- Drift you've reviewed can now be resolved. Accept an intentional change (a completed refactor) and it's adopted as the new baseline: it drops out of the report and only reappears if the value changes again — so the report stays actionable instead of accumulating old, already-settled changes.

## 0.3.3
- The fix prompt now drives dismissals reliably at scale: it works one suspect at a time and records each verdict immediately (via the CLI or a committable ledger), chunks large finding sets into resumable batches, and matches a recorded verdict even if its key was written with a different order or path style — so nothing is dropped or double-counted.

## 0.3.2
- Multi-component repos are detected automatically: when several top-level folders each ship independently (a Cloudflare worker, a VS Code extension, a signing library…), constants intentionally copied across them are no longer flagged as duplicates — no configuration needed.

## 0.3.1
- The views and fix prompt now stay clean on their own: stale entries for deleted or ignored files (e.g. build artifacts) are pruned automatically on scan, and the fix prompt focuses on actionable duplicates and value drift instead of routine file removals.

## 0.3.0
- Fewer false-positive duplicate flags: URLs are no longer truncated, SVG/HTML attributes and mirrored `.env.example` keys are ignored, and coincidental shared numbers or per-module env-var reads no longer match. New `standalone_units` setting silences expected cross-copy duplicates in repos whose subtrees deploy independently.

## 0.2.1
- Python module metadata (`__all__`, `__version__`, …) is no longer flagged as a duplicate.

## 0.2.0
- The status bar now shows the bundled engine's build, so a stale install is easy to spot.

## 0.1.14
- Fewer false-positive duplicate flags: ORM/dataclass column declarations and sibling config families (secrets, cache keys, symmetric knobs) no longer over-trigger.

## 0.1.13
- More precise variable extraction: JSX-aware parsing, brace-language function scopes, and Rust/Java support.

## 0.1.12
- The baseline scan now runs automatically the first time a project is opened.

## 0.1.11
- Demo GIF added to the listing.

## 0.1.10
- Fixed a Windows encoding crash in "Generate Fix Prompt."

## 0.1.9
- Fully local — no server or cloud dependency.

## 0.1.8
- Offline Pro license verification; smaller bundled extension.

## 0.1.7
- Detection tuning to reduce noise; cross-runtime license test coverage.

## 0.1.6
- Pro "Merge Variables" action (license-gated).

## 0.1.5
- Listing polish: gallery banner, keywords, category, and badges.

## 0.1.4
- Offline license signing and verification.

## 0.1.3
- "Fix with AI" — hand a duplicate's fix prompt straight to Claude Code or Kilo Code.

## 0.1.2
- Engine bundled into the extension (zero-config setup) and a brand icon.

## 0.1.0
- Initial release: Duplicates, Variables, and Sessions tree views.
