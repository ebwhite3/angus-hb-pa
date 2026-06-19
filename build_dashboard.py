#!/usr/bin/env python3
"""Build the Angus P&A dashboard: wells.json + rig_reports.json -> site/index.html
Usage: python3 build_dashboard.py [dir]   (dir defaults to script location)
"""
import json, base64, os, sys, datetime

D = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))

wells = json.load(open(os.path.join(D, 'wells.json')))
reports = json.load(open(os.path.join(D, 'rig_reports.json')))

logo_b64 = ''
logo_path = os.path.join(D, 'ns-logo.png')
if os.path.exists(logo_path):
    logo_b64 = base64.b64encode(open(logo_path, 'rb').read()).decode()

permits = {}
ppath = os.path.join(D, 'permits.json')
if os.path.exists(ppath):
    permits = json.load(open(ppath))

# Slack-sourced NOI approvals (from #angus_managers, posted by Luc). Authoritative
# for "approved" status + permit link so an approval shows even when the workbook
# is stale/unreadable. Keyed by well_norm -> {well_display, approved_date, permit, url, ...}.
slack_appr = {}
sapath = os.path.join(D, 'slack_approvals.json')
if os.path.exists(sapath):
    slack_appr = json.load(open(sapath)).get('approvals', {})

# NOTE: the banner's "current rig well" is set AFTER reconciliation (below), from
# the reconciled per-well rig status — NOT from a single globally-newest report.
# A prior well's "Job Complete" and a new well's "Day 1" can share the same date;
# tie-breaking by day number would pick the completed well and wrongly blank the
# banner. The reconciled status is the only reliable signal for who is on the rig.

# ---- Reconcile execution state from the daily rig reports ----------------------
# The reports are authoritative for rig start / finish / days and the rig &
# complete statuses. The workbook stays authoritative for the permit pipeline
# (well list, API, permit #, NOI status, approval date, fire permit, type, TD)
# and for the pre-rig statuses 'ready' / 'pending'. Reports win where present;
# the workbook is the fallback for wells no rig has touched yet.
from collections import defaultdict
reps_by_well = defaultdict(list)
for r in reports['reports']:
    reps_by_well[r['well']].append(r)   # reports key the normalized name as 'well'

def latest_mobilization(reps):
    """Reports for the most recent mobilization only. A new block begins whenever
    the crew Day-N resets (drops vs the prior report in date order), so a rig
    re-entry later does not inflate the day count."""
    reps = sorted(reps, key=lambda r: (r['date'], r['day']))
    block, prev = [], None
    for r in reps:
        if prev is not None and r['day'] <= prev:
            block = []
        block.append(r)
        prev = r['day']
    return block

today = datetime.date.today()
flags = []

for w in wells['wells']:
    # API: zero-pad to 10 digits so the CalGEM leading zero is never dropped.
    api = (w.get('api') or '').strip()
    if api.endswith('.0'): api = api[:-2]
    if api.isdigit(): api = api.zfill(10)
    w['api'] = api

    # Permit #/link: spreadsheet (via extract_wells.py) first, permits.json fills gaps.
    p = permits.get(w['well_norm'])
    if p:
        if not w.get('permit'): w['permit'] = p['permit']
        if not w.get('permit_url'): w['permit_url'] = p['url']

    # Slack-sourced approval override (authoritative for "approved"). Lifts a well
    # from pending -> approved/ready and fills the permit link when Luc posts it in
    # #angus_managers. Rig/complete status from the reports (below) still wins.
    a = slack_appr.get(w['well_norm'])
    if a:
        if not (w.get('noi_status') or '').strip().startswith('0'):
            w['noi_status'] = '0. Approved'
            w['noi_source'] = 'slack'
            w['status'] = 'ready'
            if not w.get('approved') and a.get('approved_date'):
                w['approved'] = a['approved_date']
        if not w.get('permit') and a.get('permit'): w['permit'] = a['permit']
        if not w.get('permit_url') and a.get('url'): w['permit_url'] = a['url']

    # Execution state: reports override the workbook where they exist.
    sheet_start, sheet_end = w.get('ops_start'), w.get('ops_end')
    reps = reps_by_well.get(w['well_norm'])
    if reps:
        block = latest_mobilization(reps)
        w['exec_source'] = 'reports'
        w['rig_days'] = max(r['day'] for r in block)            # crew Day-N count
        # Rig start: Day-1 date when we have it, else fall back to the workbook.
        w['ops_start'] = block[0]['date'] if block[0]['day'] == 1 else (sheet_start or block[0]['date'])
        jc = [r for r in block if r.get('job_complete')]
        if jc:
            w['ops_end'] = max(jc, key=lambda r: (r['date'], r['day']))['date']
            w['status'] = 'complete'
        else:
            w['ops_end'] = None
            w['status'] = 'rig'
            # Guardrails for the "stays rig forever" failure mode.
            if sheet_end:
                flags.append(f"{w['well']}: workbook shows P&A complete ({sheet_end}) but no "
                             f"“Job Complete” rig report is on file — verify status.")
            else:
                gap = (today - datetime.date.fromisoformat(block[-1]['date'])).days
                if gap > 3:
                    flags.append(f"{w['well']}: flagged Rig on well but no rig report in {gap} days "
                                 f"— check for a missing Job Complete.")
    else:
        # No reports: keep the workbook-derived status and dates (fallback tier).
        w['exec_source'] = 'spreadsheet'

