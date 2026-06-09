# Revert: Cloudflare Pages → Netlify

The dashboard was hosted on Netlify until 2026-06-06, then moved to Cloudflare Pages
(to stop per-deploy credit charges). **Nothing about Netlify was deleted** — the site,
the `.netlify.json` credentials, and this deploy recipe are all still intact, so reverting
is just swapping the deploy step back.

## To go back to Netlify

1. In the scheduled task `angus-pa-dashboard-refresh`, replace the **Cloudflare deploy**
   step with the **Netlify file-digest** block below.
2. Update `README.md`'s Deploy section back to Netlify (or just keep both documented).
3. (Optional) Pause/leave the Cloudflare project — no need to delete it.

The Netlify site, token, and site_id are unchanged:
- URL: https://angus-hb-pa-4pfspmlu.netlify.app
- Credentials: `00_Resources/.netlify.json` (`token`, `sites.angus-hb-pa.site_id` = `0bfd2b38-b9d1-4c16-9364-b3c06d7dedef`)

## Netlify deploy recipe (file-digest method — the original, verified)

> Do NOT use the Netlify zip upload endpoint — it stores the archive as a single
> `text/plain` file at "/" and breaks the page (discovered 2026-06-05).

```bash
cd "Angus Dashboard/site"
TOKEN=$(python3 -c "import json;print(json.load(open('.../00_Resources/.netlify.json'))['token'])")
SITE_ID=0bfd2b38-b9d1-4c16-9364-b3c06d7dedef
SHA=$(sha1sum index.html | cut -d' ' -f1)
# 1. Create deploy with file manifest
DID=$(curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"files\":{\"/index.html\":\"$SHA\"}}" \
  "https://api.netlify.com/api/v1/sites/$SITE_ID/deploys" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
# 2. Upload the file
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" \
  --data-binary @index.html -X PUT "https://api.netlify.com/api/v1/deploys/$DID/files/index.html"
# 3. Verify
curl -sI "https://angus-hb-pa-4pfspmlu.netlify.app/" | grep -i content-type   # must be text/html
```

Everything else in the pipeline (extract_wells.py, build_dashboard.py, the reconcile logic,
skip-if-unchanged) is host-agnostic and stays exactly the same either way.
