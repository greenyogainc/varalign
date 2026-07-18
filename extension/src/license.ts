// Offline VarAlign license verification (Ed25519, Node built-in crypto — no
// dependency, no phone-home). A license is `VL1.<b64url(claims)>.<b64url(sig)>`;
// we verify the signature over the exact claims bytes with the embedded PUBLIC
// key, then apply a 14-day grace window past expiry. The private key never
// leaves the signing host. Mirrors licensing/varalign-license.mjs.
import * as crypto from 'crypto';
import * as vscode from 'vscode';

const PUBLIC_KEY_X = 'LNfjckR4obqvYBWEyqqCpgrO13tqu5v4YIIfICKDTxQ';
const GRACE_DAYS = 14;

const TIER_FEATURES: Record<string, string[]> = {
  pro: ['diagnostics', 'quick-fix', 'rename-assist'],
  team: ['diagnostics', 'quick-fix', 'rename-assist',
         'reviews-sync', 'ci-pro', 'org-report'],
  enterprise: ['diagnostics', 'quick-fix', 'rename-assist',
               'reviews-sync', 'ci-pro', 'org-report',
               'offline', 'sso', 'priority-support'],
};

export type LicenseStatus =
  | 'active' | 'grace' | 'expired'
  | 'malformed' | 'bad-signature' | 'bad-claims' | 'none';

export interface License {
  valid: boolean;
  status: LicenseStatus;
  tier?: string;
  sub?: string;
  features: string[];
  expires?: string | null;
}

function publicKey(): crypto.KeyObject {
  return crypto.createPublicKey(
    { key: { kty: 'OKP', crv: 'Ed25519', x: PUBLIC_KEY_X }, format: 'jwk' });
}

/** Verify a license token fully offline. `now` is injectable for tests. */
export function verifyLicense(token: string | undefined,
                             now = Date.now()): License {
  const t = (token || '').trim();
  if (!t) { return { valid: false, status: 'none', features: [] }; }
  const parts = t.split('.');
  if (parts.length !== 3 || parts[0] !== 'VL1') {
    return { valid: false, status: 'malformed', features: [] };
  }
  let body: Buffer, sig: Buffer;
  try {
    body = Buffer.from(parts[1], 'base64url');
    sig = Buffer.from(parts[2], 'base64url');
  } catch { return { valid: false, status: 'malformed', features: [] }; }
  let ok = false;
  try { ok = crypto.verify(null, body, publicKey(), sig); } catch { ok = false; }
  if (!ok) { return { valid: false, status: 'bad-signature', features: [] }; }
  let claims: any;
  try { claims = JSON.parse(body.toString('utf8')); }
  catch { return { valid: false, status: 'bad-claims', features: [] }; }
  const exp = (claims.exp || 0) * 1000;
  const graceEnd = exp + GRACE_DAYS * 86400_000;
  let status: LicenseStatus = 'active';
  if (exp && now > graceEnd) { status = 'expired'; }
  else if (exp && now > exp) { status = 'grace'; }
  return {
    valid: status !== 'expired',
    status,
    tier: claims.tier,
    sub: claims.sub,
    features: claims.features || TIER_FEATURES[claims.tier] || [],
    expires: exp ? new Date(exp).toISOString().slice(0, 10) : null,
  };
}

export function currentLicense(): License {
  const key = vscode.workspace.getConfiguration('varalign')
    .get<string>('licenseKey') || '';
  return verifyLicense(key);
}

/** True when a paid tier is active (or within grace). */
export function isPro(): boolean { return currentLicense().valid; }

/** Gate an individual Pro/Team/Enterprise capability. */
export function hasFeature(feature: string): boolean {
  const lic = currentLicense();
  return lic.valid && lic.features.includes(feature);
}
