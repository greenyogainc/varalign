"""Control-tower frontend for the live server (varmem/live.py).

One self-contained page: no external fonts, icons, CDNs, frameworks, or
build step — semantic HTML, CSS custom properties, restrained inline SVG,
plain JavaScript. Every repository-derived value is escaped before insertion.
All data arrives from the repo-scoped JSON APIs; the page never mixes two
repositories' findings into one list without labelling each row's repository.
"""

from ._assets import FAVICON_DATA_URI, LOGO_DATA_URI

CSS = """
:root{
  --ink:#101828; --sub:#667085; --line:#e4e7ec; --panel:#fff; --soft:#f8fafc;
  --bg:#f5f7fb; --violet:#6558e8; --violet-2:#7c6ff1; --violet-soft:#efedff;
  --violet-ink:#5146c9; --red:#d92d20; --red-bar:#f04438; --amber:#dc6803;
  --amber-bar:#f79009; --green:#079455;
  --bar-bg:#121726; --bar-line:#2a3140; --bar-btn:#1a2130;
  --bar-btn-line:#394356;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--ink);
  font:14px/1.45 system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
button{font:inherit;cursor:pointer}
input,select,textarea{font:inherit;color:var(--ink)}
:focus-visible{outline:2px solid var(--violet);outline-offset:2px}
.skip{position:absolute;left:-9999px;top:0;background:var(--violet);
  color:#fff;padding:8px 14px;border-radius:0 0 8px 0;z-index:60}
.skip:focus{left:0}
code{background:#f2f4f7;padding:.08rem .35rem;border-radius:4px;
  font-size:.92em;word-break:break-all}

/* ------------------------------------------------------------ product bar */
.appbar{position:sticky;top:0;z-index:30;height:56px;display:flex;
  align-items:center;gap:14px;padding:0 16px;background:var(--bar-bg);
  color:#fff;border-bottom:1px solid var(--bar-line)}
.menu-btn{display:none;background:var(--bar-btn);color:#d6dce6;
  border:1px solid var(--bar-btn-line);border-radius:8px;padding:6px 9px}
.logo{font-size:17px;font-weight:800;letter-spacing:-.02em;display:flex;
  align-items:center;gap:8px;white-space:nowrap}
.logo .mark{width:27px;height:27px;border-radius:8px;object-fit:cover;
  display:block}
.crumb{color:#aab3c2;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;min-width:0}
.crumb b{color:#fff}
.appbar .right{margin-left:auto;display:flex;gap:8px;align-items:center}
.sync{color:#aab3c2;font-size:12px;white-space:nowrap}
.topbtn{padding:7px 11px;border:1px solid var(--bar-btn-line);
  border-radius:8px;color:#d6dce6;background:var(--bar-btn);
  white-space:nowrap}
.topbtn:hover{background:#232c3f}
.topbtn.primary{background:var(--violet);border-color:var(--violet);
  color:#fff;font-weight:700}
.topbtn.primary:hover{background:#5a4ee0}
.topbtn[disabled]{opacity:.55;cursor:progress}

/* ----------------------------------------------------------------- layout */
.shell{display:grid;grid-template-columns:250px minmax(0,1fr);
  min-height:calc(100vh - 56px)}
.side{background:var(--panel);border-right:1px solid var(--line);
  padding:14px 10px 24px;overflow-y:auto}
.side-label{font-size:10px;text-transform:uppercase;letter-spacing:.09em;
  color:#98a2b3;font-weight:800;margin:12px 8px 6px}
.grouprow{display:flex;align-items:center;gap:6px}
.group-btn{flex:1;display:flex;align-items:center;gap:8px;width:100%;
  text-align:left;padding:9px 10px;border:0;background:none;
  border-radius:9px;font-weight:700;color:var(--ink)}
.group-btn:hover{background:var(--soft)}
.group-btn[aria-current="true"]{background:var(--violet-soft);
  color:var(--violet-ink)}
.gear{border:0;background:none;color:#98a2b3;border-radius:7px;
  padding:5px 7px;font-size:13px}
.gear:hover{background:var(--soft);color:var(--ink)}
.repo-btn{display:flex;align-items:center;gap:8px;width:100%;
  text-align:left;padding:7px 10px 7px 24px;border:0;background:none;
  color:#475467;border-radius:8px}
.repo-btn:hover{background:var(--soft)}
.repo-btn[aria-current="true"]{background:#f5f3ff;color:#4e43c3;
  font-weight:700}
.dot{width:7px;height:7px;border-radius:50%;background:#12b76a;flex:none}
.dot.warn{background:var(--amber-bar)}
.dot.idle{background:#c7ced9}
.count{margin-left:auto;background:#f2f4f7;border-radius:999px;
  padding:2px 7px;color:var(--sub);font-size:10px;font-weight:700}
.count.hot{background:#fee4e2;color:#b42318}
.side .newgroup{margin:8px 8px 0;width:calc(100% - 16px)}
.backdrop{display:none}

/* ------------------------------------------------------------------- main */
.main{padding:20px 24px 40px;min-width:0}
.heading{display:flex;align-items:flex-start;justify-content:space-between;
  gap:16px;flex-wrap:wrap}
.heading h2{font-size:22px;margin:0 0 4px;letter-spacing:-.02em}
.heading .sub{margin:0;color:var(--sub);word-break:break-all}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.button{border:1px solid #d0d5dd;border-radius:9px;background:#fff;
  padding:8px 12px;font-weight:700;color:#344054;white-space:nowrap}
.button:hover{background:var(--soft)}
.button.primary{border-color:var(--violet);background:var(--violet);
  color:#fff}
.button.primary:hover{background:#5a4ee0}
.button.danger{border-color:#fda29b;color:#b42318}
.button.small{padding:5px 9px;font-size:12px}
.button[disabled]{opacity:.55;cursor:progress}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;
  margin:18px 0}
.metric{background:var(--panel);border:1px solid var(--line);
  border-radius:13px;padding:13px 15px}
.metric .lab{color:var(--sub);font-size:11px;font-weight:650}
.metric b{display:block;font-size:25px;line-height:1.15;margin:5px 0 2px;
  letter-spacing:-.03em}
.metric .delta{font-size:10px;color:#98a2b3}
.metric b.bad{color:var(--red)} .metric b.med{color:var(--amber)}
.grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(280px,.75fr);
  gap:14px;align-items:start}
.panel{background:var(--panel);border:1px solid var(--line);
  border-radius:14px;overflow:hidden}
.ph{display:flex;align-items:center;gap:8px;padding:12px 15px;
  border-bottom:1px solid var(--line)}
.ph strong{font-size:14px}
.ph .phsub{margin-left:auto;color:var(--sub);font-size:11px}

/* --------------------------------------------------------------- filters */
.filters{display:flex;gap:8px;padding:10px 12px;
  border-bottom:1px solid #eef0f3;flex-wrap:wrap;align-items:center}
.filters input[type=search]{flex:1;min-width:140px;padding:7px 10px;
  border:1px solid #d0d5dd;border-radius:8px;background:#fff}
.filters select{padding:7px 8px;border:1px solid #d0d5dd;border-radius:8px;
  background:#fff}
.filters label{display:flex;align-items:center;gap:5px;color:var(--sub);
  font-size:12px}

/* ----------------------------------------------------------------- issues */
.issue{display:grid;grid-template-columns:8px minmax(0,1fr) auto;gap:10px;
  padding:0;border-bottom:1px solid #eef0f3;align-items:stretch}
.issue:last-child{border-bottom:0}
.issue .bar{border-radius:5px;margin:12px 0 12px 10px;background:#c7ced9}
.issue.high .bar{background:var(--red-bar)}
.issue.medium .bar{background:var(--amber-bar)}
.issue.low .bar{background:#facc15}
.issue-btn{display:block;width:100%;text-align:left;background:none;
  border:0;padding:12px 4px;min-width:0}
.issue-btn:hover{background:var(--soft)}
.issue .title{font-weight:750;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.issue .meta{font-size:11px;color:#98a2b3;margin-top:3px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sev{display:inline-block;padding:2px 6px;border-radius:5px;font-size:9px;
  font-weight:800;letter-spacing:.04em;vertical-align:1px}
.sev.high{background:#fff1f0;color:#b42318}
.sev.medium{background:#fff7ed;color:#b54708}
.sev.low{background:#fefce8;color:#a16207}
.sev.state{background:#eef2ff;color:#3730a3}
.sev.done{background:#ecfdf3;color:#027a48}
.repotag{display:inline-block;padding:2px 6px;border-radius:5px;
  background:#f2f4f7;color:#475467;font-size:9px;font-weight:750}
.issue .open{align-self:center;margin-right:12px;font-size:11px;
  border:1px solid #d0d5dd;border-radius:7px;padding:6px 9px;
  color:#344054;font-weight:700;background:#fff;white-space:nowrap}
.issue .open:hover{background:var(--soft)}
.detail{grid-column:1 / -1;padding:0 14px 14px;background:#fbfbfd}
.sides{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}
.sideCard{background:var(--panel);border:1px solid var(--line);
  border-radius:9px;padding:10px 12px;overflow-x:auto;min-width:0}
.sideCard .nm{font-weight:750}
.sideCard .smeta{color:#98a2b3;font-size:11px;margin-top:4px}
.sideCard .note{color:#9a6700;font-size:12px;margin-top:4px}
.reviewform{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;
  align-items:center}
.reviewform select,.reviewform input{padding:7px 9px;
  border:1px solid #d0d5dd;border-radius:8px;background:#fff}
.reviewform input{flex:1;min-width:160px}
.verdict-note{margin-top:8px;font-size:12px;color:#475467;background:#f0fdf4;
  border:1px solid #bbf7d0;border-radius:8px;padding:7px 10px}

/* -------------------------------------------------------------- variables */
.vars-table{width:100%;border-collapse:collapse}
.vars-table th,.vars-table td{text-align:left;padding:7px 10px;
  border-bottom:1px solid #eef0f3;vertical-align:top;font-size:12px}
.vars-table th{color:#9fa2a8;font-weight:700;position:sticky;top:56px;
  background:var(--panel)}
.vars-table td.mono{font-family:ui-monospace,Consolas,monospace;
  font-size:11px}
.table-wrap{overflow-x:auto}

/* --------------------------------------------------------------- prompts */
.prompt-repo{display:flex;align-items:center;gap:9px;padding:10px;
  border:1px solid #e1defd;background:#f8f7ff;border-radius:10px;
  margin:0 0 8px}
.repoicon{display:grid;place-items:center;width:30px;height:30px;flex:none;
  border-radius:8px;background:#e5e1ff;color:var(--violet-ink);
  font-weight:850}
.prompt-repo .info{min-width:0}
.prompt-repo b{display:block;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.prompt-repo small{color:#7a8290}
.prompt-repo .button{margin-left:auto}
.guard{margin-top:10px;padding:10px;border-radius:9px;background:var(--soft);
  color:var(--sub);font-size:11px;border:1px dashed #d0d5dd}
.guard b{color:#344054}
.activity .item{padding:9px 15px;border-bottom:1px solid #eef0f3}
.activity .item:last-child{border-bottom:0}
.activity b{font-size:12px}
.activity small{display:block;color:#98a2b3;margin-top:2px}

/* ------------------------------------------------------- pager + states */
.pager{display:flex;align-items:center;gap:10px;padding:10px 14px;
  color:var(--sub);font-size:12px}
.pager .button{padding:5px 10px}
.pager .spacer{flex:1}
.empty{padding:26px 16px;text-align:center;color:var(--sub)}
.empty b{display:block;color:var(--ink);margin-bottom:4px}
.errorbox{margin:14px 0;padding:12px 14px;border:1px solid #fda29b;
  background:#fef3f2;color:#b42318;border-radius:10px;font-size:13px}
.loading{padding:22px 16px;color:var(--sub)}
.loading::after{content:"…";animation:pulse 1s infinite}
@keyframes pulse{50%{opacity:.35}}

/* ----------------------------------------------------------------- modal */
.modal-backdrop{position:fixed;inset:0;background:rgba(16,24,40,.55);
  display:grid;place-items:center;z-index:50;padding:18px}
/* the class sets display:grid, which would override the [hidden] attribute
   and leave this full-screen overlay swallowing every click — keep it hidden */
.modal-backdrop[hidden]{display:none}
.modal{background:var(--panel);border-radius:14px;width:min(860px,100%);
  max-height:min(86vh,900px);display:flex;flex-direction:column;
  box-shadow:0 24px 70px rgba(16,24,40,.35)}
.modal header{display:flex;align-items:center;padding:14px 18px;
  border-bottom:1px solid var(--line)}
.modal header h3{margin:0;font-size:16px}
.modal header .x{margin-left:auto;border:0;background:none;font-size:16px;
  color:var(--sub);border-radius:7px;padding:4px 9px}
.modal header .x:hover{background:var(--soft);color:var(--ink)}
.modal .body{padding:16px 18px;overflow-y:auto}
.modal .foot{padding:12px 18px;border-top:1px solid var(--line);
  display:flex;gap:8px;justify-content:flex-end}
.field{margin-bottom:12px}
.field label{display:block;font-size:12px;font-weight:700;color:#344054;
  margin-bottom:4px}
.field input[type=text]{width:100%;padding:8px 10px;
  border:1px solid #d0d5dd;border-radius:8px}
.checklist{border:1px solid var(--line);border-radius:9px;max-height:220px;
  overflow-y:auto}
.checklist label{display:flex;gap:8px;align-items:center;padding:8px 10px;
  border-bottom:1px solid #eef0f3;font-size:13px;font-weight:400}
.checklist label:last-child{border-bottom:0}
.prompt-card{border:1px solid var(--line);border-radius:11px;
  margin-bottom:12px;overflow:hidden}
.prompt-card .pc-head{display:flex;align-items:center;gap:9px;
  padding:10px 12px;background:var(--soft);border-bottom:1px solid var(--line);
  flex-wrap:wrap}
.prompt-card textarea{width:100%;border:0;padding:10px 12px;min-height:180px;
  resize:vertical;font:11px/1.5 ui-monospace,Consolas,monospace;
  background:#fbfbfd;display:block}
.suggestion{border:1px solid var(--line);border-radius:11px;padding:12px;
  margin-bottom:10px}
.suggestion .tags{margin:6px 0}
.toasts{position:fixed;right:16px;bottom:16px;z-index:70;display:flex;
  flex-direction:column;gap:8px;max-width:340px}
.toast{background:#101828;color:#fff;border-radius:10px;padding:10px 14px;
  font-size:13px;box-shadow:0 8px 24px rgba(16,24,40,.35)}
.toast.err{background:#7a271a}

/* ------------------------------------------------------------ responsive */
@media (max-width:1080px){
  .metrics{grid-template-columns:repeat(2,1fr)}
  .grid{grid-template-columns:1fr}
}
@media (max-width:900px){
  .menu-btn{display:inline-block}
  .crumb{display:none}
  .sync{display:none}
  .shell{grid-template-columns:1fr}
  .side{position:fixed;top:56px;left:0;bottom:0;width:270px;z-index:40;
    transform:translateX(-102%);transition:transform .18s ease;
    box-shadow:0 10px 40px rgba(16,24,40,.25)}
  .side.open{transform:translateX(0)}
  .backdrop{display:none;position:fixed;inset:56px 0 0 0;
    background:rgba(16,24,40,.4);z-index:35}
  .backdrop.open{display:block}
  .main{padding:14px 12px 40px}
  .sides{grid-template-columns:1fr}
  .issue{grid-template-columns:8px minmax(0,1fr)}
  .issue .open{display:none}
}
@media (max-width:640px){
  body{overflow-x:hidden}
  /* the primary action stays available in the workspace heading */
  #btn-prompts{display:none}
  .metrics{grid-template-columns:1fr 1fr;gap:8px}
  .metric b{font-size:20px}
  .appbar{gap:8px;padding:0 10px}
  .topbtn{padding:6px 8px;font-size:12px}
}
"""

