'use strict';
// Populate the README's FAQ from the canonical FAQ on the product site, at build
// time (runs in vscode:prepublish, so the packaged .vsix — and thus the Open VSX
// / Marketplace listing — always matches the website). The site's schema.org
// FAQPage JSON-LD is the source of truth.
//
// Degrades gracefully: on ANY fetch/parse failure the last-built FAQ (already
// committed between the markers) is kept and the build continues — a network
// hiccup during packaging never breaks a release. Same rationale as
// bundle-engine.js's graceful degradation.
const fs = require('fs');
const path = require('path');

const SOURCE = 'https://www.greenyogainc.com/varalign/';
const README = path.resolve(__dirname, '..', 'README.md');
const START = '<!-- FAQ:START';      // prefix of the start-marker line
const END = '<!-- FAQ:END -->';
const UA = 'Mozilla/5.0 (compatible; VarAlign-build/1.0; '
  + '+https://open-vsx.org/extension/greenyogainc/varalign)';

const ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
  mdash: '—', ndash: '–', hellip: '…',
  rsquo: '’', lsquo: '‘', ldquo: '“', rdquo: '”',
};

function decode(s) {
  return String(s)
    .replace(/<[^>]+>/g, '')                                     // strip HTML tags
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/&([a-z]+);/gi, (_, e) => ENTITIES[e.toLowerCase()] ?? `&${e};`)
    .replace(/\s+/g, ' ')
    .trim();
}

function extractFaq(html) {
  const blocks = [...html.matchAll(
    /<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)];
  const out = [];
  const walk = (node) => {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (!node || typeof node !== 'object') { return; }
    if (node['@type'] === 'FAQPage') {
      const qs = Array.isArray(node.mainEntity) ? node.mainEntity
        : node.mainEntity ? [node.mainEntity] : [];
      for (const q of qs) {
        const question = decode(q && q.name);
        const ans = q && q.acceptedAnswer;
        const answer = decode(ans && ans.text);
        if (question && answer) { out.push({ question, answer }); }
      }
    }
    Object.values(node).forEach(walk);
  };
  for (const b of blocks) {
    let data;
    try { data = JSON.parse(b[1].trim()); } catch { continue; }
    walk(data);
  }
  const seen = new Set();                                        // de-dupe, keep order
  return out.filter((f) => (seen.has(f.question) ? false : seen.add(f.question)));
}

function render(faqs) {
  return faqs.map((f) => `### ${f.question}\n\n${f.answer}`).join('\n\n');
}

async function main() {
  const readme = fs.readFileSync(README, 'utf-8');
  const startIdx = readme.indexOf(START);
  const endIdx = readme.indexOf(END);
  if (startIdx === -1 || endIdx === -1 || endIdx < startIdx) {
    console.warn('[build-faq] FAQ markers not found in README.md — skipping.');
    return;
  }

  let faqs;
  try {
    const res = await fetch(SOURCE, { headers: { 'User-Agent': UA } });
    if (!res.ok) { throw new Error(`HTTP ${res.status}`); }
    faqs = extractFaq(await res.text());
  } catch (e) {
    console.warn(`[build-faq] could not refresh FAQ from ${SOURCE} (${e.message}) `
      + '— keeping the committed FAQ.');
    return;                                                      // graceful degradation
  }
  if (!faqs.length) {
    console.warn('[build-faq] no FAQPage entries found — keeping the committed FAQ.');
    return;
  }

  const lineEnd = readme.indexOf('\n', startIdx);                // preserve START marker line
  const next = readme.slice(0, lineEnd + 1)
    + '\n' + render(faqs) + '\n\n'
    + readme.slice(endIdx);
  if (next !== readme) {
    fs.writeFileSync(README, next);
    console.log(`[build-faq] wrote ${faqs.length} FAQ entries into README.md`);
  } else {
    console.log('[build-faq] FAQ already up to date.');
  }
}

// Never let FAQ generation break packaging — the committed FAQ is the fallback.
main().catch((e) => {
  console.warn(`[build-faq] unexpected error (${e && e.message}) — skipping.`);
  process.exit(0);
});