# ---- Banner well: derive from the reconciled rig status ------------------------
# A well is "on the rig" only if its latest mobilization has no Job Complete
# (status == 'rig', sourced from reports). Pick the active well with the most
# recent report date. If more than one is active, the newest wins and the rest
# are flagged. If none is active, blank current_rig_well so the banner shows the
# most recent completion. This replaces the old global-latest-report heuristic
# that mis-resolved a same-day "Job Complete + new spud" handoff.
def _well_last_date(wn):
    rs = reps_by_well.get(wn)
    return max((r['date'] for r in rs), default='')

active = [w for w in wells['wells'] if w.get('status') == 'rig' and w.get('exec_source') == 'reports']
if active:
    active.sort(key=lambda w: _well_last_date(w['well_norm']), reverse=True)
    cw = active[0]
    reports['meta']['current_rig_well'] = cw['well_norm']
    cw_rep = max(reps_by_well[cw['well_norm']], key=lambda r: (r['date'], r['day']))
    reports['meta']['current_rig_well_display'] = cw_rep.get('well_display', cw['well_norm'])
    for w in active[1:]:
        flags.append(f"{w['well']}: flagged Rig on well at the same time as "
                     f"{cw['well']} — only one rig is on site; verify a Job Complete "
                     f"is on file for the finished well.")
else:
    reports['meta']['current_rig_well'] = None

built_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
built = datetime.datetime.now().strftime('%B %-d, %Y %-I:%M %p') if os.name != 'nt' else datetime.datetime.now().strftime('%B %d, %Y %I:%M %p')  # fallback only

