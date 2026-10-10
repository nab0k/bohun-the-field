# Bohundefence source pipeline: execution checkpoints (2026-10-10)

## 1. Inoreader
- Top100 Notion publisher snapshot is in `collectors/inoreader/top100_sources.json`.
- Last uploaded **dry-run** snapshot: 42 total subscriptions, 32 feeds with parsable RSS/Atom entries, 15 matched existing subscriptions, 17 new candidates. This does not prove that any 17 new feeds were subscribed.
- One-time first batch (five selected candidate feeds) is configured in `.github/workflows/inoreader-import-first-batch.yml` with a push-only trigger. Its resulting GitHub Action status and subscribed feeds MUST be confirmed from action logs and API snapshot; never count a configured job as an import.
- Existing manual manager has `check_subscriptions`, `only`, `apply` controls. Select named sources for opt-in write. For bulk import, inspect `added`, `added_needs_verification`, `add_failed`, `assignment_failed`, and subscription list afterwards.
- Remaining sources without RSS should be evaluated for official API, Inoreader Web Feed, or read-only Web Monitor. A site with no RSS is NOT a dead source.

**Blockers:** live write status, real content inspection (publisher, recency, relevance), category mapping to existing BOHUN folders, API quotas and Inoreader Web Feed availability. Do not assume an OAuth refresh token can write just because it can read.

## 2. Web Monitor
- Existing `channels.json` URLs are **seeds, not proven live article feeds**. Monitor remains read-only.
- This PR fixes doubled backslashes in asset/date regular expressions and adds regression tests.
- Next: validate actual listing selectors and publication metadata; expand channel coverage; accept only real articles with published date, original publisher, canonical URL; deduplicate and write verified records to the agreed canonical database after reconciling Supabase vs Cloudflare D1 architecture.

**Blockers:** host robots/timeout restrictions, misleading index pages, inconsistent date metadata. Do not bypass access restrictions or publish unverified records.

## 3. Direct opportunities API
- TED EU workflow already exists and imports structured tenders into Supabase.
- New `collectors/opportunities/grants_us.py` uses publicly documented Grants.gov `search2` API for posted/forecasted opportunities matching defence-adjacent keywords, emits JSON and CSV, daily job.
- EU Funding & Tenders Portal also documents a public API: https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/support/apis. Integration is **not yet implemented**.
- USASpending awards search API is another official source: https://api.usaspending.gov/docs/endpoints. Integration is **not yet implemented**.
- Grants.gov keyword search is only candidate collection; classify relevance, dedup, paginate, map to funding/grant opportunity schema, then add reviewed DB ingest.

**Blockers:** first-run HTTP/API schema validation, pagination and recall, source-specific category filters, stable canonical external IDs, verified DB schema and pipeline.

## 4. Unified registry and delivery status
- New weekly `source-coverage-audit.yml` runs Inoreader dry-run and combines **100 Notion snapshot sources** with additional nonduplicate publisher hosts in the existing **84-candidate CSV**, plus Web Monitor listing seeds. Writes an artifact CSV, not database records.
- Distinguishes `already_subscribed`, `validated_feed`, `site_unreachable`, `not_checked` from unverified Web Monitor seeds. A feed discovered is not a subscription active, and a Web Monitor seed is not a collector in production.
- PR #16 (existing source reconciliation and 247 multilingual keywords) is separate and remained open when this note was authored.

**Blockers:** live Notion synchronization (current data is snapshot), dedup beyond host equality, active/inactive states, provenance/status history, sync to canonical database and website map.

## Done criteria
A source counts as **active** only when latest run confirms a working collection path, normalized items and storage target. A successful GitHub workflow proves the run, not necessarily insertion into Inoreader or the website.
