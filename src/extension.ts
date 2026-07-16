import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import * as core from './core';
import * as license from './license';
import * as pro from './pro';

const ORDER: Record<string, number> = { high: 3, medium: 2, low: 1 };
const LEVEL_ICON: Record<string, string> = {
  high: 'error', medium: 'warning', low: 'info',
};

function workspaceRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

// Keep VarAlign's per-repo store out of the user's git history. Non-fatal:
// the README documents the one-line manual fallback if this can't write.
function ensureGitignore(root: string): void {
  try {
    if (!fs.existsSync(path.join(root, '.git'))) { return; }  // git repos only
    const gi = path.join(root, '.gitignore');
    let text = '';
    try { text = fs.readFileSync(gi, 'utf-8'); } catch { /* none yet */ }
    if (/^\s*\.varmem\/?\s*$/m.test(text)) { return; }        // already ignored
    const block = (text && !text.endsWith('\n') ? '\n' : '')
      + '\n# VarAlign local store (machine-local variable-tracking data)\n'
      + '.varmem/\n';
    fs.appendFileSync(gi, block);
  } catch { /* README documents the manual `.varmem/` line */ }
}

function isDismissed(d: core.Dup): boolean {
  return !!(d.review && d.review.verdict === 'not_duplicate');
}

// ------------------------------------------------------------ shared store

class Store {
  data: core.ReportData | null = null;
  error: string | null = null;
  private readonly _onChange = new vscode.EventEmitter<void>();
  readonly onChange = this._onChange.event;

  async refresh(): Promise<void> {
    const root = workspaceRoot();
    try {
      if (core.apiMode()) {
        // Hosted API: no local repo/core required.
        this.data = await core.report(root || '');
      } else if (!root) {
        throw new Error('Open a folder to use VarAlign — or set '
          + '"varalign.apiUrl" to read from a hosted API.');
      } else if (!core.corePath()) {
        throw new Error('Set "varalign.corePath" to varmem.py — or set '
          + '"varalign.apiUrl" to read from a hosted API.');
      } else {
        this.data = await core.report(root);
      }
      this.error = null;
    } catch (e: any) {
      this.error = e.message || String(e); this.data = null;
    }
    this._onChange.fire();
    updateStatusBar(this);
  }
}

// ---------------------------------------------------- duplicates tree view

type DupNode =
  | { t: 'msg'; text: string }
  | { t: 'level'; level: string }
  | { t: 'dup'; dup: core.Dup }
  | { t: 'side'; side: core.Side };

class DuplicatesProvider implements vscode.TreeDataProvider<DupNode> {
  private readonly _emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._emitter.event;
  constructor(private readonly store: Store) {
    store.onChange(() => this._emitter.fire());
  }

  private visible(): core.Dup[] {
    const d = this.store.data;
    if (!d) { return []; }
    const floor = ORDER[core.minLevel()] ?? 2;
    const show = core.showDismissed();
    return d.dups.filter(x => ORDER[x.level] >= floor
      && (show || !isDismissed(x)));
  }

  getChildren(el?: DupNode): DupNode[] {
    if (!el) {
      if (this.store.error) { return [{ t: 'msg', text: this.store.error }]; }
      const dups = this.visible();
      return ['high', 'medium', 'low']
        .filter(lv => ORDER[lv] >= (ORDER[core.minLevel()] ?? 2)
          && dups.some(d => d.level === lv))
        .map(level => ({ t: 'level', level } as DupNode));
    }
    if (el.t === 'level') {
      return this.visible().filter(d => d.level === el.level)
        .sort((a, b) => b.score - a.score)
        .map(dup => ({ t: 'dup', dup } as DupNode));
    }
    if (el.t === 'dup') {
      return [{ t: 'side', side: el.dup.a }, { t: 'side', side: el.dup.b }];
    }
    return [];
  }

