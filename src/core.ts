// Bridge to the VarAlign engine. The extension is a thin client: all
// detection/scoring/persistence stays in the engine. It can reach the engine
// two ways:
//   * local  — shell out to the Python core (varmem.py) over the current repo;
//   * api    — call a hosted VarAlign API (your own deployment or the cloud)
//              when `varalign.apiUrl` is set. Licensed feature.
// The report payload is identical in both modes (report.report_data), so the
// tree views don't care which backend produced it.
import { execFile } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import * as path from 'path';
import * as vscode from 'vscode';

export interface Side {
  id: string; name: string; file: string; scope: string; line: number;
  value: string; status: string; origin: string; lang: string;
  note?: string | null; repo_value?: string; last_session?: string;
}
export interface Review { verdict: string; note?: string | null; learned?: boolean; }
export interface Dup {
  pair_key: string; score: number; level: 'high' | 'medium' | 'low';
  reason: string; review: Review | null; a: Side; b: Side;
}
export interface ReportData {
  name: string; project: string; generated: string;
  dups: Dup[]; vars: Side[]; varLevel: Record<string, string>;
  counts: Record<string, number>;
  levelCounts: Record<string, number>;
  unreviewedLevels?: Record<string, number>;
  learnedSuppressed?: number;
}

function config() {
  const c = vscode.workspace.getConfiguration('varalign');
  return {
    python: c.get<string>('pythonPath') || 'python',
    core: c.get<string>('corePath') || '',
    minLevel: c.get<string>('minLevel') || 'medium',
    showDismissed: c.get<boolean>('showDismissed') || false,
    apiUrl: (c.get<string>('apiUrl') || '').trim().replace(/\/+$/, ''),
    apiToken: (c.get<string>('apiToken') || '').trim(),
    apiProject: (c.get<string>('apiProject') || '').trim(),
    apiAllowInsecure: c.get<boolean>('apiAllowInsecure') || false,
  };
}

let _extensionPath = '';
/** Set once on activation so we can locate the bundled engine. */
export function setExtensionPath(p: string): void { _extensionPath = p; }

/** Engine shipped inside the .vsix (works with zero configuration). */
function bundledCore(): string {
  return _extensionPath ? path.join(_extensionPath, 'engine', 'varmem.py') : '';
}

/** Resolved local engine: the configured path, else the bundled engine. */
export function corePath(): string {
  const configured = config().core;
  if (configured) { return configured; }
  const bundled = bundledCore();
  return bundled && fs.existsSync(bundled) ? bundled : '';
}
export function minLevel(): string { return config().minLevel; }
export function showDismissed(): boolean { return config().showDismissed; }

/** True when the extension should talk to a hosted API instead of the core. */
export function apiMode(): boolean { return !!config().apiUrl; }

/** Human label for the active backend, for the status bar / errors. */
export function backend(): string {
  const c = config();
  if (c.apiUrl) { return c.apiUrl; }
  return c.core ? 'local core' : '(unconfigured)';
}

// --------------------------------------------------------------- API client

