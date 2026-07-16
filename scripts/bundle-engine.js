'use strict';
// Bundle the VarAlign Python engine into the extension so the packaged .vsix
// works with ZERO configuration — no corePath to set, just Python on PATH.
//
// This runs at package time via the `vscode:prepublish` script, so `engine/`
// is a BUILD ARTIFACT: it is gitignored and never committed (build-time-
// everything — one reproducible copy per package, no stale blob in git).
const fs = require('fs');
const path = require('path');

// Engine source: the monorepo checkout (../../varmem) when developed inside the
// VarAlign monorepo, else a vendored ./engine-src (the public standalone repo
// ships an Apache-2.0 copy of the engine so it builds without the monorepo).
const monoRoot = path.resolve(__dirname, '..', '..');
const vendored = path.resolve(__dirname, '..', 'engine-src');
const srcRoot = fs.existsSync(path.join(monoRoot, 'varmem.py')) ? monoRoot : vendored;
const dest = path.resolve(__dirname, '..', 'engine');

// The public source mirror ships only the extension (no engine). Degrade
// gracefully: build a dev .vsix without a bundled engine — users then set
// varalign.corePath (or varalign.apiUrl). Official releases build in the
// monorepo, where ../../varmem exists and IS bundled.
if (!fs.existsSync(path.join(srcRoot, 'varmem.py'))) {
  console.warn('[bundle-engine] no engine source found — packaging WITHOUT a '
    + 'bundled engine; set varalign.corePath or varalign.apiUrl at runtime.');
  process.exit(0);
}

// Skip Python bytecode caches — source only.
const keep = (src) => !src.includes('__pycache__')
  && !src.endsWith('.pyc') && !src.endsWith('.pyo');

fs.rmSync(dest, { recursive: true, force: true });
fs.mkdirSync(dest, { recursive: true });
fs.copyFileSync(path.join(srcRoot, 'varmem.py'), path.join(dest, 'varmem.py'));
fs.cpSync(path.join(srcRoot, 'varmem'), path.join(dest, 'varmem'),
  { recursive: true, filter: keep });

console.log('[bundle-engine] copied engine -> ' + dest);
