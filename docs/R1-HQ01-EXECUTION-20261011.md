# R1 HQ01 execution evidence · 2026-10-11

Scope: existing bohundefence.com Atlas entrypoint, left Intelligence Feed, center MapLibre map, right evidence dossier. No independent AI product.

## Artifacts
- branch: `r1-unified-site-review-20261011`
- R1 CI: `.github/workflows/r1-unified-site-review.yml`
- Desktop/mobile browser smoke: `scripts/r1-browser-smoke.mjs`, screenshots will be at `output/r1-review/browser/{desktop,mobile}.png` inside CI artifact if run succeeds.
- Production bundle gate: `vite.site.config.js`, removing `dist/atlas/pilot-internal-feed.json`.
- Existing persisted SMART LOCAL PASS: Actions run 38177695701. Local SQLite only.

## Verified status (not aspirational)
| Gate | Status | Evidence |
|---|---|---|
| Review branch and file commits | PASS | GitHub file commits |
| Existing website entrypoint and MapLibre code retained | PASS (code inspection) | atlas.html and src/atlas/main.js |
| Left feed/right dossier integrated with QA-approved filter | PASS (code inspection) | src/atlas/m1-state.js |
| HOLD exclusion in build configuration | PASS (code inspection), runtime unverified | vite.site.config.js |
| R1 npm tests/build/browser run | BLOCKED | new CI job results not retrieved |
| Public preview URL | BLOCKED | HQ approval and access-controlled hosting missing; no external preview performed |
| Independent browser access verification | BLOCKED | no authorized preview |
| Supabase cloud persistence A→B | BLOCKED | staging/cloud writes not authorized |
| Public QA-approved Gemini cards | BLOCKED | QA08 approved set empty |
| Production deploy | BLOCKED by instruction | no deployment performed |

## Release candidate boundary
The empty-state/source-only R1 shell is a candidate for QA, not publishable until CI screenshots, regression test, independent QA08 and HQ approval. Never treat existing LOCAL PASS as cloud PASS. Do not copy internal SMART fixture, Gemini HOLD results or raw data into public build.

## Dependencies from other streams
- 02: canonical first-party feed source manifest, fetch timestamp/URL and license.
- 03: validated company IDs, legal identity and geography role; no speculative pin.
- 04: TED notice identity, type, deadline, evidence and QA.
- 05: SMART CORDIS original XML/evidence amount scope, stable project ID.
- 07: primary legal/regulatory item with date, stage and jurisdiction.
- 08: claim-level QA decision APPROVE/public_release and publication rights; presently no approved Gemini cards.
- HQ01/owner: authorize isolated browser-accessible preview with access controls and cloud staging permissions separately.

No production write, deploy, new paid Gemini call or secrets change.