  getTreeItem(el: DupNode): vscode.TreeItem {
    if (el.t === 'msg') {
      return new vscode.TreeItem(el.text, vscode.TreeItemCollapsibleState.None);
    }
    if (el.t === 'level') {
      const n = this.visible().filter(d => d.level === el.level).length;
      const it = new vscode.TreeItem(
        `${el.level.toUpperCase()} (${n})`,
        vscode.TreeItemCollapsibleState.Expanded);
      it.iconPath = new vscode.ThemeIcon(LEVEL_ICON[el.level]);
      it.contextValue = 'level';
      return it;
    }
    if (el.t === 'dup') {
      const d = el.dup;
      const it = new vscode.TreeItem(`${d.a.name} ↔ ${d.b.name}`,
        vscode.TreeItemCollapsibleState.Collapsed);
      const learned = d.review?.learned ? ' · auto-quieted' : '';
      const rev = d.review ? ` · ${d.review.verdict}${learned}` : '';
      it.description = `${d.reason} · ${d.score.toFixed(2)}${rev}`;
      it.contextValue = 'dup';
      it.tooltip = `${d.a.name} (${d.a.file}:${d.a.line})\n`
        + `${d.b.name} (${d.b.file}:${d.b.line})\n${d.reason} · ${d.score}`;
      (it as any).dup = d;
      if (isDismissed(d)) { it.iconPath = new vscode.ThemeIcon('circle-slash'); }
      return it;
    }
    // side
    const s = el.side;
    const it = new vscode.TreeItem(s.name, vscode.TreeItemCollapsibleState.None);
    it.description = `${s.scope ? s.scope + ' · ' : ''}${s.file}:${s.line}`
      + (s.status !== 'active' ? ` · ${s.status}` : '');
    it.iconPath = new vscode.ThemeIcon('symbol-variable');
    it.command = openCmd(s);
    return it;
  }
}

// ----------------------------------------------------- variables tree view

type VarNode = { t: 'file'; file: string } | { t: 'var'; side: core.Side }
  | { t: 'msg'; text: string };

class VariablesProvider implements vscode.TreeDataProvider<VarNode> {
  private readonly _emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._emitter.event;
  constructor(private readonly store: Store) {
    store.onChange(() => this._emitter.fire());
  }

  getChildren(el?: VarNode): VarNode[] {
    const d = this.store.data;
    if (!el) {
      if (!d) { return this.store.error ? [{ t: 'msg', text: this.store.error }] : []; }
      const files = Array.from(new Set(d.vars.map(v => v.file))).sort();
      return files.map(file => ({ t: 'file', file } as VarNode));
    }
    if (el.t === 'file' && d) {
      return d.vars.filter(v => v.file === el.file)
        .sort((a, b) => (a.line || 0) - (b.line || 0))
        .map(side => ({ t: 'var', side } as VarNode));
    }
    return [];
  }

  getTreeItem(el: VarNode): vscode.TreeItem {
    if (el.t === 'msg') {
      return new vscode.TreeItem(el.text, vscode.TreeItemCollapsibleState.None);
    }
    if (el.t === 'file') {
      const n = this.store.data!.vars.filter(v => v.file === el.file).length;
      const it = new vscode.TreeItem(el.file,
        vscode.TreeItemCollapsibleState.Collapsed);
      it.description = `${n}`;
      it.iconPath = vscode.ThemeIcon.File;
      return it;
    }
    const s = el.side;
    const it = new vscode.TreeItem(s.name, vscode.TreeItemCollapsibleState.None);
    it.description = `${s.scope ? s.scope + ' · ' : ''}${(s.value || '').slice(0, 60)}`;
    const lvl = this.store.data?.varLevel[s.id];
    it.iconPath = new vscode.ThemeIcon(lvl ? LEVEL_ICON[lvl]
      : (s.status !== 'active' ? 'warning' : 'symbol-variable'));
    if (s.note) { it.tooltip = `✎ ${s.note}`; }
    it.command = openCmd(s);
    return it;
  }
}

// ------------------------------------------------------- sessions tree view

interface Ev { ts?: string; action?: string; name?: string; file?: string;
  scope?: string; session_id?: string; }
type SessNode = { t: 'session'; sid: string; evs: Ev[] }
  | { t: 'event'; ev: Ev } | { t: 'msg'; text: string };

class SessionsProvider implements vscode.TreeDataProvider<SessNode> {
  private readonly _emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._emitter.event;
  constructor(store: Store) {
    store.onChange(() => this._emitter.fire());
  }

