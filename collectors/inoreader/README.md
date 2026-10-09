# Inoreader BOHUN folder sync v1

**Status: proposed PR, NOT deployed or tested against live Inoreader.**

- Automatically discovers subscriptions and their `BOHUN_*` folders on each run.
- Reads up to 200 most recent items per folder (configurable), deduplicates by Inoreader item ID, and inserts metadata into existing Supabase `intelligence.sources` and `intelligence.items`.
- Stores headline, URL, publication timestamp, feed origin and folder. **Does not store article content or summaries**.
- Safe to rerun: `ON CONFLICT (source_id, external_id) DO NOTHING`.
- Runs every 30 minutes in GitHub Actions. Actual execution time may vary. Not a real-time feed.
- A new source is picked up if assigned to a `BOHUN_*` folder; unassigned subscriptions are ignored.
- Required GitHub Actions secrets: `INOREADER_CLIENT_ID`, `INOREADER_CLIENT_SECRET`, `INOREADER_REFRESH_TOKEN`, `SUPABASE_DB_PASSWORD`.
- Inoreader OAuth refresh token is never logged. If provider rotates refresh tokens, manually update the secret; future version should automate secure rotation.
- Limitations: this is a rolling recent-items window, **not guaranteed full historical backfill**; multiple folders for the same item retain the first folder only; deleted subscriptions are not deleted from historical DB. API quotas and schema compatibility must be verified in first run.
- **Architecture conflict:** `docs/DECISIONS.md` specifies Cloudflare D1 as canonical website DB, while the proven TED pipeline currently uses Supabase. This collector deliberately targets the *existing working ingestion DB* to avoid inventing D1 credentials or schema. Do not claim that the website is wired. Resolve D1 vs Supabase through an explicit architecture decision and migration/replication plan before publication.

## First deployment
1. Review PR and ensure the secrets above exist in GitHub repository Actions secrets. Never paste their values into chat.
2. Merge PR, run workflow manually, inspect `DISCOVERED`, `VALIDATED`, `COMMITTED` lines.
3. Run a second time to verify `new=0` when no new items have arrived.
4. Confirm source metadata in Supabase, check folder naming and quotas.
5. Only then mark this component as operational in Notion.

## Next work
- Incremental watermark and catch-up pagination.
- Per-feed registry and source health checks.
- Gemini classification with evidence checks and editorial publication gates.
- Reconcile target canonical DB with `docs/DECISIONS.md`.
