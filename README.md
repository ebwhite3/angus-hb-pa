# Angus Huntington Beach P&A Dashboard

Live status dashboard for the E&B Natural Resources / Angus Petroleum Huntington Beach well abandonment program. Deployed to Netlify as a static page; rebuilt daily by a Cowork scheduled task.

## Pipeline

1. **`extract_wells.py`** — reads `Master Well List` from the NOI Progress Report workbook
   (`E:\Numeric Solutions Dropbox\Eric White\Angus Petroleum\Engineering\NOI Prep\P&A NOI Progress Report-v2.xlsx`,
   sandbox path `/sessions/<session>/mnt/NOI Prep/...`) → `wells.json`.
   Args: `extract_wells.py <xlsx> wells.json <CURRENT_RIG_WELL>` (normalized name, e.g. `A-8I`).
2. **`rig_reports.json`** — archive of Daily Rig Reports from Jorge Macias (JMacias@ewscorp.net).
   Each entry: well, day, date (work day, PT), phase label, **plain-language summary written by Claude**
   (audience: non-petroleum engineers at E&B and sponsors), and verbatim `full_text`.
   `meta.current_rig_well` records the well the rig crew is on, taken from the *newest spud's* subject
   line. It is **re-derived at build time** from the reconciled rig status (see Banner below), so the
   ingestion value is advisory, not final — when a new well spuds, point it at that well even on the
   same day a prior well reports "Job Complete."
3. **`build_dashboard.py`** — merges both JSONs + `ns-logo.png` (base64-embedded) → `site/index.html`
   (single self-contained file, no external assets). Also **reconciles execution state from the
   rig reports** (see Source of truth) and renders any data-quality flags.
4. **`permits.json`** — `{ "<well_norm>": {"permit": "...", "url": "<Dropbox NOI permit PDF>"} }`.
   Fallback source for the Permit column link when the workbook cell has no hyperlink. Add a row here
   (or hyperlink the Permit # cell in the workbook) when a new NOI is approved.

## Source of truth (two tiers)

The pipeline does **not** trust the workbook for rig execution state — that broke repeatedly when the
manually-maintained columns lagged or carried formula artifacts. Instead:

- **Daily rig reports are authoritative** for: **rig start, rig finish, rig days, and the `rig` / `complete`
  statuses.** `build_dashboard.py` groups reports by well and, for the most recent mobilization:
  rig days = max crew Day-N; rig start = Day-1 date (else workbook fallback if Day 1 isn't on file);
  rig finish = the `job_complete` report date; status = `complete` if a Job Complete exists, else `rig`.
- **The workbook is authoritative** for the permit pipeline (well list, API, permit #, NOI status,
  approval date, fire permit, type, TD) **and** for the pre-rig statuses `ready` (NOI approved, awaiting
  rig) and `pending` (under CalGEM review) — reports can't describe a well no rig has touched yet.
- **Precedence:** reports win where they exist; the workbook is the fallback for wells with no reports.

**Banner (hero) selection.** The top banner is driven by the **reconciled rig status**, never by a
single "newest report." After reconciliation, `build_dashboard.py` re-derives `meta.current_rig_well`
as the well whose latest mobilization has no Job Complete (`status == 'rig'`, sourced from reports); if
several qualify, the one with the most recent report date wins and the others are flagged. When a well
is active the banner shows that well's latest report ("Rig is currently on well …"); when none is
active it shows the most recent Job Complete ("No well currently active …").

> **Why this matters (bug fixed 2026-06-09):** A prior well's "Job Complete" and a new well's "Day 1"
> can share the same calendar date. The old logic picked the single newest report by `max(date, day)`,
> and the day-number tiebreak made the *completed* well (high Day-N) outrank the *new* well (Day 1) —
> so the banner showed "last job complete on A-8i" while the table correctly showed A-7i on the rig.
> Fix: the banner now follows reconciled status, so a new spud always takes precedence over a same-day
> completion. Do **not** reintroduce a global newest-report heuristic for the banner.

**Guardrail flags** (rendered in a banner on the page): a well shows `rig` but the workbook says
P&A-complete (likely a missing Job Complete report), or a well is `rig` with no new report in >3 days.
Re-entries are handled by resetting the day-count block whenever crew Day-N drops.

## Daily refresh task (scheduled in Cowork)

1. Search Gmail: `from:JMacias@ewscorp.net subject:"Daily Rig Report" newer_than:3d`; fetch any thread newer than `meta.last_email_date`.
2. For each new report: write a plain-language summary (explain depths/plugs/CalGEM witnessing in lay terms; note "Job Complete"), append to `rig_reports.json` (newest data in `meta`: `last_email_date`, `last_email_id`, `current_rig_well` from subject line).
3. Run `extract_wells.py` against the current workbook, then `build_dashboard.py`.
4. Deploy `site/` to Cloudflare Pages (see below). Skip deploy if nothing changed.

## Deploy (Cloudflare Pages — direct upload)

**Live URL: https://angus-hb-pa.pages.dev** — moved from Netlify on 2026-06-06 to stop per-deploy
credit charges. Cloudflare Pages direct upload is free with unlimited bandwidth; each dashboard is its
own Pages project and the shared `00_Resources/.cloudflare.json` (`token`, `account_id`) covers all of them.

```bash
cd "Angus Dashboard"
export CLOUDFLARE_API_TOKEN=$(python3 -c "import json;print(json.load(open('.../00_Resources/.cloudflare.json'))['token'])")
export CLOUDFLARE_ACCOUNT_ID=$(python3 -c "import json;print(json.load(open('.../00_Resources/.cloudflare.json'))['account_id'])")
npx --yes wrangler@4 pages deploy site --project-name=angus-hb-pa --branch=production --commit-dirty=true
# Verify
curl -sI "https://angus-hb-pa.pages.dev/" | grep -i content-type   # must be text/html
```

Direct upload pushes the prebuilt `site/` folder — no Netlify/Cloudflare CI build runs, so it doesn't
count against build quotas. `npx` re-downloads Wrangler each run (fresh sandbox); that's expected.

**To revert to Netlify:** see `REVERT-TO-NETLIFY.md`. The Netlify site, `.netlify.json`, and the
file-digest recipe are left intact, so reverting is just swapping this deploy step back.

## Notes

- Well name normalization: `A-8 I` (xlsx) ≡ `A-8i` (emails) → `A-8I` internally. Reports key the
  normalized name as `well`; `wells.json` uses `well_norm`.
- `Rig Days` from the workbook is **no longer used** — rig days now come from the crew Day-N count in the
  reports (sidesteps the negative-value formula artifacts entirely).
- **API leading zero:** both `extract_wells.py` and `build_dashboard.py` zero-pad the 10-digit API
  (`zfill(10)`), so a value stored as a number (`405921419`) is restored to `0405921419`. Excel's
  `0000000000` display format hides the dropped zero, but openpyxl reads the raw number — the zfill makes that moot.
- Status derivation (see Source of truth): reports → `complete` / `rig`; workbook NOI `0. Approved` →
  `ready`; else `pending`.
- Spreadsheet has duplicate variants (conflicted copies, dated copies). **Source of truth: `P&A NOI Progress Report-v2.xlsx`** per Eric, 2026-06-05.
- Eric will add more tracked items later (e.g., fire department signoff is already a column; future: site restoration, etc.).
