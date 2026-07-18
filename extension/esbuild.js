'use strict';
// Production bundle: esbuild bundles the whole extension into ONE minified
// out/extension.js, so the shipped .vsix is not clean source (a light anti-
// patch measure for the Pro gate). Dev builds still use `npm run compile`
// (tsc) for readable, debuggable output under F5.
const esbuild = require('esbuild');
const fs = require('fs');
const minify = process.argv.includes('--minify');

fs.rmSync('out', { recursive: true, force: true });
esbuild.build({
  entryPoints: ['src/extension.ts'],
  bundle: true,
  outfile: 'out/extension.js',
  platform: 'node',
  format: 'cjs',
  target: 'node18',
  external: ['vscode'],
  minify,
  sourcemap: !minify,
  logLevel: 'warning',
}).then(() => console.log('[esbuild] bundled' + (minify ? ' (minified)' : '')))
  .catch(() => process.exit(1));
