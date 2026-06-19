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
3. Ingest NOI approvals posted by Luc in Slack `#angus_managers` (see **Slack approvals** below) → `slack_approvals.json` (+ permit link into `permits.json`), and react ✅ to each ingested message.
4. Run `extract_wells.py` against the canonical workbook (see **Source workbook** below), then `build_dashboard.py`.
5. Deploy `site/` to Cloudflare Pages (see below). Skip deploy only if **nothing** changed — no new rig emails, **no new Slack approvals**, and the spreadsheet content is unchanged.

### Source workbook (canonical — always read this)

Per Eric (2026-06-15), the scheduled task must **always read the canonical file**:
`C:\Users\Admin\Numeric Solutions Dropbox\Eric White\Angus Petroleum\Engineering\NOI Prep\P&A NOI Progress Report-v2.xlsx`
(sandbox: `/sessions/<session>/mnt/NOI Prep/P&A NOI Progress Report-v2.xlsx`). **Do not** substitute a
dated copy, a "conflicted copy," or the fallback copy that may sit in this `Angus Dashboard/` folder.

This file is a Dropbox cloud-synced binary and the sandbox mount sometimes serves it **truncated**
(valid header, but `openpyxl` raises `BadZipFile` / no end-of-central-directory `PK\x05\x06`). Before
running `extract_wells.py`, **validate and retry**:

1. Open the workbook with `zipfile.ZipFile(path)` (or check the file ends with `PK\x05\x06`).
2. If it fails, the mount has a partial copy — `sleep 15` and re-check, up to ~4 times (a stale
   read can take a minute to hydrate; a parallel `Read` of the file by the host helps trigger it).
3. If it validates, run the pipeline normally.
4. If it still fails after retries, **do not substitute another copy** and do not run `extract_wells.py`.
   Instead **always rebuild from the last-good `wells.json`** already on disk (skip `extract` only) by
   running `build_dashboard.py`, then apply the **normal sha1 skip-check** (step 4 of the daily refresh):
   diff the freshly built `site/index.html` against the currently-published page and **deploy if they
   differ**. This is critical — the rig reports and Slack approvals are merged at *build* time from their
   own JSON files, so a truncated workbook must **never** block publishing rig-execution or approval
   updates. It also self-heals the case where a prior run ingested a report or approval into JSON but
   never deployed it (e.g. the workbook was truncated that day too): the sha1 will differ and this run
   publishes the backlog. Note in the run output that the **permit pipeline was not refreshed from the
   workbook** (well list, NOI statuses, approval dates reflect the last good extract) so Eric can force a
   Dropbox re-sync. **True fail-safe (no deploy)** only when the page can't be built at all — no last-good
   `wells.json` on disk, or `build_dashboard.py` errors — in which case leave the live site as-is and
   report "dashboard unbuildable — refresh skipped." (A full re-sync, or a save to a new path, hydrates the file.)

   > **Bug fixed 2026-06-17:** the old rule skipped the deploy entirely unless *new Slack approvals*
   > arrived *that run*. A-7i Day 7 was ingested into `rig_reports.json` by an evening run that never
   > deployed (its last deploy predated the email), then the next morning's run found the workbook
   > truncated, saw no new approvals, and fail-safed — leaving the live page stuck on Day 6 with Day 7
   > sitting unpublished in JSON. Fix: on a truncated workbook, always rebuild from last-good `wells.json`
   > and let the sha1 skip-check decide; only a genuine build failure suppresses the deploy.

## Slack approvals (#angus_managers)

Luc Landry posts NOI approvals to Slack `#angus_managers` (channel `C092D4XT5QD`, Luc = `U019FH7PXDF`)
as they come in, giving the **well, permit number, and a Dropbox link to the permit**. This is a second,
workbook-independent path to capture approvals — **authoritative for "approved" status and the permit
link** (Eric, 2026-06-15), so an approval shows on the dashboard even when the workbook read is lagging.

Each run:

1. Read `#angus_managers` for messages from Luc newer than `slack_approvals.json` → `meta.last_checked_ts`
   (first run: scan the last ~7 days). Use `slack_read_channel` / `slack_search_public_and_private`
   (`in:#angus_managers from:<@U019FH7PXDF>`).
2. For each message that signals an approval, parse:
   - **well** — token like `A-8i`, `B-11`, `B-16i`; normalize the same way as the pipeline
     (`A-8 I`/`A-8i` → `A-8I`).
   - **permit #** — the 7-digit number (e.g. `7056326`).
   - **Dropbox URL** — the `https://www.dropbox.com/...` link.
   A message is only actionable when it carries a well **and** (permit # or Dropbox link). If a message
   is ambiguous or missing pieces, skip it (do not guess) and note it in the run output.
3. Append to `slack_approvals.json` under `approvals` keyed by normalized well:
   `{"well_display","approved_date" (Slack msg date, Pacific),"permit","url","source_ts","acked":true}`.
   Also add/refresh the same well in `permits.json` (`{"permit","url"}`) so the permit column links even
   on a workbook-only rebuild. Update `meta.last_checked_ts` to the newest message ts processed.
4. React ✅ (`slack_add_reaction`, emoji `white_check_mark`) to each ingested message so the team sees it
   was captured. Do not post chat replies. Adding a duplicate reaction is harmless (idempotent).
5. `build_dashboard.py` reads `slack_approvals.json` and applies each approval as an override: a well not
   already `0. Approved` is lifted to `0. Approved` / status `ready` (tagged `noi_source:"slack"`), with
   `approved` date and permit link filled if missing. Rig reports still own `rig`/`complete` status, and a
   later workbook `0. Approved` simply matches (no conflict). Approvals therefore feed the header
   **"Permits approved (total)"** KPI automatically.

Idempotency: a well already in `slack_approvals.json` (or already approved in the workbook) is a no-op —
re-ingesting the same message changes nothing and the ✅ is already set.

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
