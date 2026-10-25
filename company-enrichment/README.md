# BOHUN Company Enrichment Worker (read-only scaffold)

This is **not** the autonomous enrichment engine yet. It only queries up to 10 Notion pages and returns IDs and property names for schema diagnosis. No data is written to Notion, Supabase, GitHub or Atlas. No Cron and no automatic deployment.

- `GET /health`: public health check.
- `POST /run`: requires `Authorization: Bearer <ADMIN_TOKEN>` (at least 24 characters). Reads Notion data source, never returns secrets or full company values.
- Configure Cloudflare secrets `NOTION_TOKEN`, `NOTION_DATA_SOURCE_ID`, `ADMIN_TOKEN`; optional `MAX_PAGES` (1–10).
- Uses Notion API version `2025-09-03` and data source query endpoint. Confirm actual Companies data source ID before deployment.
- No schedule and `workers_dev = false` to avoid accidental public deployment.

Next: inspect Notion properties and Supabase schema, then add evidence extraction and idempotent candidate storage. QA/public_release remain independent.