  private events(): Ev[] {
    const root = workspaceRoot();
    if (!root) { return []; }
    const p = path.join(root, '.varmem', 'events.jsonl');
    try {
      return fs.readFileSync(p, 'utf-8').split('\n')
        .filter(l => l.trim()).map(l => JSON.parse(l) as Ev);
    } catch { return []; }
  }

  getChildren(el?: SessNode): SessNode[] {
    if (!el) {
      const by = new Map<string, Ev[]>();
      for (const e of this.events()) {
        const sid = e.session_id || '?';
        (by.get(sid) || by.set(sid, []).get(sid)!).push(e);
      }
      return Array.from(by.entries())
        .sort((a, b) => (b[1].at(-1)?.ts || '').localeCompare(a[1].at(-1)?.ts || ''))
        .map(([sid, evs]) => ({ t: 'session', sid, evs } as SessNode));
    }
    if (el.t === 'session') {
      return el.evs.slice().reverse().map(ev => ({ t: 'event', ev } as SessNode));
    }
    return [];
  }

  getTreeItem(el: SessNode): vscode.TreeItem {
    if (el.t === 'msg') {
      return new vscode.TreeItem(el.text, vscode.TreeItemCollapsibleState.None);
    }
    if (el.t === 'session') {
      const it = new vscode.TreeItem(el.sid.slice(0, 16),
        vscode.TreeItemCollapsibleState.Collapsed);
      it.description = `${el.evs.length} events`;
      it.iconPath = new vscode.ThemeIcon('history');
      return it;
    }
    const e = el.ev;
    const it = new vscode.TreeItem(`${e.action}${e.name ? ': ' + e.name : ''}`,
      vscode.TreeItemCollapsibleState.None);
    it.description = `${e.file || ''}${(e.ts || '').slice(0, 19)}`;
    it.iconPath = new vscode.ThemeIcon('circle-small-filled');
    if (e.file) { it.command = openCmd({ file: e.file, line: 1 } as core.Side); }
    return it;
  }
}

// ------------------------------------------------------------------ helpers

function fileUri(rel: string): vscode.Uri {
  const root = workspaceRoot() || '';
  return vscode.Uri.file(path.join(root, rel));
}

function openCmd(s: { file: string; line: number }): vscode.Command {
  return { command: 'varalign.openSide', title: 'Open', arguments: [s] };
}

let statusBar: vscode.StatusBarItem;
function updateStatusBar(store: Store) {
  const d = store.data;
  const hi = d?.unreviewedLevels?.high ?? d?.levelCounts?.high ?? 0;
  const med = d?.unreviewedLevels?.medium ?? d?.levelCounts?.medium ?? 0;
  const via = core.apiMode() ? ` · via ${core.backend()}` : '';
  const lic = license.currentLicense();
  const tier = lic.valid ? (lic.tier || 'pro').toUpperCase() : 'Free';
  statusBar.text = d ? `$(shield) VarAlign: ${hi} high` : '$(shield) VarAlign';
  statusBar.tooltip = d
    ? `VarAlign ${tier} · ${hi} high · ${med} medium duplicate suspects `
      + `— click to open${via}`
    : (store.error || `VarAlign ${tier}`);
  statusBar.show();
}

async function reload(store: Store, action?: (root: string) => Promise<void>,
                      busy?: string) {
  const root = workspaceRoot();
  if (!root) { vscode.window.showWarningMessage('Open a folder first.'); return; }
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Window, title: busy || 'VarAlign' },
    async () => {
      try {
        if (action) { await action(root); }
        await store.refresh();
      } catch (e: any) {
        vscode.window.showErrorMessage(`VarAlign: ${e.message || e}`);
      }
    });
}

// -------------------------------------------------------------- fix with AI