payload = json.dumps({'wells': wells, 'reports': reports, 'built': built, 'built_iso': built_iso, 'flags': flags}, ensure_ascii=False)
payload = payload.replace('</', '<\\/')  # script-tag safety

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Angus Petroleum — Huntington Beach P&A Dashboard</title>
<style>
:root{
  --ns-blue:#0097fd; --ns-blue-dark:#0072c0; --ns-blue-light:#eaf5fe;
  --ink:#1d2733; --gray:#5d6b7a; --line:#dde6ee; --bg:#f4f8fb; --card:#ffffff;
  --ok:#1e8a4c; --ok-bg:#e6f5ec; --warn:#b97a00; --warn-bg:#fdf3df;
  --pend:#6b7785; --pend-bg:#eef1f5; --rig:#0072c0; --rig-bg:#dff0ff;
}
*{box-sizing:border-box}
body{margin:0;font-family:'Segoe UI',system-ui,-apple-system,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.45}
.wrap{max-width:1200px;margin:0 auto;padding:0 20px}
header{background:#fff;border-bottom:3px solid var(--ns-blue)}
.hrow{display:flex;align-items:center;gap:24px;padding:16px 0;flex-wrap:wrap}
.hrow img{height:44px}
.htitle h1{font-size:21px;margin:0;font-weight:650}
.htitle p{margin:2px 0 0;color:var(--gray);font-size:13px}
.htitle{flex:1 1 auto;min-width:0}
.hmeta{margin-left:auto;text-align:right;font-size:12.5px;color:var(--gray);white-space:nowrap}
.hmeta b{color:var(--ink)}
.banner{background:linear-gradient(90deg,var(--ns-blue-dark),var(--ns-blue));color:#fff;margin:20px 0;border-radius:10px;padding:18px 22px;display:flex;gap:18px;align-items:flex-start;box-shadow:0 2px 8px rgba(0,80,140,.18)}
.banner .pulse{flex:none;margin-top:4px;width:14px;height:14px;border-radius:50%;background:#7CFC9A;box-shadow:0 0 0 0 rgba(124,252,154,.7);animation:pulse 1.8s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(124,252,154,.6)}70%{box-shadow:0 0 0 11px rgba(124,252,154,0)}100%{box-shadow:0 0 0 0 rgba(124,252,154,0)}}
.banner h2{margin:0 0 4px;font-size:17px}
.banner p{margin:0;font-size:14px;opacity:.96}
.banner .bdate{font-size:12px;opacity:.8;margin-top:6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .n{font-size:30px;font-weight:700;line-height:1.1}
.kpi .l{font-size:12.5px;color:var(--gray);margin-top:3px}
.kpi.k-total .n{color:var(--ink)} .kpi.k-done .n{color:var(--ok)} .kpi.k-rig .n{color:var(--rig)}
.kpi.k-ready .n{color:var(--warn)} .kpi.k-pend .n{color:var(--pend)} .kpi.k-appr .n{color:var(--ns-blue)}
.progress{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin-bottom:22px}
.pbar{height:14px;border-radius:7px;background:var(--pend-bg);overflow:hidden;display:flex;margin-top:8px}
.pbar div{height:100%}
.plegend{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--gray);margin-top:8px}
.plegend span::before{content:'';display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.lg-done::before{background:var(--ok)} .lg-rig::before{background:var(--ns-blue)} .lg-ready::before{background:#f0b429} .lg-pend::before{background:#c3ccd6}
h2.sec{font-size:18px;margin:26px 0 10px}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
.fbtn{border:1px solid var(--line);background:#fff;border-radius:18px;padding:5px 14px;font-size:13px;cursor:pointer;color:var(--gray)}
.fbtn.on{background:var(--ns-blue);border-color:var(--ns-blue);color:#fff}
#q{margin-left:auto;border:1px solid var(--line);border-radius:18px;padding:6px 14px;font-size:13px;width:200px}
.tblwrap{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:960px}
td a{color:var(--ns-blue-dark);font-weight:600;text-decoration:none}
td a:hover{text-decoration:underline}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--gray);text-align:left;padding:10px 12px;border-bottom:2px solid var(--line);background:#fafcfe;position:sticky;top:0}
td{padding:9px 12px;border-bottom:1px solid var(--line);font-size:13.5px;white-space:nowrap}
tr:last-child td{border-bottom:none}
tr.rig-row{background:var(--rig-bg)}
tr.rig-row td:first-child{border-left:4px solid var(--ns-blue)}
.badge{display:inline-block;border-radius:13px;padding:3px 11px;font-size:12px;font-weight:600}
.b-done{background:var(--ok-bg);color:var(--ok)} .b-rig{background:var(--ns-blue);color:#fff}
.b-ready{background:var(--warn-bg);color:var(--warn)} .b-pend{background:var(--pend-bg);color:var(--pend)}
.fire-ok{color:var(--ok);font-weight:600} .fire-prog{color:var(--warn);font-weight:600} .fire-none{color:#9aa6b3}
.muted{color:#9aa6b3}
.reports{margin:10px 0 40px;display:flex;flex-direction:column;gap:12px}
.rep{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.rep.current{border-left:4px solid var(--ns-blue)}
.rep .rtop{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.rep .rwell{font-weight:700;font-size:15.5px}
.rep .rphase{font-size:12.5px;color:var(--ns-blue-dark);background:var(--ns-blue-light);border-radius:12px;padding:2px 10px;font-weight:600}
.rep .rphase.done{color:var(--ok);background:var(--ok-bg)}
.rep .rdate{margin-left:auto;font-size:12.5px;color:var(--gray)}
.rep .rsum{margin:8px 0 4px;font-size:14px}
details{margin-top:8px}
summary{cursor:pointer;font-size:12.5px;color:var(--ns-blue-dark);font-weight:600}
.rfull{background:#f7fafc;border:1px solid var(--line);border-radius:8px;padding:12px 14px;font-size:12.5px;color:#42505e;margin-top:8px;line-height:1.6;white-space:pre-wrap}
.rsub{font-size:11.5px;color:#9aa6b3;margin-top:6px}
.note{font-size:12.5px;color:var(--gray);background:var(--ns-blue-light);border-radius:8px;padding:10px 14px;margin:8px 0 14px}
.flags{background:var(--warn-bg);border:1px solid #f0c97a;border-radius:10px;padding:12px 16px;margin:20px 0 0}
.flags b{color:var(--warn)}
.flags ul{margin:6px 0 0;padding-left:20px}
.flags li{font-size:13px;margin:3px 0;color:#7a5300}
footer{border-top:1px solid var(--line);background:#fff;margin-top:30px;padding:18px 0;font-size:12.5px;color:var(--gray)}
footer .frow{display:flex;gap:18px;align-items:center;flex-wrap:wrap}
footer img{height:30px;opacity:.9}
@media(max-width:700px){ .hmeta{text-align:left;margin-left:0} #q{width:100%;margin-left:0} }
</style>
</head>
<body>
<header><div class="wrap hrow">
  __LOGO__
  <div class="htitle">
    <h1>Angus Petroleum — Huntington Beach P&amp;A Project</h1>
    <p>Well abandonment progress dashboard &middot; E&amp;B Natural Resources / Angus Petroleum</p>
  </div>
  <div class="hmeta">Last updated<br><b id="built"></b></div>
</div></header>

<div class="wrap">
  <div id="banner"></div>
  <div id="flags"></div>
  <div class="kpis" id="kpis"></div>
  <div class="progress">
    <b style="font-size:14px">Program progress</b> <span id="ptext" style="font-size:13px;color:var(--gray)"></span>
    <div class="pbar" id="pbar"></div>
    <div class="plegend"><span class="lg-done">Abandoned</span><span class="lg-rig">Rig on well</span><span class="lg-ready">Permit approved — awaiting rig</span><span class="lg-pend">Permit under CalGEM review</span></div>
  </div>

  <h2 class="sec">Master Well List</h2>
  <div class="controls" id="filters">
    <button class="fbtn on" data-f="all">All</button>
    <button class="fbtn" data-f="rig">Rig on well</button>
    <button class="fbtn" data-f="complete">Abandoned</button>
    <button class="fbtn" data-f="ready">Ready for rig</button>
    <button class="fbtn" data-f="pending">Permit pending</button>
    <input id="q" placeholder="Search well / API...">
  </div>
  <div class="tblwrap"><table id="tbl">
    <thead><tr>
      <th>Status</th><th>Well</th><th>API</th><th>Permit</th><th>NOI (Permit) Status</th><th>Approved</th>
      <th>Fire Dept</th><th>Rig Start</th><th>Rig Finish</th><th>Rig Days</th>
    </tr></thead><tbody></tbody>
  </table></div>

  <h2 class="sec">Daily Rig Reports</h2>
  <div class="note">Plain-language summaries of the daily reports from the rig contractor (Jorge Macias, EWS Corp). Click
  &ldquo;Full report text&rdquo; on any entry to read the original field report verbatim. A &ldquo;plug&rdquo; is a column of cement pumped
  into the well to permanently seal it; CalGEM is the California state oil &amp; gas regulator that witnesses and approves each step.</div>
  <div class="controls">
    <span style="font-size:13px;color:var(--gray)">Show:</span>
    <select id="repwell" class="fbtn" style="padding:5px 10px"></select>
  </div>
  <div class="reports" id="reports"></div>
</div>

<footer><div class="wrap frow">
  __LOGO_FOOT__
  <div>Numeric Solutions LLC &middot; 1536 Eastman Ave, Suite D, Ventura, CA 93003 &middot; (805) 794-5011 &middot; numericsolutions.com<br>
  Data sources: CalGEM NOI progress workbook (permit pipeline — well list, API, permit, NOI status) and EWS Corp daily rig reports (rig start/finish/days and rig &amp; abandoned status, derived automatically). Internal project tracking — not a regulatory submittal.</div>
</div></footer>

<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const wells = DATA.wells.wells, reports = DATA.reports.reports, meta = DATA.reports.meta;
document.getElementById('built').textContent = DATA.built_iso
  ? new Date(DATA.built_iso).toLocaleString(undefined, {year:'numeric', month:'long', day:'numeric', hour:'numeric', minute:'2-digit', timeZoneName:'short'})
  : DATA.built;

// Data-reconciliation flags (e.g. missing Job Complete, workbook/report conflict)
const flags = DATA.flags || [];
if (flags.length) {
  document.getElementById('flags').innerHTML =
    `<div class="flags"><b>⚠ ${flags.length} item${flags.length>1?'s':''} need review</b>`
    + `<ul>${flags.map(f=>`<li>${f}</li>`).join('')}</ul></div>`;
}

const ORDER = {rig:0, ready:1, pending:2, complete:3};
const LABEL = {complete:'Abandoned', rig:'Rig on well', ready:'Ready — NOI approved', pending:'NOI under review'};
const BCLS = {complete:'b-done', rig:'b-rig', ready:'b-ready', pending:'b-pend'};

// Banner — driven by the reconciled current rig well, and it integrates EVERY
// report filed on the most recent activity date. A single day can carry both a
// prior well's "Job Complete" and a new well's "Day 1" (a rig handoff); the
// banner shows both — the headline reflects who is on the rig now, and the body
// recaps the whole day's work (e.g. "finished A-8i, moved on to A-7i"). The
// reconciled status (not a single newest report) decides the active well, so a
// same-day completion never outranks a new spud.
const byRecency = (a,b)=> b.date.localeCompare(a.date) || b.day-a.day;
const bn = document.getElementById('banner');
// Active well (on the rig now) per the reconciled status.
let active = null;
if (meta.current_rig_well) {
  const r = reports.filter(r=>r.well===meta.current_rig_well).sort(byRecency)[0];
  if (r && !r.job_complete) active = r;
}
// Every report from the latest activity date — completions first, then ongoing work.
const latestDate = reports.reduce((m,r)=> r.date>m?r.date:m, '');
const dayReps = reports.filter(r=>r.date===latestDate)
  .sort((a,b)=> (b.job_complete?1:0)-(a.job_complete?1:0) || a.day-b.day);
const multi = dayReps.length > 1;
const block = r => {
  if (!multi) return `<p>${r.summary}</p>`;
  const done = r.job_complete;
  const tag = done ? `✓ ${r.well_display} — Job Complete (Day ${r.day})`
            : `${(active && r.well===active.well)?'▶ ':''}${r.well_display} — Day ${r.day} · ${r.phase}`;
  return `<div style="margin-top:.6rem"><div style="font-weight:700;font-size:.95em;opacity:.95">${tag}</div><p style="margin:.15rem 0 0">${r.summary}</p></div>`;
};
const body = dayReps.map(block).join('');
if (active) {
  bn.innerHTML = `<div class="banner"><div class="pulse"></div><div>
    <h2>Rig is currently on well ${active.well_display} — Day ${active.day} (${active.phase})</h2>
    ${body}
    <div class="bdate">Latest field report: ${fmtDate(latestDate)}${multi?` &middot; ${dayReps.length} updates`:` &middot; ${dayReps[0].subject}`}</div>
  </div></div>`;
} else if (dayReps.length) {
  const lastDone = dayReps.filter(r=>r.job_complete).slice(-1)[0] || dayReps[0];
  bn.innerHTML = `<div class="banner" style="background:linear-gradient(90deg,#1e8a4c,#27a35d)"><div>
    <h2>No well currently active — last job complete on ${lastDone.well_display}</h2>
    ${body}
    <div class="bdate">Latest field report: ${fmtDate(latestDate)}</div>
  </div></div>`;
}

// KPIs
const count = s => wells.filter(w=>w.status===s).length;
// Total wells whose NOI permit is approved — includes abandoned, on-rig, and
// ready wells (every approved well carries an "0. Approved" NOI status).
const approvedTotal = wells.filter(w=>(w.noi_status||'').trim().startsWith('0')).length;
const kp = [
  ['k-total', wells.length, 'Total wells'],
  ['k-appr', approvedTotal, 'Permits approved (total)'],
  ['k-done', count('complete'), 'Plugged & abandoned'],
  ['k-rig', count('rig'), 'Rig on well now'],
  ['k-ready', count('ready'), 'Approved — awaiting rig'],
  ['k-pend', count('pending'), 'NOI under CalGEM review'],
];
document.getElementById('kpis').innerHTML = kp.map(k=>`<div class="kpi ${k[0]}"><div class="n">${k[1]}</div><div class="l">${k[2]}</div></div>`).join('');

// Progress bar
const tot = wells.length;
const seg = [['complete','var(--ok)'],['rig','var(--ns-blue)'],['ready','#f0b429'],['pending','#c3ccd6']];
document.getElementById('pbar').innerHTML = seg.map(s=>`<div style="width:${count(s[0])/tot*100}%;background:${s[1]}"></div>`).join('');
document.getElementById('ptext').textContent = `${count('complete')} of ${tot} wells abandoned (${Math.round(count('complete')/tot*100)}%)`;

// Table
function fmtDate(d){ if(!d) return ''; const p=d.split('-'); return `${+p[1]}/${+p[2]}/${p[0]}`; }
function fireCell(f){
  if(!f) return '<span class="fire-none">—</span>';
  const c = /submit/i.test(f) ? 'fire-ok' : 'fire-prog';
  return `<span class="${c}">${f}</span>`;
}
const tb = document.querySelector('#tbl tbody');
function render(filter, q){
  const rows = wells.slice().sort((a,b)=> ORDER[a.status]-ORDER[b.status] || a.well.localeCompare(b.well,undefined,{numeric:true}));
  tb.innerHTML = rows.filter(w=>{
    if(filter!=='all' && w.status!==filter) return false;
    if(q && !(w.well+' '+w.api).toLowerCase().includes(q)) return false;
    return true;
  }).map(w=>`<tr class="${w.status==='rig'?'rig-row':''}">
    <td><span class="badge ${BCLS[w.status]}">${LABEL[w.status]}</span></td>
    <td><b>${w.well}</b></td>
    <td>${w.api||''}</td>
    <td>${w.permit_url?`<a href="${w.permit_url}" target="_blank" rel="noopener" title="Open NOI Permit Acceptance Notice (Dropbox)">${w.permit}</a>`:(w.permit||'<span class="muted">&mdash;</span>')}</td>
    <td>${w.noi_status||''}${w.resubmitted?` <span class="muted">(${w.resubmitted.replace('Resubmitted: ','resub. ')})</span>`:''}</td>
    <td>${fmtDate(w.approved)||'<span class="muted">—</span>'}</td>
    <td>${fireCell(w.fire_permit)}</td>
    <td>${fmtDate(w.ops_start)||'<span class="muted">—</span>'}</td>
    <td>${fmtDate(w.ops_end)||'<span class="muted">—</span>'}</td>
    <td>${w.rig_days??'<span class="muted">—</span>'}</td>
  </tr>`).join('');
}
let curF='all', curQ='';
render(curF, curQ);
document.getElementById('filters').addEventListener('click', e=>{
  if(!e.target.dataset.f) return;
  curF = e.target.dataset.f;
  document.querySelectorAll('.fbtn[data-f]').forEach(b=>b.classList.toggle('on', b===e.target));
  render(curF, curQ);
});
document.getElementById('q').addEventListener('input', e=>{ curQ=e.target.value.toLowerCase(); render(curF, curQ); });

// Reports
const repWells = [...new Set(reports.map(r=>r.well_display))];
const sel = document.getElementById('repwell');
sel.innerHTML = `<option value="all">All wells (${reports.length} reports)</option>` + repWells.map(w=>`<option value="${w}">${w}</option>`).join('');
function renderReports(){
  const f = sel.value;
  const list = reports.slice().sort((a,b)=> b.date.localeCompare(a.date) || b.day-a.day)
    .filter(r=> f==='all' || r.well_display===f);
  document.getElementById('reports').innerHTML = list.map(r=>`
   <div class="rep ${(!r.job_complete && meta.current_rig_well && r.well===meta.current_rig_well)?'current':''}">
    <div class="rtop">
      <span class="rwell">${r.well_display} — Day ${r.day}</span>
      <span class="rphase ${r.job_complete?'done':''}">${r.phase}</span>
      <span class="rdate">${fmtDate(r.date)}</span>
    </div>
    <div class="rsum">${r.summary}</div>
    <details><summary>Full report text (verbatim from field)</summary>
      <div class="rfull">${r.full_text}</div>
      <div class="rsub">Source email: &ldquo;${r.subject}&rdquo; — Jorge Macias, EWS Corp</div>
    </details>
   </div>`).join('');
}
renderReports();
sel.addEventListener('change', renderReports);
</script>
</body>
</html>"""

logo_tag = f'<img src="data:image/png;base64,{logo_b64}" alt="Numeric Solutions">' if logo_b64 else '<b style="font-size:18px;color:var(--ns-blue)">Numeric Solutions</b>'
logo_foot = f'<img src="data:image/png;base64,{logo_b64}" alt="Numeric Solutions">' if logo_b64 else ''
html = HTML.replace('__DATA__', payload).replace('__LOGO__', logo_tag).replace('__LOGO_FOOT__', logo_foot)

os.makedirs(os.path.join(D, 'site'), exist_ok=True)
out = os.path.join(D, 'site', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"Wrote {out} ({len(html)//1024} KB)")
