// Pro features, computed LOCALLY and gated by the OFFLINE license (see
// license.ts / hasFeature). All-local: no server, no network, airgap-friendly.
// The license can't be forged (Ed25519 — the private key never ships); a
// source-patched bypass is a Business Source License violation. A server check
// would break the airgap/local-first story, so offline enforcement is correct.
import type { Side } from './core';

export interface MergePlan {
  keep: { name: string; file: string; line: number };
  drop: { name: string; file: string; line: number };
  rename: { from: string; to: string };
  canonical_value: string | null;
  value_conflict: boolean;
  note: string;
}

function score(name: string): [number, number] {
  const tokens = name.split(/[_.]|(?<=[a-z0-9])(?=[A-Z])/).filter(Boolean).length;
  return [tokens, name.length];
}

/** Plan the consolidation of a duplicate pair. Canonical = the more descriptive
 *  name (more tokens, then longer, then lexical) so DATABASE_URL beats DB_URL,
 *  deterministically — mirrors the engine's own rule. Pure/local; no I/O. */
export function mergePlan(a: Side, b: Side): MergePlan {
  const [sa, sb] = [score(a.name), score(b.name)];
  let keep = a, drop = b;
  const bWins = sb[0] > sa[0]
    || (sb[0] === sa[0] && sb[1] > sa[1])
    || (sb[0] === sa[0] && sb[1] === sa[1] && b.name < a.name);
  if (bWins) { keep = b; drop = a; }
  const conflict = (a.value ?? null) !== (b.value ?? null);
  return {
    keep: { name: keep.name, file: keep.file, line: keep.line },
    drop: { name: drop.name, file: drop.file, line: drop.line },
    rename: { from: drop.name, to: keep.name },
    canonical_value: keep.value ?? null,
    value_conflict: conflict,
    note: `Keep "${keep.name}" (${keep.file}:${keep.line}); rewrite `
      + `"${drop.name}" -> "${keep.name}" and remove its definition`
      + (conflict ? '. Values differ -- review before applying.' : '.'),
  };
}