// Supported assistants, in tie-break priority. `open` commands are tried in
// order until one succeeds (they focus/open the assistant's chat). We can't
// inject text into another extension's webview, so the prompt goes to the
// clipboard and the user pastes — robust across their updates.
interface AiTool { id: string; label: string; open: string[]; }
const AI_TOOLS: AiTool[] = [
  { id: 'anthropic.claude-code', label: 'Claude Code',
    open: ['claude-vscode.focus', 'claude-vscode.newConversation',
           'claude-vscode.sidebar.open'] },
  { id: 'kilocode.kilo-code', label: 'Kilo Code',
    open: ['kilo-code.new.focusChatInput', 'kilo-code.new.plusButtonClicked'] },
];

function pickAiTool(): AiTool | undefined {
  const pref = vscode.workspace.getConfiguration('varalign')
    .get<string>('aiTool') || 'auto';
  if (pref === 'claude') { return AI_TOOLS[0]; }
  if (pref === 'kilo') { return AI_TOOLS[1]; }
  const installed = AI_TOOLS.filter(t => vscode.extensions.getExtension(t.id));
  // prefer an already-active assistant (a chat you have loaded); else first
  // installed, in priority order
  return installed.find(t => vscode.extensions.getExtension(t.id)?.isActive)
    || installed[0];
}

function buildDupFixPrompt(d: core.Dup): string {
  const a = d.a, b = d.b;
  const line = (s: core.Side) => `\`${s.name}\` — ${s.file}:${s.line}`
    + (s.value ? `  =  ${s.value}` : '');
  return [
    'VarAlign flagged two variable assignments that look like the same concept'
      + ` (${d.reason}, ${d.level} confidence):`,
    '',
    `1. ${line(a)}`,
    `2. ${line(b)}`,
    '',
    'If they are the same concept, consolidate them into one canonical '
      + 'definition and update every reference. If they are intentionally '
      + 'distinct, rename one so the names are no longer ambiguous. Then run '
      + 'the project tests to confirm nothing broke.',
  ].join('\n');
}

async function fixWithAI(item: any): Promise<void> {
  const d: core.Dup | undefined = item?.dup;
  let prompt: string;
  if (d) {
    prompt = buildDupFixPrompt(d);
  } else {
    const root = workspaceRoot();
    if (!root && !core.apiMode()) {
      vscode.window.showWarningMessage('Open a folder first.'); return;
    }
    try { prompt = await core.prompt(root || ''); }
    catch (e: any) {
      vscode.window.showErrorMessage(`VarAlign: ${e.message || e}`); return;
    }
  }
  await vscode.env.clipboard.writeText(prompt);
  const tool = pickAiTool();
  if (!tool) {
    vscode.window.showInformationMessage('VarAlign: fix prompt copied to '
      + 'clipboard — open your AI assistant and paste it (Ctrl+V).');
    return;
  }
  let opened = false;
  for (const cmd of tool.open) {
    try { await vscode.commands.executeCommand(cmd); opened = true; break; }
    catch { /* try the next open command */ }
  }
  vscode.window.showInformationMessage(`VarAlign: fix prompt copied — `
    + `${opened ? tool.label + ' opened' : 'open ' + tool.label}, paste it `
    + '(Ctrl+V) and send.');
}

// --------------------------------------------------- merge variables (Pro)

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Server-enforced Pro feature: ask the license-gated API for a merge plan
// (only the two descriptors leave the machine), then apply it LOCALLY. A 402
// means no valid license — there is no client-side toggle to flip.
async function mergeVariables(item: any, store: Store): Promise<void> {
  const d: core.Dup | undefined = item?.dup;
  if (!d) { return; }
  // Offline Pro gate — verified locally (Ed25519 signature + expiry via the
  // embedded public key). No server, works airgapped. Can't be forged; a
  // source-patched bypass is a Business Source License violation.
  if (!license.hasFeature('rename-assist')) {
    const pick = await vscode.window.showWarningMessage(
      'Merge Variables is a VarAlign Pro feature.', 'Enter License', 'Get Pro');
    if (pick === 'Enter License') {
      vscode.commands.executeCommand('varalign.enterLicense');
    } else if (pick === 'Get Pro') {
      vscode.env.openExternal(vscode.Uri.parse('https://varalign.dev/pro'));
    }
    return;
  }
  const plan = pro.mergePlan(d.a, d.b);   // computed locally — no network
  const go = await vscode.window.showInformationMessage(
    plan.note, { modal: true }, 'Merge');
  if (go !== 'Merge') { return; }
  await applyMergePlan(plan, store);
}

