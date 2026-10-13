# M0 approved intelligence API: implementation gate (PR #53)

Status: **client adapter and CI proven; server endpoint NOT implemented**. Do not deploy this contract as if the backend existed.

## Verified source boundaries

- `src/atlas/main.js` mounts `mountM1Panels` with records returned by `loadApprovedIntelligence`.
- `src/atlas/m1-state.js` requires `qa.decision === 'APPROVE'`, `qa.public_release === true`, HTTPS `source_url`, and `content_hash`.
- `db/schema.sql` is a **historical SQLite/D1 schema**, with `public_entity` filtering `entity.status='published'`. It is **not evidence** of the current Supabase `intelligence` schema or of an approved-view API.
- `scripts/supabase-staging-packet.py` on rescue branch handles `bohun_staging` HOLD-only records, not public rows.
- No `/api/intelligence/approved` Cloudflare Pages Function was found at standard repository paths. Account-level routing has not been inspected.

## Endpoint contract

GET `/api/intelligence/approved` returns `200 application/json`:
`{"records":[{"qa":{"decision":"APPROVE","public_release":true},"content_hash":"<64-hex-sha256>","source_url":"https://publisher.example/item","headline":"...","published_at":"2026-10-13T00:00:00Z","country_iso2":"AT","event_id":"...","company_id":null,"takeaway":"..."}]}`

No records: `{"records":[]}`. Response must be an approved-only server-side projection, not a filter over a publicly readable staging table. Never return raw documents, private addresses, model drafts, secret tokens or HOLD candidates. Geography must be proven and UA-sensitive-location restrictions remain in force.

## Required read-only verification before implementation

1. Inspect the **actual** Supabase `intelligence` tables, columns, RLS, grants and approved publication view with read-only credentials. Identify exact join/provenance chain, content hash and approval authority. No schema migrations.
2. Inspect Cloudflare Pages/Workers routing and existing environment bindings without exposing secrets. Reuse existing endpoint if present.
3. Prove the server query excludes HOLD/REJECT, nonpublic records, and records without immutable source provenance. Include adversarial tests for mixed status, stale approval, wrong source URL, and UA location disclosure.
4. Only then implement Cloudflare read-only endpoint with server-held least-privilege credential and response caps. Do not connect Supabase directly from browser.
5. Validate production-shaped responses, desktop/mobile feed, event→map geography, `npm test`, `npm run build:site`; obtain explicit approval before merge or deployment.

## CI evidence

GitHub Actions run **38359303564** for PR #53 commit `5026e0c`: `npm ci`, `node tests/test-approved-feed.mjs`, `npm test`, `npm run build:site` all **success**. This validates build and adapter only; it does not establish API availability, real data, or browser UX.