_JS = r"""
'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {
  tree: null,          // {groups, repos, standalone}
  counts: {},          // repo id -> counts (lazy)
  sel: {group: null, repo: null, view: 'issues'},
  issues: {page: 1, q: '', level: '', kind: '', review: ''},
  vars: {page: 1, q: '', status: ''},
  openIssue: null,
};
let seq = 0;           // render sequence guard for async races

async function api(path, body){
  const opts = body === undefined ? {} :
    {method: 'POST', body: JSON.stringify(body)};
  let resp, data;
  try { resp = await fetch(path, opts); } catch (e) {
    throw new Error('server unreachable: ' + e.message);
  }
  try { data = await resp.json(); } catch (e) { data = {}; }
  if (!resp.ok) throw new Error(data.error || ('HTTP ' + resp.status));
  return data;
}

function toast(msg, isErr){
  const t = document.createElement('div');
  t.className = 'toast' + (isErr ? ' err' : '');
  t.textContent = msg;
  $('toasts').appendChild(t);
  setTimeout(() => t.remove(), isErr ? 8000 : 4000);
}

function setSync(){
  $('sync').textContent = 'Synced ' + new Date().toLocaleTimeString();
}

/* ------------------------------------------------------------- selection */
function groupMeta(){
  if (state.sel.group === 'standalone')
    return {id: 'standalone', name: 'Standalone repos',
            repo_ids: state.tree ? state.tree.standalone : []};
  return (state.tree?.groups || []).find(g => g.id === state.sel.group)
    || null;
}
function memberRepos(){
  const g = groupMeta();
  if (!g || !state.tree) return [];
  const by = Object.fromEntries(state.tree.repos.map(r => [r.id, r]));
  return g.repo_ids.map(id => by[id]).filter(Boolean);
}
function repoMeta(rid){
  return (state.tree?.repos || []).find(r => r.id === rid) || null;
}
function selectGroup(gid){
  state.sel = {group: gid, repo: null, view: 'issues'};
  state.openIssue = null;
  render();
}
function selectRepo(rid){
  state.sel.repo = rid;
  state.sel.view = 'issues';
  state.issues.page = 1; state.vars.page = 1;
  state.openIssue = null;
  render();
}

/* ----------------------------------------------------------------- boot */
async function loadTree(keepSel){
  try {
    state.tree = await api('/api/tree');
  } catch (e) {
    $('main').innerHTML = '<div class="errorbox">Could not load '
      + 'repositories: ' + esc(e.message) + '</div>';
    return;
  }
  const groups = state.tree.groups;
  if (!keepSel || !groupMeta()) {
    state.sel.group = groups.length ? groups[0].id : 'standalone';
    state.sel.repo = null;
  }
  render();
  setSync();
  refreshCounts();
}

async function refreshCounts(){
  for (const r of (state.tree?.repos || [])) {
    try {
      const d = await api('/api/repo?id=' + encodeURIComponent(r.id));
      state.counts[r.id] = d.counts;
      renderSidebar();
      if (!state.sel.repo) renderMetrics();
    } catch (e) { /* per-repo failure shows as missing count */ }
  }
}

/* -------------------------------------------------------------- sidebar */
function needCount(rid){
  const c = state.counts[rid];
  return c ? c.needs_attention : null;
}
function renderSidebar(){
  const host = $('nav-groups');
  host.innerHTML = '';
  const t = state.tree;
  if (!t) return;
  const addGroup = (g, manageable) => {
    const row = document.createElement('div');
    row.className = 'grouprow';
    const total = g.repo_ids.map(needCount)
      .reduce((a, b) => (b === null ? a : (a ?? 0) + b), null);
    const on = state.sel.group === g.id;
    const btn = document.createElement('button');
    btn.className = 'group-btn';
    btn.setAttribute('aria-current', on ? 'true' : 'false');
    btn.innerHTML = esc(g.name) +
      `<span class="count${total ? ' hot' : ''}">` +
      (total === null ? '…' : total) + '</span>';
    btn.onclick = () => selectGroup(g.id);
    row.appendChild(btn);
    if (manageable) {
      const gear = document.createElement('button');
      gear.className = 'gear';
      gear.setAttribute('aria-label', 'Manage group ' + g.name);
      gear.textContent = '⚙';
      gear.onclick = () => groupModal(g.id);
      row.appendChild(gear);
    }
    host.appendChild(row);
    if (on) {
      const by = Object.fromEntries(t.repos.map(r => [r.id, r]));
      for (const rid of g.repo_ids) {
        const r = by[rid];
        if (!r) continue;
        const n = needCount(rid);
        const rb = document.createElement('button');
        rb.className = 'repo-btn';
        rb.setAttribute('aria-current',
          state.sel.repo === rid ? 'true' : 'false');
        const dotCls = n === null ? 'idle' : (n > 0 ? 'warn' : '');
        rb.innerHTML = `<span class="dot ${dotCls}" aria-hidden="true">` +
          `</span>${esc(r.name)}<span class="count${n ? ' hot' : ''}">` +
          (n === null ? '…' : n) + '</span>';
        rb.title = r.path;
        rb.onclick = () => selectRepo(rid);
        host.appendChild(rb);
      }
    }
  };
  for (const g of t.groups) addGroup(g, true);
  addGroup({id: 'standalone', name: 'Standalone repos',
            repo_ids: t.standalone}, false);
}

/* ----------------------------------------------------------- main views */
function render(){
  renderSidebar();
  renderCrumb();
  const main = $('main');
  main.innerHTML = '';
  if (!state.tree) { main.innerHTML = '<div class="loading">Loading</div>'; return; }
  if (!state.tree.repos.length) {
    main.innerHTML = '<div class="empty"><b>No repositories registered</b>' +
      'Register one with <code>python varmem.py repos add &lt;path&gt;' +
      '</code> and refresh.</div>';
    return;
  }
  if (state.sel.repo) renderRepoView();
  else renderGroupView();
}

function renderCrumb(){
  const g = groupMeta();
  const r = state.sel.repo ? repoMeta(state.sel.repo) : null;
  $('crumb').innerHTML = 'Groups&nbsp;/&nbsp;<b>' +
    esc(g ? g.name : '—') + '</b>' +
    (r ? '&nbsp;/&nbsp;<b>' + esc(r.name) + '</b>' : '');
}

function metricCards(c){
  return `
  <div class="metrics">
    <div class="metric"><span class="lab">Needs attention</span>
      <b>${c.needs_attention}</b>
      <span class="delta">unreviewed high/medium + drift + missing</span></div>
    <div class="metric"><span class="lab">High confidence</span>
      <b class="bad">${c.high}</b>
      <span class="delta">review these first</span></div>
    <div class="metric"><span class="lab">Drifted / missing</span>
      <b class="med">${c.drifted + c.missing}</b>
      <span class="delta">live state differs</span></div>
    <div class="metric"><span class="lab">Reviewed</span>
      <b>${c.reviewed}</b>
      <span class="delta">pair verdicts recorded</span></div>
  </div>`;
}
function sumCounts(list){
  const z = {tracked:0, needs_attention:0, high:0, medium:0, low:0,
             drifted:0, missing:0, file_deleted:0, reviewed:0};
  for (const c of list) if (c)
    for (const k of Object.keys(z)) z[k] += (c[k] || 0);
  return z;
}
function renderMetrics(){
  const host = $('metrics-slot');
  if (!host) return;
  const members = memberRepos();
  host.innerHTML =
    metricCards(sumCounts(members.map(r => state.counts[r.id])));
}

function sevChip(it){
  if (it.kind === 'duplicate')
    return `<span class="sev ${it.level}">${it.level.toUpperCase()}</span>`;
  return `<span class="sev state">${esc(it.kind.replace('_', ' ')
    .toUpperCase())}</span>`;
}

function issueRowHTML(it, showRepo){
  const loc = it.kind === 'duplicate'
    ? `${esc(it.a.file)}:${it.a.line} ↔ ${esc(it.b.file)}:${it.b.line}`
    : `${esc(it.var.file)}:${it.var.line}`;
  const reason = it.kind === 'duplicate'
    ? `${esc(it.reason)} · ${it.score.toFixed(2)}` : esc(it.reason);
  const done = it.review
    ? ` <span class="sev done">REVIEWED: ${esc(it.review.verdict)}</span>`
    : '';
  const rtag = showRepo && it.repo
    ? `<span class="repotag">${esc(it.repo.name)}</span> ` : '';
  return `<span class="bar" aria-hidden="true"></span>
    <button class="issue-btn" data-key="${esc(it.key)}"
      aria-expanded="false">
      <div class="title">${sevChip(it)} ${esc(it.title)}${done}</div>
      <div class="meta">${rtag}${reason} · ${loc}</div>
    </button>
    <span class="open" aria-hidden="true">Review</span>`;
}

function evidenceSide(tag, s){
  const scope = s.scope ? ` <span class="smeta">[${esc(s.scope)}]</span>` : '';
  return `<div class="sideCard">
    <span class="nm">${tag}: ${esc(s.name)}</span>${scope}
    <div>= <code>${esc(s.value)}</code></div>
    <div class="smeta">${esc(s.file)}:${s.line} · ${esc(s.status)} ·
      ${esc(s.origin)} · ${esc(s.lang)}${s.last_session
      ? ' · sess ' + esc(s.last_session) : ''}</div>
    ${s.status === 'drifted' && s.repo_value !== s.value
      ? `<div class="smeta">repo now: <code>${esc(s.repo_value)}</code></div>`
      : ''}
    ${s.note ? `<div class="note">✎ ${esc(s.note)}</div>` : ''}
  </div>`;
}

function detailHTML(it, rid){
  let body;
  if (it.kind === 'duplicate') {
    body = `<div class="sides">${evidenceSide('A', it.a)}
      ${evidenceSide('B', it.b)}</div>` +
      (it.review ? `<div class="verdict-note">Reviewed:
        <b>${esc(it.review.verdict)}</b>${it.review.note
        ? ' — ' + esc(it.review.note) : ''}</div>` : '') +
      `<form class="reviewform" data-kind="review"
         data-repo="${esc(rid)}" data-key="${esc(it.key)}">
        <label>Verdict
          <select name="verdict" aria-label="Review verdict">
            <option value="not_duplicate">not a duplicate (dismiss)</option>
            <option value="duplicate">confirmed duplicate</option>
            <option value="merged">merged / fixed</option>
          </select></label>
        <input name="note" placeholder="note (why)" aria-label="Review note">
        <button class="button primary small" type="submit">Save review
        </button>
      </form>`;
  } else {
    const v = it.var;
    body = `<div class="sides">
      <div class="sideCard"><span class="nm">${esc(v.name)}</span>
        <div>AI wrote: <code>${esc(v.value)}</code></div>
        <div class="smeta">${esc(v.file)}:${v.line} · last sess
          ${esc(v.last_session || '?')}</div></div>
      <div class="sideCard"><span class="nm">Repository now</span>
        <div>${it.kind === 'drifted'
          ? '<code>' + esc(v.repo_value) + '</code>'
          : esc(it.kind === 'missing'
            ? 'name no longer present in the file'
            : 'tracked file was deleted')}</div></div>
      </div>
      ${v.note ? `<div class="verdict-note">✎ ${esc(v.note)}</div>` : ''}
      <form class="reviewform" data-kind="annotate"
        data-repo="${esc(rid)}" data-name="${esc(v.name)}"
        data-file="${esc(v.file)}">
        <input name="note" placeholder="attach a note (e.g. intentional)"
          aria-label="Variable note">
        <button class="button small" type="submit">Save note</button>
      </form>`;
  }
  return `<div class="detail">${body}</div>`;
}

function pagerHTML(p){
  return `<div class="pager">
    <span>${p.total} result${p.total === 1 ? '' : 's'} ·
      page ${p.page} of ${p.pages}</span><span class="spacer"></span>
    <button class="button small" data-page="${p.page - 1}"
      ${p.page <= 1 ? 'disabled' : ''}>‹ Prev</button>
    <button class="button small" data-page="${p.page + 1}"
      ${p.page >= p.pages ? 'disabled' : ''}>Next ›</button>
  </div>`;
}

/* --------------------------------------------------------- group view */
async function renderGroupView(){
  const g = groupMeta();
  const members = memberRepos();
  const my = ++seq;
  const main = $('main');
  const composed = (g.compose_projects || []).length
    ? ` · Compose project <b>${esc(g.compose_projects.join(', '))}</b>` : '';
  const confirmedTxt = g.id === 'standalone' ? ''
    : (g.confirmed ? ' · memberships confirmed' : ' · unconfirmed');
  main.innerHTML = `
    <div class="heading">
      <div><h2>${esc(g.name)}</h2>
        <p class="sub">${members.length} repositor${members.length === 1
          ? 'y' : 'ies'}${composed}${confirmedTxt}</p></div>
      <div class="actions">
        <button class="button" id="act-refresh">Refresh</button>
        <button class="button" id="act-rescan">Full rescan</button>
        <button class="button primary" id="act-prompts">
          Generate fix prompts</button>
      </div>
    </div>
    <div id="metrics-slot">${metricCards(sumCounts(
      members.map(r => state.counts[r.id])))}</div>
    <div class="grid">
      <section class="panel" aria-label="Action queue">
        <div class="ph"><strong>Action queue</strong>
          <span class="phsub">sorted by confidence and state ·
            top of each repository</span></div>
        <div id="queue"><div class="loading">Loading queue</div></div>
      </section>
      <aside>
        <section class="panel" aria-label="AI fix prompts">
          <div class="ph"><strong>AI fix prompts</strong>
            <span class="phsub">repo-separated</span></div>
          <div style="padding:14px" id="prompt-cards"></div>
        </section>
        <section class="panel activity" style="margin-top:14px"
          aria-label="Recent activity">
          <div class="ph"><strong>Recent activity</strong></div>
          <div id="activity"><div class="loading">Loading</div></div>
        </section>
      </aside>
    </div>`;
  $('act-refresh').onclick = () => { loadTree(true); };
  $('act-rescan').onclick = () => rescanFlow(members, $('act-rescan'));
  $('act-prompts').onclick = () => promptsFlow(g.id);

  const cardsHost = $('prompt-cards');
  cardsHost.innerHTML = members.map(r => {
    const c = state.counts[r.id];
    return `<div class="prompt-repo">
      <span class="repoicon" aria-hidden="true">${esc(
        (r.name[0] || '?').toUpperCase())}</span>
      <span class="info"><b>${esc(r.name)}</b>
      <small>${c ? c.needs_attention + ' finding(s) need attention'
        : 'counting…'}</small></span>
      <button class="button primary small" data-rid="${esc(r.id)}">
        Generate</button></div>`;
  }).join('') + `<div class="guard"><b>Isolation guarantee</b><br>
    Each output contains only that repository's root, evidence, files,
    review state, validation expectations, and stop conditions.
    No prompt combines repositories.</div>`;
  cardsHost.querySelectorAll('button[data-rid]').forEach(b => {
    b.onclick = () => promptsFlow(null, b.dataset.rid);
  });

  if (!members.length) {
    $('queue').innerHTML = '<div class="empty"><b>No repositories in this ' +
      'group</b>Use the group settings (⚙) to add members.</div>';
    $('activity').innerHTML = '<div class="empty">No activity.</div>';
    return;
  }
  try {
    const ov = await api('/api/overview?group=' +
      encodeURIComponent(g.id));
    if (my !== seq) return;
    const qHost = $('queue');
    if (!ov.queue.length) {
      qHost.innerHTML = '<div class="empty"><b>Queue is clear</b>' +
        'No unreviewed findings in this group.</div>';
    } else {
      qHost.innerHTML = ov.queue.map(it =>
        `<div class="issue ${it.kind === 'duplicate' ? it.level : 'medium'}"
         >${issueRowHTML(it, true)}</div>`).join('');
      qHost.querySelectorAll('.issue-btn').forEach((b, i) => {
        // index-based: pair keys are only unique within one repository
        b.onclick = () => {
          const it = ov.queue[i];
          if (it && it.repo) { selectRepo(it.repo.id); }
        };
      });
    }
    const aHost = $('activity');
    aHost.innerHTML = ov.activity.length ? ov.activity.map(e =>
      `<div class="item"><b>${esc(e.action)}${e.name
        ? ': ' + esc(e.name) : ''}</b>
       <small>${esc(e.repo.name)}${e.file ? ' · ' + esc(e.file) : ''} ·
        ${esc((e.ts || '').slice(0, 19).replace('T', ' '))}</small></div>`
    ).join('') : '<div class="empty">No recorded events yet.</div>';
  } catch (e) {
    if (my !== seq) return;
    $('queue').innerHTML =
      `<div class="errorbox">${esc(e.message)}</div>`;
    $('activity').innerHTML = '';
  }
}

/* ---------------------------------------------------------- repo view */
function renderRepoView(){
  const r = repoMeta(state.sel.repo);
  const c = state.counts[r.id];
  const main = $('main');
  main.innerHTML = `
    <div class="heading">
      <div><h2>${esc(r.name)}</h2>
        <p class="sub"><code>${esc(r.path)}</code></p></div>
      <div class="actions">
        <button class="button" id="act-reconcile">Reconcile</button>
        <button class="button" id="act-rescan">Full rescan</button>
        <button class="button primary" id="act-prompt">Generate fix prompt
        </button>
      </div>
    </div>
    <div id="metrics-slot">${c ? metricCards(c)
      : '<div class="loading">Counting</div>'}</div>
    <div class="panel">
      <div class="ph" role="tablist" aria-label="Repository data">
        <button class="button small" role="tab" id="tab-issues"
          aria-selected="${state.sel.view === 'issues'}">Issues</button>
        <button class="button small" role="tab" id="tab-vars"
          aria-selected="${state.sel.view === 'vars'}">Variables</button>
        <span class="phsub">${c ? c.tracked + ' tracked variables' : ''}
        </span>
      </div>
      <div id="tab-body"><div class="loading">Loading</div></div>
    </div>`;
  $('act-rescan').onclick = () => rescanFlow([r], $('act-rescan'));
  $('act-reconcile').onclick = () => reconcileFlow(r, $('act-reconcile'));
  $('act-prompt').onclick = () => promptsFlow(null, r.id);
  $('tab-issues').onclick = () => { state.sel.view = 'issues'; render(); };
  $('tab-vars').onclick = () => { state.sel.view = 'vars'; render(); };
  if (state.sel.view === 'vars') renderVars(r);
  else renderIssues(r);
}

async function renderIssues(r){
  const host = $('tab-body');
  const f = state.issues;
  host.innerHTML = `
    <div class="filters">
      <input type="search" id="f-q" value="${esc(f.q)}"
        placeholder="filter by name or file…"
        aria-label="Filter issues by name or file">
      <label>Severity
        <select id="f-level" aria-label="Severity filter">
          <option value="">all</option>
          <option value="high"${f.level === 'high' ? ' selected' : ''}>high
          </option>
          <option value="medium"${f.level === 'medium' ? ' selected' : ''}>
            medium</option>
          <option value="low"${f.level === 'low' ? ' selected' : ''}>low
          </option>
        </select></label>
      <label>Type
        <select id="f-kind" aria-label="Issue type filter">
          <option value="">all</option>
          <option value="duplicate"${f.kind === 'duplicate'
            ? ' selected' : ''}>duplicates</option>
          <option value="drifted"${f.kind === 'drifted'
            ? ' selected' : ''}>drifted</option>
          <option value="missing"${f.kind === 'missing'
            ? ' selected' : ''}>missing</option>
          <option value="file_deleted"${f.kind === 'file_deleted'
            ? ' selected' : ''}>file deleted</option>
        </select></label>
      <label>Review
        <select id="f-review" aria-label="Review state filter">
          <option value="">all</option>
          <option value="unreviewed"${f.review === 'unreviewed'
            ? ' selected' : ''}>unreviewed</option>
          <option value="reviewed"${f.review === 'reviewed'
            ? ' selected' : ''}>reviewed</option>
        </select></label>
    </div>
    <div id="issue-list"><div class="loading">Loading issues</div></div>
    <div id="issue-pager"></div>`;
  const rerun = () => { state.issues.page = 1; renderIssues(r); };
  let deb;
  $('f-q').oninput = e => { clearTimeout(deb);
    deb = setTimeout(() => { state.issues.q = e.target.value; rerun(); },
      280); };
  $('f-level').onchange = e => { state.issues.level = e.target.value;
    rerun(); };
  $('f-kind').onchange = e => { state.issues.kind = e.target.value;
    rerun(); };
  $('f-review').onchange = e => { state.issues.review = e.target.value;
    rerun(); };

  const my = ++seq;
  let p;
  try {
    p = await api('/api/issues?repo=' + encodeURIComponent(r.id) +
      '&page=' + f.page + '&q=' + encodeURIComponent(f.q) +
      '&level=' + f.level + '&kind=' + f.kind + '&review=' + f.review);
  } catch (e) {
    if (my === seq) $('issue-list').innerHTML =
      `<div class="errorbox">${esc(e.message)}</div>`;
    return;
  }
  if (my !== seq) return;
  const list = $('issue-list');
  if (!p.items.length) {
    list.innerHTML = '<div class="empty"><b>No issues match</b>' +
      (p.total === 0 && !f.q && !f.level && !f.kind && !f.review
        ? 'This repository has no open findings.'
        : 'Try clearing a filter.') + '</div>';
  } else {
    list.innerHTML = p.items.map(it =>
      `<div class="issue ${it.kind === 'duplicate' ? it.level : 'medium'}"
        data-key="${esc(it.key)}">${issueRowHTML(it, false)}</div>`
    ).join('');
    list.querySelectorAll('.issue-btn').forEach(b => {
      b.onclick = () => {
        const key = b.dataset.key;
        const row = b.closest('.issue');
        const wasOpen = state.openIssue === key;
        list.querySelectorAll('.detail').forEach(d => d.remove());
        list.querySelectorAll('.issue-btn').forEach(x =>
          x.setAttribute('aria-expanded', 'false'));
        state.openIssue = wasOpen ? null : key;
        if (!wasOpen) {
          const it = p.items.find(x => x.key === key);
          row.insertAdjacentHTML('beforeend', detailHTML(it, r.id));
          b.setAttribute('aria-expanded', 'true');
          wireDetailForms(row, r);
        }
      };
    });
  }
  $('issue-pager').innerHTML = pagerHTML(p);
  $('issue-pager').querySelectorAll('button[data-page]').forEach(b => {
    b.onclick = () => { state.issues.page = parseInt(b.dataset.page, 10);
      renderIssues(r); };
  });
}

function wireDetailForms(row, r){
  row.querySelectorAll('form.reviewform').forEach(form => {
    form.onsubmit = async ev => {
      ev.preventDefault();
      const btn = form.querySelector('button[type=submit]');
      btn.disabled = true;
      try {
        if (form.dataset.kind === 'review') {
          await api('/api/review', {repo: form.dataset.repo,
            pair_key: form.dataset.key,
            verdict: form.querySelector('[name=verdict]').value,
            note: form.querySelector('[name=note]').value || null});
          toast('Review saved');
        } else {
          await api('/api/annotate', {repo: form.dataset.repo,
            name: form.dataset.name, file: form.dataset.file,
            note: form.querySelector('[name=note]').value});
          toast('Note saved');
        }
        state.openIssue = null;
        await refreshRepoCounts(r.id);
        renderIssues(r);
      } catch (e) { toast(e.message, true); }
      btn.disabled = false;
    };
  });
}

async function refreshRepoCounts(rid){
  try {
    const d = await api('/api/repo?id=' + encodeURIComponent(rid));
    state.counts[rid] = d.counts;
    renderSidebar();
    const slot = $('metrics-slot');
    if (slot && state.sel.repo === rid)
      slot.innerHTML = metricCards(d.counts);
  } catch (e) { /* non-fatal */ }
}

/* ------------------------------------------------------ variables view */
async function renderVars(r){
  const host = $('tab-body');
  const f = state.vars;
  host.innerHTML = `
    <div class="filters">
      <input type="search" id="v-q" value="${esc(f.q)}"
        placeholder="filter by name, file, value…"
        aria-label="Filter variables">
      <label>Status
        <select id="v-status" aria-label="Status filter">
          <option value="">all</option>
          ${['active', 'drifted', 'missing', 'file_deleted'].map(s =>
            `<option value="${s}"${f.status === s ? ' selected' : ''}>${s}
             </option>`).join('')}
        </select></label>
    </div>
    <div class="table-wrap">
    <table class="vars-table"><thead><tr>
      <th scope="col">dup</th><th scope="col">name</th>
      <th scope="col">scope</th><th scope="col">value</th>
      <th scope="col">location</th><th scope="col">status</th>
      <th scope="col">origin</th><th scope="col">session</th>
      <th scope="col">note</th></tr></thead>
      <tbody id="var-rows"><tr><td colspan="9" class="loading">Loading
        </td></tr></tbody></table></div>
    <div id="var-pager"></div>`;
  let deb;
  $('v-q').oninput = e => { clearTimeout(deb);
    deb = setTimeout(() => { state.vars.q = e.target.value;
      state.vars.page = 1; renderVars(r); }, 280); };
  $('v-status').onchange = e => { state.vars.status = e.target.value;
    state.vars.page = 1; renderVars(r); };
  const my = ++seq;
  let p;
  try {
    p = await api('/api/vars?repo=' + encodeURIComponent(r.id) +
      '&page=' + f.page + '&q=' + encodeURIComponent(f.q) +
      '&status=' + f.status);
  } catch (e) {
    if (my === seq) $('var-rows').innerHTML =
      `<tr><td colspan="9"><div class="errorbox">${esc(e.message)}
       </div></td></tr>`;
    return;
  }
  if (my !== seq) return;
  const tb = $('var-rows');
  tb.innerHTML = p.items.length ? p.items.map(v => `<tr>
    <td>${v.dup_level ? `<span class="sev ${v.dup_level}">
      ${v.dup_level.toUpperCase()}</span>` : ''}</td>
    <td><b>${esc(v.name)}</b></td>
    <td class="mono">${esc(v.scope)}</td>
    <td class="mono"><code>${esc(v.value)}</code></td>
    <td class="mono">${esc(v.file)}:${v.line}</td>
    <td>${esc(v.status)}</td><td>${esc(v.origin)}</td>
    <td class="mono">${esc(v.last_session)}</td>
    <td>${esc(v.note || '')}</td></tr>`).join('')
    : `<tr><td colspan="9"><div class="empty">No variables match.
       </div></td></tr>`;
  $('var-pager').innerHTML = pagerHTML(p);
  $('var-pager').querySelectorAll('button[data-page]').forEach(b => {
    b.onclick = () => { state.vars.page = parseInt(b.dataset.page, 10);
      renderVars(r); };
  });
}

/* ------------------------------------------------------ rescan / sync */
async function rescanFlow(repoList, trigger){
  if (trigger.disabled) return;
  trigger.disabled = true;
  const orig = trigger.textContent;
  try {
    for (let i = 0; i < repoList.length; i++) {
      const r = repoList[i];
      trigger.textContent =
        `Rescanning ${r.name} (${i + 1}/${repoList.length})…`;
      const res = await api('/api/rescan', {repo: r.id});
      toast(`${r.name}: rescanned ${res.totals.files} files, ` +
        `${res.totals.vars} vars`);
      await refreshRepoCounts(r.id);
    }
  } catch (e) { toast(e.message, true); }
  trigger.textContent = orig;
  trigger.disabled = false;
  setSync();
  if (!state.sel.repo) renderGroupView(); else renderRepoView();
}

async function reconcileFlow(r, trigger){
  if (trigger.disabled) return;
  trigger.disabled = true;
  const orig = trigger.textContent;
  trigger.textContent = 'Reconciling…';
  try {
    const res = await api('/api/reconcile', {repo: r.id, force: true});
    toast(`${r.name}: ${res.totals.drifted} drifted, ` +
      `${res.totals.missing} missing across ${res.totals.files} files`);
    await refreshRepoCounts(r.id);
  } catch (e) { toast(e.message, true); }
  trigger.textContent = orig;
  trigger.disabled = false;
  setSync();
  if (state.sel.repo) renderRepoView();
}

/* --------------------------------------------------------------- modal */
let lastFocus = null;
function modalOpen(title, bodyHTML, footHTML){
  lastFocus = document.activeElement;
  const back = $('modal');
  back.innerHTML = `<div class="modal" role="dialog" aria-modal="true"
      aria-label="${esc(title)}">
    <header><h3>${esc(title)}</h3>
      <button class="x" id="modal-x" aria-label="Close dialog">✕</button>
    </header>
    <div class="body" id="modal-body">${bodyHTML}</div>
    ${footHTML ? `<div class="foot" id="modal-foot">${footHTML}</div>` : ''}
  </div>`;
  back.hidden = false;
  $('modal-x').onclick = modalClose;
  back.onclick = e => { if (e.target === back) modalClose(); };
  const first = back.querySelector(
    'input,select,textarea,button:not(#modal-x)') || $('modal-x');
  first.focus();
}
function modalClose(){
  $('modal').hidden = true;
  $('modal').innerHTML = '';
  if (lastFocus) lastFocus.focus();
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('modal').hidden) modalClose();
});

/* -------------------------------------------------------------- prompts */
function download(filename, text){
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text],
    {type: 'text/markdown;charset=utf-8'}));
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 300);
}
async function copyText(text){
  try { await navigator.clipboard.writeText(text); return true; }
  catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) {}
    ta.remove();
    return ok;
  }
}
function promptCardHTML(item, i){
  if (item.skipped) {
    return `<div class="prompt-card"><div class="pc-head">
      <span class="repoicon" aria-hidden="true">${esc(
        (item.name[0] || '?').toUpperCase())}</span>
      <b>${esc(item.name)}</b>
      <span class="repotag">skipped</span>
      <span style="color:var(--sub);font-size:12px">${esc(item.skipped)}
      </span></div></div>`;
  }
  return `<div class="prompt-card"><div class="pc-head">
    <span class="repoicon" aria-hidden="true">${esc(
      (item.name[0] || '?').toUpperCase())}</span>
    <b>${esc(item.name)}</b>
    <span class="repotag">${esc(item.filename)}</span>
    <span style="flex:1"></span>
    <button class="button small" data-copy="${i}">Copy</button>
    <button class="button small" data-dl="${i}">Download .md</button>
    </div>
    <textarea readonly aria-label="Remediation prompt for ${esc(item.name)}"
      >${esc(item.prompt)}</textarea></div>`;
}
async function promptsFlow(groupId, repoId){
  modalOpen('AI fix prompts',
    '<div class="loading">Generating repository-scoped prompts</div>');
  let items;
  try {
    if (repoId) {
      const it = await api('/api/prompt', {repo: repoId});
      items = [it];
    } else {
      const res = await api('/api/prompts', {group: groupId});
      items = res.items;
    }
  } catch (e) {
    $('modal-body').innerHTML =
      `<div class="errorbox">${esc(e.message)}</div>`;
    return;
  }
  $('modal-body').innerHTML =
    `<div class="guard" style="margin:0 0 12px"><b>One prompt per
    repository.</b> Prompts are ephemeral — copy or download what you
    need; nothing is written into any repository.</div>` +
    items.map((it, i) => promptCardHTML(it, i)).join('');
  $('modal-body').querySelectorAll('button[data-copy]').forEach(b => {
    b.onclick = async () => {
      const it = items[parseInt(b.dataset.copy, 10)];
      toast(await copyText(it.prompt)
        ? 'Prompt copied to clipboard' : 'Copy failed — select manually',
        false);
    };
  });
  $('modal-body').querySelectorAll('button[data-dl]').forEach(b => {
    b.onclick = () => {
      const it = items[parseInt(b.dataset.dl, 10)];
      download(it.filename, it.prompt);
    };
  });
}

/* ------------------------------------------------------------ discovery */
async function discoveryFlow(){
  modalOpen('Container discovery',
    '<div class="loading">Inspecting running containers</div>');
  let res;
  try { res = await api('/api/discovery'); }
  catch (e) {
    $('modal-body').innerHTML =
      `<div class="errorbox">${esc(e.message)}</div>`;
    return;
  }
  let html = '';
  if (res.docker === 'unavailable') {
    html = `<div class="empty"><b>Docker CLI not available</b>
      Discovery is optional — confirmed groups keep working without it.
      You can always create groups manually.</div>`;
  } else if (res.docker === 'error') {
    html = `<div class="errorbox">Docker did not respond:
      ${esc(res.error)}<br>Confirmed groups are unaffected.</div>`;
  } else if (!res.suggestions.length) {
    html = `<div class="empty"><b>No group suggestions</b>
      ${res.containers_seen} running container(s) inspected; none mount
      two or more registered repositories. Confirmed groups persist
      regardless of container state.</div>`;
  } else {
    html = res.suggestions.map((s, i) => `<div class="suggestion">
      <b>${esc(s.name)}</b>
      ${s.already_grouped
        ? '<span class="repotag">already grouped</span>' : ''}
      <div class="tags">${s.repo_names.map(n =>
        `<span class="repotag">${esc(n)}</span>`).join(' ')}</div>
      <div style="color:var(--sub);font-size:12px">containers:
        ${s.containers.map(esc).join(', ')}</div>
      <div class="reviewform">
        <input value="${esc(s.name)}" data-name="${i}"
          aria-label="Group name for suggestion ${esc(s.name)}">
        <button class="button primary small" data-confirm="${i}"
          ${s.already_grouped ? 'disabled' : ''}>Confirm group</button>
      </div></div>`).join('') +
      `<div class="guard">Suggestions never change memberships on their
       own — a group is saved only when you confirm it, and it persists
       after the containers stop.</div>`;
  }
  $('modal-body').innerHTML = html;
  $('modal-body').querySelectorAll('button[data-confirm]').forEach(b => {
    b.onclick = async () => {
      const i = parseInt(b.dataset.confirm, 10);
      const s = res.suggestions[i];
      const name = $('modal-body')
        .querySelector(`input[data-name="${i}"]`).value || s.name;
      b.disabled = true;
      try {
        await api('/api/discovery/confirm', {name,
          repo_ids: s.repo_ids, compose_projects: s.compose_projects});
        toast('Group "' + name + '" confirmed');
        modalClose();
        loadTree(false);
      } catch (e) { toast(e.message, true); b.disabled = false; }
    };
  });
}

/* --------------------------------------------------------- group modal */
function groupModal(groupId){
  const g = groupId
    ? state.tree.groups.find(x => x.id === groupId) : null;
  const registered = state.tree.repos.filter(r => r.registered);
  const checked = new Set(g ? g.repo_ids : []);
  modalOpen(g ? 'Manage group' : 'New group', `
    <div class="field"><label for="g-name">Group name</label>
      <input type="text" id="g-name" value="${esc(g ? g.name : '')}"
        placeholder="e.g. Trading platform"></div>
    <div class="field"><label id="g-members-label">Member repositories
      </label>
      <div class="checklist" role="group" aria-labelledby="g-members-label">
      ${registered.length ? registered.map(r =>
        `<label><input type="checkbox" value="${esc(r.id)}"
          ${checked.has(r.id) ? ' checked' : ''}>
          ${esc(r.name)} <span style="color:var(--sub)">${esc(r.path)}
          </span></label>`).join('')
        : '<label>No registered repositories.</label>'}
      </div></div>
    ${g && (g.compose_projects || []).length
      ? `<p style="color:var(--sub);font-size:12px">Compose projects:
         ${g.compose_projects.map(esc).join(', ')}</p>` : ''}`,
    (g ? `<button class="button danger" id="g-delete">Delete group
      </button><span style="flex:1"></span>` : '') +
    `<button class="button" id="g-cancel">Cancel</button>
     <button class="button primary" id="g-save">${g ? 'Save changes'
       : 'Create group'}</button>`);
  $('g-cancel').onclick = modalClose;
  if (g) $('g-delete').onclick = async () => {
    if (!confirm(`Delete group "${g.name}"? Repositories and their data ` +
                 'are not touched.')) return;
    try {
      await api('/api/groups/delete', {id: g.id});
      toast('Group deleted');
      modalClose();
      loadTree(false);
    } catch (e) { toast(e.message, true); }
  };
  $('g-save').onclick = async () => {
    const name = $('g-name').value.trim();
    if (!name) { toast('Group name is required', true); return; }
    const ids = [...$('modal-body')
      .querySelectorAll('input[type=checkbox]:checked')]
      .map(cb => cb.value);
    try {
      if (g) {
        if (name !== g.name)
          await api('/api/groups/rename', {id: g.id, name});
        await api('/api/groups/members', {id: g.id, repo_ids: ids});
        toast('Group updated');
      } else {
        await api('/api/groups/create', {name, repo_ids: ids});
        toast('Group created');
      }
      modalClose();
      loadTree(true);
    } catch (e) { toast(e.message, true); }
  };
}

/* ------------------------------------------------------------ app bar */
$('btn-refresh').onclick = () => loadTree(true);
$('btn-discovery').onclick = discoveryFlow;
$('btn-prompts').onclick = () => {
  if (state.sel.repo) promptsFlow(null, state.sel.repo);
  else promptsFlow(state.sel.group);
};
$('btn-newgroup').onclick = () => groupModal(null);
$('menu-btn').onclick = () => {
  const open = $('side').classList.toggle('open');
  $('menu-btn').setAttribute('aria-expanded', String(open));
  $('side-backdrop').classList.toggle('open', open);
};
$('side-backdrop').onclick = () => {
  $('side').classList.remove('open');
  $('side-backdrop').classList.remove('open');
  $('menu-btn').setAttribute('aria-expanded', 'false');
};

loadTree(false);
"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>varmem live</title>
<link rel="icon" type="image/png" href="__FAVICON__">
<style>__CSS__</style></head><body>
<a class="skip" href="#main">Skip to content</a>
<header class="appbar">
  <button id="menu-btn" class="menu-btn" aria-label="Toggle navigation"
    aria-expanded="false">
    <svg width="16" height="14" viewBox="0 0 16 14" aria-hidden="true"
      fill="none" stroke="currentColor" stroke-width="2">
      <path d="M1 1h14M1 7h14M1 13h14"/></svg>
  </button>
  <div class="logo"><img class="mark" src="__LOGO__" alt="" width="27"
    height="27">
    varmem <span style="color:#aab3c2;font-weight:400">live</span></div>
  <div class="crumb" id="crumb"></div>
  <div class="right">
    <span class="sync" id="sync" role="status" aria-live="polite"></span>
    <button class="topbtn" id="btn-refresh">Refresh</button>
    <button class="topbtn" id="btn-discovery">Discovery</button>
    <button class="topbtn primary" id="btn-prompts">Generate fix prompts
    </button>
  </div>
</header>
<div class="shell">
  <nav class="side" id="side" aria-label="Groups and repositories">
    <div class="side-label">Groups</div>
    <div id="nav-groups"></div>
    <div class="side-label">Manage</div>
    <button class="button small newgroup" id="btn-newgroup">＋ New group…
    </button>
  </nav>
  <main class="main" id="main" tabindex="-1">
    <div class="loading">Loading</div>
  </main>
</div>
<div class="backdrop" id="side-backdrop"></div>
<div class="modal-backdrop" id="modal" hidden></div>
<div class="toasts" id="toasts" role="status" aria-live="polite"></div>
<script>__JS__</script>
</body></html>
""".replace("__CSS__", CSS).replace("__JS__", _JS) \
   .replace("__FAVICON__", FAVICON_DATA_URI).replace("__LOGO__", LOGO_DATA_URI)
