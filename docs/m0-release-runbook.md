# M0 RELEASE HOLD / CLOUDFLARE EXECUTION RUNBOOK
Last verified: 2026-10-11. No live deploy authorized by this document alone.

## Source, smoke & snapshot evidence
- Release candidate: branch `m0-public-release-review`; `npm ci`, `npm test`, `npm run build`, `npm run build:site`, release Chromium smoke, QA policy fixtures.
- Existing live root HTTP snapshot captured by GitHub Actions with SHA-256 for 11 externally linked resources, including `/`, `/index.html`, `/ukraine-entry.html`, `/europe-entry.html`, `/privacy.html` and main assets; manifest says root_saved=true, account_config_backup_verified=false, rollback_verified=false. Artifact: https://github.com/nab0k/bohun-the-field/actions/runs/38148640828/artifacts/11706320117
- Original root contact form: POST `https://formspree.io/f/xbdwvgqp`; fields `route_intent`, `name`, `organisation`, `email`, `message`; existing email hello@bohundefence.com. Release route matches action and field names, but production submission/notification is NOT YET VERIFIED.
- Never treat a successful preview/browser test as permission to alter DNS.

## BEFORE CUTOVER: OWNER/CLOUDFLARE ACCOUNT ACTION REQUIRED
1. Cloudflare dashboard > Websites > bohundefence.com > DNS > Records > **Export**: retain zone-file with old A/AAAA/CNAME/MX/TXT, proxied statuses, TTLs. Preserve MX/TXT for email.
2. Workers & Pages > identify project serving current root and custom domain; record project name, active deployment ID/URL/commit, production branch, all custom-domain routes, environment variable NAMES (never secret values), build command/output settings, Workers routes and cache rules. Save screenshots / JSON account export where supported.
3. Verify existing site's contact form by a benign test message, check received email in hello@bohundefence.com or Formspree dashboard. Do not claim tested from HTML inspection only.
4. Make the old site restorable independently of old deployment availability: retain artifact snapshot and current Pages build/project/source; restore to a protected preview and verify same HTML hash, static assets, privacy and a successful contact submission. A backup is not an operational rollback until this is tested.
5. Inspect account plan and existing Workers AI neuron usage (Workers & Pages > Workers AI > Usage) before GLM-4.7-Flash test; Free allowance 10,000 neurons/day shared, but capacity and account permissions remain unverified. No paid subscription or quota overage without separate owner approval.

## SAFE PREVIEW / CUTOVER
1. Create candidate preview deployment on existing or dedicated isolated Pages preview project from release branch only after old-site backup is verified. Do NOT bind `bohundefence.com` during preview.
2. Build command: `npm ci && npm run build:site`; output `dist`; no Supabase/Gemini API keys in browser or source. Do not use GitHub Pages workflow as equivalent of Cloudflare.
3. QA at preview: / atlas map loads, About/Services/About Project/Support Project/Contact/Privacy all HTTP 200; Formspree sends and is received; assets not 404; excluded nine AT flags absent in served JSON; unsafe journeys and LIVE off; source QA rule defaults HOLD; cookie/privacy checks; responsive screenshots.
4. Account owner confirms current DNS records and custom domain mapping; only then Cloudflare Pages > Custom domains > attach bohundefence.com and check TLS/DNS. No speculative DNS instructions without live record readback.
5. Immediately post-change: verify `https://bohundefence.com/`, cache status, TLS, expected title and page hash, links, form submission, mobile map, UA safety, no exposed test signals. Abort if critical checks fail.

## VERIFIED ROLLBACK
- Preferred: Cloudflare Pages > affected project > Deployments > select PREVIOUS verified production build > **Rollback** (if project supports that operation) and test same live domain.
- If moving domains between projects: restore saved custom-domain binding and original DNS records, honor propagation, purge only relevant cache, verify restored old root matches prechange SHA-256 `70a11ee1d43ce2bca173d31225442261b6a238a2f4a0c3160456a2db0d0b3e4e`, old privacy route and Formspree form.
- Rollback FAIL until a real preview restore and post-switch readback has been exercised. Snapshot alone is not sufficient.

## NO SHIP CONDITIONS
- No access to Cloudflare live config/DNS, no verified complete backup/rollback, Formspree receipt not tested, Cloudflare actual Workers AI free plan/quota not checked, unreviewed news/claims not eligible for public, owner has not approved risky DNS cutover.
- Supabase remains sole canonical writer under ADR012. D1 is NOT write-master. No DB migration, AI-driven publication or secret changes in this release.

## Source QA risks
- Three real source probes are evidence chain checks; NOT proof of three full Gemini→GLM→QA→Atlas live E2E tests. Model validation remains blocked until account-scoped permissions/free usage verified and an exact claim+excerpt QA review passes.
