# R1 release request · HOLD (no deploy) · 2026-10-11
Target: existing bohundefence.com, not a new service.
Current candidate base: review branch `r1-unified-site-review-20261011`.
Last R1 QA change before this request: `c92c777048eb8e9a9b3e0283d4531e15ffd4d40e` (bundled JS HOLD scan enforcement).
Exact final release SHA MUST be read from GitHub after CI completes, not assumed from this document.

## Gates and observed evidence
- PASS: R1 review branch exists, source integration/QA gate and no-deploy workflow checked in.
- PASS: deep HOLD scan script is now part of R1 workflow: `scripts/r1-dist-hold-audit.mjs`. It checks compiled JS, JSON, HTML, CSS and maps, not just named JSON files.
- BLOCKED: no independent confirmed success result for workflow at final HEAD from available GitHub connector statuses. HQ reports baseline CI pass; matching SHA/run URL not provided.
- BLOCKED: actual compiled dist is unavailable in this execution environment, so scanner results cannot be certified.
- BLOCKED: original site browser screenshots at current HEAD. Desktop/mobile Playwright script exists but fresh run/screenshots unavailable.
- BLOCKED: Night Side, Austrian companies layer and contact form Chromium interactions at current HEAD. Source/static tests do not replace running browsers.
- BLOCKED: publicly accessible protected preview pending HQ authorization; do not expose QA HOLD.
- BLOCKED: QA08/HQ owner release approval.
- PASS: no production deploy or cloud writes initiated in this task.

## Required Chromium release evidence
- Desktop 1440x900 and mobile iPhone 13 viewport, screenshots feed/map/dossier/navigation.
- MapLibre renders and no JS/page errors. Toggle Night Side on/off and verify layer state.
- Austrian companies count/list and flagged item exclusion, verify real source links.
- Contact form required fields/validation, POST target verified without submitting user data.
- Test empty feed and source-backed dossier interactions. No QA HOLD content in final browser view.
- Run dist HOLD scanner after `npm run build:site` and archive machine-readable JSON report.
- Provide linked GitHub workflow ID, conclusion success, HEAD SHA and artifact SHA-256.

## Release and rollback
Release is disallowed until tests succeed at immutable HEAD, QA08 approves allowed public records, HQ/owner explicitly approves. Preserve current production commit/assets as rollback point and archive its hash; deploy built version only from pinned SHA. On failure, revert Cloudflare Pages to previous deployment, invalidate any newly introduced cache if authorized, verify previous navigation/map/contact, and keep public data unchanged. Rollback must not mutate Supabase.

## Cross-stream
02: verified sources and licenses. 03: company/legal/country mapping. 05: immutable SMART XML hash/amount provenance. 07: regulatory legal status. 08: claim-level public_release approval. HQ01: protected preview and final release permission.
