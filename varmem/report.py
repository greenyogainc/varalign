"""Self-contained HTML report — one repository, no network.

Shares the control tower's visual language (varmem/live_ui.py) but stays a
single static file: because a static page cannot POST, the Override button
generates the exact `dup-note` CLI command (verdict + note baked in) to
copy; the live dashboard and the VS Code extension (EXTENSION.md) replace
that with native one-click writes.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import build_info, config, duplicates, store

CSS = """
:root{
  --ink:#101828; --sub:#667085; --line:#e4e7ec; --panel:#fff; --soft:#f8fafc;
  --bg:#f5f7fb; --violet:#6558e8; --violet-ink:#5146c9; --red:#d92d20;
  --amber:#dc6803; --bar-bg:#121726; --bar-line:#2a3140;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.45 system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
button,input{font:inherit}
:focus-visible{outline:2px solid var(--violet);outline-offset:2px}
code{background:#f2f4f7;padding:.08rem .35rem;border-radius:4px;
  font-size:.92em;word-break:break-all}
.appbar{position:sticky;top:0;z-index:20;height:56px;display:flex;
  align-items:center;gap:12px;padding:0 18px;background:var(--bar-bg);
  color:#fff;border-bottom:1px solid var(--bar-line)}
.logo{font-size:16px;font-weight:800;letter-spacing:-.02em;display:flex;
  align-items:center;gap:8px;white-space:nowrap}
.logo .mark{display:inline-grid;place-items:center;width:27px;height:27px;
  border-radius:8px;background:linear-gradient(135deg,#7c6ff1,#4d43c5)}
.appbar .meta{color:#aab3c2;font-size:12px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.wrap{padding:18px 22px 40px;max-width:1240px;margin:0 auto}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;
  margin:0 0 16px}
.metric{background:var(--panel);border:1px solid var(--line);
  border-radius:13px;padding:12px 14px}
.metric .lab{color:var(--sub);font-size:11px;font-weight:650}
.metric b{display:block;font-size:24px;margin:4px 0 2px;
  letter-spacing:-.03em}
.metric .delta{font-size:10px;color:#98a2b3}
.metric b.bad{color:var(--red)} .metric b.med{color:var(--amber)}
.tabs{display:flex;gap:6px;margin-bottom:-1px}
.tabs button{background:var(--panel);color:#475467;
  border:1px solid var(--line);border-bottom:0;padding:8px 14px;
  cursor:pointer;border-radius:9px 9px 0 0;font-weight:700}
.tabs button[aria-selected="true"]{color:var(--violet-ink);
  background:var(--soft)}
.pane{border:1px solid var(--line);border-radius:0 10px 10px 10px;
  padding:14px;background:var(--panel)}
.filters{display:flex;gap:10px;align-items:center;margin-bottom:12px;
  flex-wrap:wrap}
.filters input[type=search]{flex:1;min-width:180px;padding:8px 10px;
  border:1px solid #d0d5dd;border-radius:8px}
.filters label{display:flex;gap:6px;align-items:center;color:var(--sub);
  font-size:12px}
.sev{display:inline-block;padding:2px 7px;border-radius:5px;font-size:10px;
  font-weight:800;letter-spacing:.04em}
.sev.high{background:#fff1f0;color:#b42318}
.sev.medium{background:#fff7ed;color:#b54708}
.sev.low{background:#fefce8;color:#a16207}
.reason{display:inline-block;padding:2px 7px;border-radius:5px;
  background:#f2f4f7;color:#475467;font-size:11px}
.pair{border:1px solid var(--line);border-left-width:5px;border-radius:9px;
  padding:11px 13px;margin-bottom:10px;background:var(--panel)}
.pair.high{border-left-color:#f04438}
.pair.medium{border-left-color:#f79009}
.pair.low{border-left-color:#facc15}
.pair .sides{display:grid;grid-template-columns:1fr 1fr;gap:10px;
  margin-top:8px}
.side{background:var(--soft);border:1px solid #eef0f3;border-radius:7px;
  padding:9px 11px;overflow-x:auto;min-width:0}
.side .nm{font-weight:750}
.meta{color:#98a2b3;font-size:11px}
.dismissed{opacity:.45}
.note{color:#9a6700;font-size:12px}
.ov{margin-top:9px}
.ov button,.ovform button{background:var(--violet);border:0;color:#fff;
  padding:6px 12px;border-radius:7px;cursor:pointer;font-weight:700}
.ovform{display:none;margin-top:8px}
.ovform.open{display:block}
.ovform input,.ovform select{background:#fff;border:1px solid #d0d5dd;
  padding:6px 9px;border-radius:7px;margin:0 6px 6px 0}
.ovform textarea.cmd{width:100%;margin-top:6px;background:#111827;
  color:#9ee493;border:1px solid #2c2e33;padding:9px;border-radius:7px;
  font:11px/1.5 ui-monospace,Consolas,monospace}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid #eef0f3;
  vertical-align:top;font-size:12px}
th{color:#9fa2a8;font-weight:700;position:sticky;top:0;
  background:var(--panel)}
.empty{padding:20px;text-align:center;color:var(--sub)}
@media (max-width:900px){
  .metrics{grid-template-columns:repeat(2,1fr)}
  .pair .sides{grid-template-columns:1fr}
  .wrap{padding:12px 10px 40px}
  .appbar .meta{display:none}
}
"""

_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>varmem report — __PROJECT__</title>
<style>__CSS__</style></head><body>
<header class="appbar">
  <div class="logo"><span class="mark" aria-hidden="true">V</span>
    varmem report</div>
  <span class="meta">__PROJECT__ · generated __GENERATED__ ·
    static snapshot (use `varmem live` for one-click overrides)</span>
</header>
<div class="wrap">
<div class="metrics">
  <div class="metric"><span class="lab">Variables</span><b id="c-vars"></b>
    <span class="delta">AI-written <span id="c-claude"></span> ·
      baseline <span id="c-scan"></span></span></div>
  <div class="metric"><span class="lab">Duplicate suspects</span>
    <b class="bad" id="c-dups"></b>
    <span class="delta" id="c-lv"></span></div>
  <div class="metric"><span class="lab">Drifted / missing</span>
    <b class="med" id="c-drift"></b>
    <span class="delta">live state differs</span></div>
  <div class="metric"><span class="lab">Reviewed pairs</span>
    <b id="c-rev"></b><span class="delta">verdicts recorded</span></div>
</div>
<div class="tabs" role="tablist" aria-label="Report sections">
  <button id="tab-d" role="tab" aria-selected="true"
    onclick="show('d')">Duplicates</button>
  <button id="tab-v" role="tab" aria-selected="false"
    onclick="show('v')">All variables</button>
</div>
<div class="pane" id="pane-d">
  <div class="filters">
    <input type="search" id="fd" placeholder="filter by name or file…"
      aria-label="Filter duplicates" oninput="renderDups()">
    <label><input type="checkbox" id="showdis" onchange="renderDups()">
      show dismissed</label>
  </div>
  <div id="dups"></div>
</div>
<div class="pane" id="pane-v" style="display:none">
  <div class="filters">
    <input type="search" id="fv" placeholder="filter by name, file, value…"
      aria-label="Filter variables" oninput="renderVars()">
  </div>
  <div class="table-wrap">
  <table><thead><tr><th scope="col">dup</th><th scope="col">name</th>
  <th scope="col">scope</th><th scope="col">value</th>
  <th scope="col">location</th><th scope="col">status</th>
  <th scope="col">origin</th><th scope="col">session</th>
  <th scope="col">note</th></tr></thead>
  <tbody id="vars"></tbody></table></div>
</div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function show(which){
  for (const t of ['d','v']) {
    document.getElementById('pane-'+t).style.display = t===which?'':'none';
    document.getElementById('tab-'+t)
      .setAttribute('aria-selected', String(t===which));
  }
}
function side(s){
  return `<div class="side"><span class="nm">${esc(s.name)}</span>` +
    (s.scope?` <span class="meta">[${esc(s.scope)}]</span>`:'') +
    ` = <code>${esc(s.value)}</code><br><span class="meta">${esc(s.file)}:${s.line}` +
    ` · ${esc(s.status)} · ${esc(s.origin)}${s.last_session?' · sess '+esc(s.last_session):''}</span>` +
    (s.status==='drifted' && s.repo_value!==s.value
      ?`<br><span class="meta">repo now: <code>${esc(s.repo_value)}</code></span>`:'') +
    (s.note?`<br><span class="note">✎ ${esc(s.note)}</span>`:'') + `</div>`;
}
function renderDups(){
  const q = document.getElementById('fd').value.toLowerCase();
  const showDis = document.getElementById('showdis').checked;
  const host = document.getElementById('dups');
  host.innerHTML = '';
  let shown = 0;
  DATA.dups.forEach((d, idx) => {
    const dismissed = d.review && d.review.verdict === 'not_duplicate';
    if (dismissed && !showDis) return;
    const hay = (d.a.name+' '+d.b.name+' '+d.a.file+' '+d.b.file).toLowerCase();
    if (q && !hay.includes(q)) return;
    shown++;
    const el = document.createElement('div');
    el.className = 'pair ' + d.level + (dismissed ? ' dismissed' : '');
    el.innerHTML =
      `<span class="sev ${d.level}">${d.level.toUpperCase()}</span> ` +
      `<span class="reason">${esc(d.reason)} · score ${d.score}</span>` +
      (d.review ? ` <span class="note">verdict: ${esc(d.review.verdict)}` +
        (d.review.note ? ' — '+esc(d.review.note) : '') + `</span>` : '') +
      `<div class="sides">${side(d.a)}${side(d.b)}</div>` +
      `<div class="ov"><button onclick="toggleForm(${idx})"
          aria-expanded="false" id="ovb-${idx}">Override / note…</button>
        <div class="ovform" id="ovf-${idx}">
          <select id="ovv-${idx}" aria-label="Verdict">
            <option value="not_duplicate">not a duplicate (dismiss)</option>
            <option value="duplicate">confirmed duplicate</option>
            <option value="merged">merged / fixed</option>
          </select>
          <input id="ovn-${idx}" size="42" placeholder="note (why)"
            aria-label="Review note">
          <button onclick="makeCmd(${idx})">Generate command</button>
          <textarea class="cmd" id="ovc-${idx}" rows="2" readonly
            aria-label="Command to copy"
            onclick="this.select();document.execCommand('copy')"></textarea>
        </div></div>`;
    host.appendChild(el);
  });
  if (!shown) host.innerHTML =
    '<div class="empty">no duplicate suspects match.</div>';
}
function toggleForm(i){
  const open = document.getElementById('ovf-'+i).classList.toggle('open');
  document.getElementById('ovb-'+i)
    .setAttribute('aria-expanded', String(open));
}
function makeCmd(i){
  const d = DATA.dups[i];
  const v = document.getElementById('ovv-'+i).value;
  const n = document.getElementById('ovn-'+i).value.replace(/"/g, "'");
  const c = `python "${DATA.varmem_py}" --project "${DATA.project}" dup-note ` +
            `"${d.pair_key}" --verdict ${v}` + (n ? ` --note "${n}"` : '');
  const ta = document.getElementById('ovc-'+i);
  ta.value = c + '\\n(click to copy — rerun report after)';
}
function renderVars(){
  const q = document.getElementById('fv').value.toLowerCase();
  const tb = document.getElementById('vars');
  tb.innerHTML = '';
  DATA.vars.forEach(v => {
    const hay = (v.name+' '+v.file+' '+(v.value||'')).toLowerCase();
    if (q && !hay.includes(q)) return;
    const lvl = DATA.varLevel[v.id];
    const tr = document.createElement('tr');
    tr.innerHTML =
      `<td>${lvl?`<span class="sev ${lvl}">${lvl.toUpperCase()}</span>`:''}</td>` +
      `<td><b>${esc(v.name)}</b></td><td class="meta">${esc(v.scope)}</td>` +
      `<td><code>${esc(v.value)}</code></td>` +
      `<td class="meta">${esc(v.file)}:${v.line}</td><td>${esc(v.status)}</td>` +
      `<td class="meta">${esc(v.origin)}</td><td class="meta">${esc(v.last_session)}</td>` +
      `<td class="note">${esc(v.note||'')}</td>`;
    tb.appendChild(tr);
  });
}
document.getElementById('c-vars').textContent = DATA.vars.length;
document.getElementById('c-claude').textContent =
  DATA.counts.claude + (DATA.counts.kilo || 0);
document.getElementById('c-scan').textContent = DATA.counts.scan;
document.getElementById('c-drift').textContent =
  DATA.counts.drifted + DATA.counts.missing;
document.getElementById('c-rev').textContent = DATA.reviewedPairs || 0;
document.getElementById('c-dups').textContent =
  DATA.dups.length + (DATA.omitted ? ' (+' + DATA.omitted + ' omitted)' : '');
document.getElementById('c-lv').textContent =
  ['high','medium','low'].map(l =>
    l+': '+(DATA.levelCounts ? DATA.levelCounts[l] : 0)).join(' · ');
renderDups(); renderVars();
</script></body></html>
"""


def report_data(root: Path, cfg: dict | None = None) -> dict:
    """Everything the report/live UI needs, as one JSON-ready dict."""
    cfg = cfg or config.load(root)
    st = store.open_store(root)
    all_dups = duplicates.find_duplicates(st, include_dismissed=True)
    level_counts = {lv: sum(1 for d in all_dups if d["level"] == lv)
                    for lv in ("high", "medium", "low")}
    dups = all_dups[: cfg["report_max_pairs"]]  # already score-sorted
    rows = sorted(st.all_rows(),
                  key=lambda r: (r["scope"] != "", r["file"],
                                 r["line"] or 0))
    var_level: dict[str, str] = {}
    order = {"high": 3, "medium": 2, "low": 1}
    for d in dups:
        if d["review"] and d["review"]["verdict"] == "not_duplicate":
            continue
        for s in (d["a"], d["b"]):
            cur = var_level.get(s["id"])
            if cur is None or order[d["level"]] > order[cur]:
                var_level[s["id"]] = d["level"]
    counts = {
        "claude": sum(1 for r in rows if r["origin"] == "claude"),
        "kilo": sum(1 for r in rows if r["origin"] == "kilo"),
        "scan": sum(1 for r in rows if r["origin"] == "scan"),
        "drifted": sum(1 for r in rows if r["status"] == "drifted"),
        "missing": sum(1 for r in rows if r["status"] == "missing"),
        "file_deleted": sum(1 for r in rows
                            if r["status"] == "file_deleted"),
    }
    # full-set review coverage (all_dups is pre-truncation)
    unreviewed_levels = {lv: sum(1 for d in all_dups
                                 if d["level"] == lv and not d["review"])
                         for lv in ("high", "medium", "low")}
    reviewed_pairs = sum(1 for d in all_dups
                         if d["review"] and not d["review"].get("learned"))
    learned_suppressed = sum(1 for d in all_dups
                             if d["review"] and d["review"].get("learned"))
    return {
        "project": str(root).replace("\\", "/"),
        "name": root.name,
        "generated": store.now_iso()[:19],
        "engine": build_info(),  # version + build commit, so a stale bundled
                                 # engine is visible in the UI (Bug 4)
        "varmem_py": (Path(__file__).resolve().parent.parent
                      / "varmem.py").as_posix(),
        "vars": [duplicates._side(r) for r in rows],
        "dups": dups,
        "omitted": max(0, len(all_dups) - len(dups)),
        "levelCounts": level_counts,
        "unreviewedLevels": unreviewed_levels,
        "reviewedPairs": reviewed_pairs,
        "learnedSuppressed": learned_suppressed,
        "varLevel": var_level,
        "counts": counts,
    }


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_report(root: Path, out: Path | None = None) -> Path:
    data = report_data(root)
    payload = json.dumps(data).replace("</", "<\\/")
    html = (_TEMPLATE
            .replace("__CSS__", CSS)
            .replace("__PROJECT__", _esc(data["name"]))
            .replace("__GENERATED__", data["generated"])
            .replace("__DATA__", payload))
    out = out or (config.varmem_dir(root) / "report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
