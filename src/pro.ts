// Client for the license-gated Pro API (api.varalign.dev). The server verifies
// the license and computes the work; this module only makes the call. A 402
// means "no valid Pro license" — enforced server-side, so there is no local
// toggle to flip. Only the two variable descriptors are sent — never the code.
import * as http from 'http';
import * as https from 'https';
import * as vscode from 'vscode';

const DEFAULT_PRO_API = 'https://api.varalign.dev';

export interface MergePlan {
  keep: { name: string; file: string; line: number };
  drop: { name: string; file: string; line: number };
  rename: { from: string; to: string };
  canonical_value: string | null;
  value_conflict: boolean;
  note: string;
}

/** Signals a 402 from the Pro API; carries the upgrade URL. */
export class ProRequiredError extends Error {
  constructor(public readonly upgrade: string) { super('Pro license required'); }
}

function proApiBase(): string {
  return (vscode.workspace.getConfiguration('varalign').get<string>('proApiUrl')
    || DEFAULT_PRO_API).replace(/\/+$/, '');
}

export function requestMergePlan(a: unknown, b: unknown,
                                 licenseKey: string): Promise<MergePlan> {
  const url = new URL(proApiBase() + '/v1/pro/merge');
  const payload = Buffer.from(JSON.stringify({ a, b }));
  const mod = url.protocol === 'http:' ? http : https;
  const opts: https.RequestOptions = {
    method: 'POST',
    hostname: url.hostname,
    port: url.port || (url.protocol === 'http:' ? 80 : 443),
    path: url.pathname,
    headers: {
      'Authorization': `Bearer ${licenseKey}`,
      'Content-Type': 'application/json',
      'Content-Length': payload.length,
      'User-Agent': 'varalign-vscode',
    },
  };
  return new Promise((resolve, reject) => {
    const req = mod.request(opts, res => {
      const chunks: Buffer[] = [];
      res.on('data', d => chunks.push(d));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf-8');
        let j: any = {};
        try { j = text ? JSON.parse(text) : {}; } catch { /* non-JSON */ }
        const code = res.statusCode || 0;
        if (code === 200 && j.plan) { resolve(j.plan as MergePlan); }
        else if (code === 402) {
          reject(new ProRequiredError(j.upgrade || 'https://varalign.dev/pro'));
        } else { reject(new Error(j.error || `merge failed (HTTP ${code})`)); }
      });
    });
    req.on('error', e =>
      reject(new Error(`cannot reach the VarAlign Pro API: ${e.message}`)));
    req.write(payload);
    req.end();
  });
}