function apiRequest(method: 'GET' | 'POST', action: string,
                    body?: unknown): Promise<any> {
  const { apiUrl, apiToken, apiProject, apiAllowInsecure } = config();
  if (!apiToken) {
    return Promise.reject(new Error(
      'Set "varalign.apiToken" (your license key) to use the VarAlign API.'));
  }
  if (!apiProject) {
    return Promise.reject(new Error(
      'Set "varalign.apiProject" to the project id on the VarAlign API.'));
  }
  // WHATWG URL API (not the deprecated url.parse) — validates the endpoint.
  let url: URL;
  try {
    url = new URL(`${apiUrl}/v1/projects/${encodeURIComponent(apiProject)}/`
      + action);
  } catch {
    return Promise.reject(new Error(`Invalid "varalign.apiUrl": ${apiUrl}`));
  }
  const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
  const mod = url.protocol === 'http:' ? http : https;
  const opts: https.RequestOptions = {
    method,
    hostname: url.hostname,
    port: url.port || (url.protocol === 'http:' ? 80 : 443),
    path: url.pathname + url.search,
    headers: {
      'Authorization': `Bearer ${apiToken}`,
      'Accept': 'application/json',
      ...(payload ? { 'Content-Type': 'application/json',
                      'Content-Length': payload.length } : {}),
    },
    // Only for a self-hosted internal API on a private CA this box doesn't trust.
    rejectUnauthorized: !apiAllowInsecure,
  };
  return new Promise((resolve, reject) => {
    const req = mod.request(opts, res => {
      const chunks: Buffer[] = [];
      res.on('data', d => chunks.push(d));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf-8');
        let json: any = {};
        try { json = text ? JSON.parse(text) : {}; } catch { /* non-JSON */ }
        const code = res.statusCode || 0;
        if (code >= 200 && code < 300) { resolve(json); return; }
        const msg = json?.error || `HTTP ${code}` + (text ? `: ${text.slice(0, 200)}` : '');
        if (code === 401) {
          reject(new Error('VarAlign API rejected the token (401) — check '
            + '"varalign.apiToken".'));
        } else if (code === 404) {
          reject(new Error(`VarAlign API: project "${apiProject}" not found (404).`));
        } else { reject(new Error(`VarAlign API ${msg}`)); }
      });
    });
    req.on('error', e => reject(new Error(
      `Cannot reach VarAlign API at ${apiUrl}: ${(e as Error).message}`)));
    if (payload) { req.write(payload); }
    req.end();
  });
}

// --------------------------------------------------------------- local core

/** Run `python varmem.py --project <root> <args...>` and resolve stdout. */
export function run(root: string, args: string[]): Promise<string> {
  const python = config().python;
  const core = corePath();
  if (!core) {
    return Promise.reject(new Error('No VarAlign engine found. Set '
      + '"varalign.corePath" to varmem.py — or set "varalign.apiUrl" to read '
      + 'from a hosted VarAlign API.'));
  }
  return new Promise((resolve, reject) => {
    execFile(python, [core, '--project', root, ...args],
      {
        maxBuffer: 128 * 1024 * 1024,
        // Windows defaults Python stdio to cp1252, which cannot encode the
        // arrows/dashes in prompts and reports — force UTF-8 end to end.
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      },
      (err, stdout, stderr) =>
        err ? reject(new Error((stderr || err.message).trim())) : resolve(stdout));
  });
}

// ------------------------------------------------------- unified operations

export async function report(root: string): Promise<ReportData> {
  if (apiMode()) { return apiRequest('GET', 'report') as Promise<ReportData>; }
  return JSON.parse(await run(root, ['report', '--json']));
}

export async function ignoreName(root: string, names: string[]): Promise<void> {
  await run(root, ['ignore-name', ...names]);
}

export async function prompt(root: string): Promise<string> {
  if (apiMode()) {
    const r = await apiRequest('POST', 'prompt', { min_level: minLevel() });
    if (r?.skipped) { return `# VarAlign\n\n${r.skipped}\n`; }
    return r?.prompt || '';
  }
  return run(root, ['prompt']);
}

export async function scan(root: string): Promise<void> {
  // A remote project is populated by capture hooks elsewhere; the closest
  // server-side action is a reconcile pass.
  if (apiMode()) { await apiRequest('POST', 'reconcile'); return; }
  await run(root, ['scan']);
}

export async function reconcile(root: string): Promise<void> {
  if (apiMode()) { await apiRequest('POST', 'reconcile'); return; }
  await run(root, ['reconcile', '--force']);
}

export async function dupNote(root: string, pairKey: string, verdict: string,
                              note?: string): Promise<void> {
  if (apiMode()) {
    throw new Error('Reviews are read-only over the VarAlign API. Dismiss or '
      + 'confirm on the machine that owns the project, then refresh.');
  }
  const args = ['dup-note', pairKey, '--verdict', verdict];
  if (note) { args.push('--note', note); }
  await run(root, args);
}