async function applyMergePlan(plan: pro.MergePlan, store: Store): Promise<void> {
  const root = workspaceRoot();
  if (!root) { return; }
  const uri = vscode.Uri.file(path.join(root, plan.drop.file));
  try {
    let doc = await vscode.workspace.openTextDocument(uri);
    const lineNo = Math.max(0, (plan.drop.line || 1) - 1);
    const col = doc.lineAt(lineNo).text.indexOf(plan.drop.name);
    if (col < 0) {
      throw new Error(`could not find ${plan.drop.name} at `
        + `${plan.drop.file}:${plan.drop.line}`);
    }
    // rename every reference drop -> keep via the language's rename provider
    const edit = await vscode.commands.executeCommand<vscode.WorkspaceEdit>(
      'vscode.executeDocumentRenameProvider', uri,
      new vscode.Position(lineNo, col), plan.rename.to);
    let applied = false;
    if (edit && edit.size > 0) { applied = await vscode.workspace.applyEdit(edit); }
    if (!applied) {
      // fallback: whole-word rename within the defining file only
      const we = new vscode.WorkspaceEdit();
      const re = new RegExp(`\\b${escapeRe(plan.drop.name)}\\b`, 'g');
      for (let i = 0; i < doc.lineCount; i++) {
        const t = doc.lineAt(i).text;
        let m: RegExpExecArray | null;
        while ((m = re.exec(t))) {
          we.replace(uri, new vscode.Range(i, m.index,
            i, m.index + plan.drop.name.length), plan.rename.to);
        }
      }
      await vscode.workspace.applyEdit(we);
      vscode.window.showWarningMessage(`VarAlign: renamed within ${plan.drop.file}`
        + ' only (no rename provider) — check references in other files.');
    }
    // remove the now-duplicate definition line
    doc = await vscode.workspace.openTextDocument(uri);
    const del = new vscode.WorkspaceEdit();
    del.delete(uri, doc.lineAt(lineNo).rangeIncludingLineBreak);
    await vscode.workspace.applyEdit(del);
    await doc.save();
    vscode.window.showInformationMessage(`VarAlign: merged into ${plan.keep.name}.`);
    store.refresh();
  } catch (e: any) {
    vscode.window.showErrorMessage(`VarAlign merge failed: ${e.message || e}`);
  }
}

// ----------------------------------------------------------------- activate

