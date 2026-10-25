# Supabase integration contract (verified read-only, 2026-10-25)

Project: `bohundefence-intelligence` (ref `tecrucawzukxqtchpxrh`, region `eu-central-1`).

**Do not create duplicate `public.company` tables.** The operational database already contains schemas `companies`, `bohun_staging`, `bohun_private`, `intelligence`, `exp009` and others. The `public` schema having zero tables does **not** mean the database is empty.

Existing entities and their integration role:

| Table | Relevant columns | Role |
| --- | --- | --- |
| `companies.company` | `id` text, `notion_page_id` text, `name`, `country`, `status` | Canonical company identity |
| `companies.g4_notion_crosswalk` | `notion_page_id` uuid, `company_id` text, `candidate_id` bigint, `match_status`, `notion_fingerprint` | Stable identity and matching |
| `companies.notion_company_cache` | `notion_page_id` uuid, `payload` jsonb, `content_hash`, `last_seen_run`, `active` | Source snapshot/cache |
| `companies.notion_sync_run` | `run_id` uuid, `source_count`, `content_hash`, `status` | Run receipt |
| `companies.profile_research_case` | `candidate_id`, `research_payload`, `content_sha256`, `qa_status`, `public_release` | Research candidate |
| `companies.profile_claim` | `company_id`, `claim_ref`, `field_path`, `claim_value`, `assessment`, `qa_status`, `public_release` | Structured claim |
| `companies.profile_source` | `company_id`, `source_ref`, `source_url`, `snapshot_sha256`, `original_bytes_archived` | Source provenance |
| `companies.g4_section_review` | `company_id`, `section_key`, `qa_status`, `public_release` | Section release gate |

## Invariants
1. **Never** derive canonical company ID from row order or company name alone. Resolve through `g4_notion_crosswalk` and `companies.company`.
2. Ingestion is not publication. Default every new claim/research case to pending QA and `public_release=false`.
3. Do not write to Supabase or Notion until column constraints, foreign keys, existing functions/triggers, grants and write ownership have been reviewed.
4. Worker `/run` currently performs **Notion-only read-only diagnosis**. Supabase integration is not yet implemented.
5. Use Cloudflare Secrets for credentials. Never commit secrets, tokens or raw sensitive source dumps.
6. The company map uses approximate city centres; do not add facility coordinates. Apply existing Ukraine safety restrictions.
7. The next milestone is a limited dry-run matching 10 Notion page IDs against existing crosswalk rows, with no writes.

## Deployment blockers
- Cloudflare connector not available in this chat; no Worker has been deployed.
- Verify Cloudflare account/zone and set secrets out of band.
- Verify Notion API integration access and the data-source identifier.
- Verify actual DB grants and API exposure before choosing Supabase REST vs private RPC.

This contract records read-only observations; it is **not** a migration or permission to write.