export function activate(ctx: vscode.ExtensionContext) {
  core.setExtensionPath(ctx.extensionPath);
  const store = new Store();
  statusBar = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left, 100);
  statusBar.command = 'varalign.duplicates.focus';
  ctx.subscriptions.push(statusBar);

  vscode.window.registerTreeDataProvider('varalign.duplicates',
    new DuplicatesProvider(store));
  vscode.window.registerTreeDataProvider('varalign.variables',
    new VariablesProvider(store));
  vscode.window.registerTreeDataProvider('varalign.sessions',
    new SessionsProvider(store));

  const reg = (id: string, fn: (...a: any[]) => any) =>
    ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));

  reg('varalign.refresh', () => reload(store, undefined, 'Refreshing…'));
  reg('varalign.rescan', () => reload(store, core.scan, 'Rescanning…'));
  reg('varalign.reconcile', () => reload(store, core.reconcile, 'Reconciling…'));

  reg('varalign.openSide', async (s: { file: string; line: number }) => {
    try {
      const doc = await vscode.workspace.openTextDocument(fileUri(s.file));
      const ed = await vscode.window.showTextDocument(doc);
      const ln = Math.max(0, (s.line || 1) - 1);
      const range = new vscode.Range(ln, 0, ln, 0);
      ed.revealRange(range, vscode.TextEditorRevealType.InCenter);
      ed.selection = new vscode.Selection(range.start, range.start);
    } catch (e: any) {
      vscode.window.showWarningMessage(`Cannot open ${s.file}: ${e.message}`);
    }
  });

  const verdictCmd = (id: string, verdict: string, ask: boolean) =>
    reg(id, async (item: any) => {
      const d: core.Dup | undefined = item?.dup;
      if (!d) { return; }
      let note: string | undefined;
      if (ask) {
        note = await vscode.window.showInputBox(
          { prompt: `Note for ${d.a.name} ↔ ${d.b.name}` });
      }
      await reload(store, r => core.dupNote(r, d.pair_key, verdict, note),
        'Saving verdict…');
    });
  verdictCmd('varalign.dismissDup', 'not_duplicate', false);
  verdictCmd('varalign.confirmDup', 'duplicate', false);
  verdictCmd('varalign.noteDup', 'not_duplicate', true);

  reg('varalign.toggleDismissed', async () => {
    const c = vscode.workspace.getConfiguration('varalign');
    await c.update('showDismissed', !c.get('showDismissed'), true);
    await store.refresh();
  });

  reg('varalign.generatePrompt', () => reload(store, async r => {
    const text = await core.prompt(r);
    const doc = await vscode.workspace.openTextDocument(
      { content: text, language: 'markdown' });
    await vscode.window.showTextDocument(doc);
  }, 'Generating prompt…'));

  reg('varalign.fixWithAI', (item: any) => fixWithAI(item));
  reg('varalign.mergeVariables', (item: any) => mergeVariables(item, store));

  reg('varalign.enterLicense', async () => {
    const key = await vscode.window.showInputBox({
      prompt: 'Paste your VarAlign license key (VL1.…) — empty to clear',
      placeHolder: 'VL1.…',
    });
    if (key === undefined) { return; }
    const cfg = vscode.workspace.getConfiguration('varalign');
    const trimmed = key.trim();
    if (!trimmed) {
      await cfg.update('licenseKey', '', true);
      vscode.window.showInformationMessage('VarAlign: license cleared — Free tier.');
      updateStatusBar(store);
      return;
    }
    const lic = license.verifyLicense(trimmed);
    if (!lic.valid) {
      vscode.window.showErrorMessage(`VarAlign: license not valid (${lic.status}).`);
      return;
    }
    await cfg.update('licenseKey', trimmed, true);
    const grace = lic.status === 'grace' ? ` — in grace, expired ${lic.expires}` : '';
    vscode.window.showInformationMessage(
      `VarAlign ${(lic.tier || '').toUpperCase()} active${grace}. Thank you!`);
    updateStatusBar(store);
  });

  reg('varalign.licenseStatus', () => {
    const lic = license.currentLicense();
    if (lic.status === 'none') {
      vscode.window.showInformationMessage(
        'VarAlign: Free tier. Run "VarAlign: Enter License" to activate Pro.');
    } else if (!lic.valid) {
      vscode.window.showWarningMessage(`VarAlign license: ${lic.status}.`);
    } else {
      vscode.window.showInformationMessage(
        `VarAlign ${(lic.tier || '').toUpperCase()} · ${lic.sub || ''} · `
        + `expires ${lic.expires}${lic.status === 'grace' ? ' (grace)' : ''} · `
        + `features: ${lic.features.join(', ')}`);
    }
  });

  reg('varalign.setCorePath', async () => {
    const picked = await vscode.window.showOpenDialog(
      { canSelectMany: false, filters: { 'varmem.py': ['py'] },
        openLabel: 'Use this varmem.py' });
    if (picked?.[0]) {
      await vscode.workspace.getConfiguration('varalign')
        .update('corePath', picked[0].fsPath, true);
      await store.refresh();
    }
  });

  // auto-refresh when the registry changes (a hook or CLI wrote to it)
  const root = workspaceRoot();
  if (root) {
    ensureGitignore(root);
    const watcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(root, '.varmem/**'));
    const bump = () => store.refresh();
    watcher.onDidChange(bump); watcher.onDidCreate(bump);
    watcher.onDidDelete(bump);
    ctx.subscriptions.push(watcher);
  }
  vscode.workspace.onDidChangeConfiguration(e => {
    if (e.affectsConfiguration('varalign')) { store.refresh(); }
  }, null, ctx.subscriptions);

  store.refresh();
}

export function deactivate() { /* nothing to clean up */ }
