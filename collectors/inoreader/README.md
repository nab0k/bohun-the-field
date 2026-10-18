# Inoreader BOHUN folder sync and bounded content capture

**INPUT-02 status: committed locally as `aba17ec`, not deployed.** The existing
metadata collector has written items to Supabase; the content extension described
below has only been checked offline. Its release and a bounded cloud run require
separate approval. Live API text coverage and completeness are not established.

- Automatically discovers subscriptions and their `BOHUN_*` folders on each run.
- Reads up to 200 most recent items per folder (configurable), deduplicates by Inoreader item ID, and inserts metadata into existing Supabase `intelligence.sources` and `intelligence.items`.
- Preserves headline, URL, publication timestamp, feed origin and folder. New
  records additionally carry bounded provider content in `raw_data.content_capture`.
- Safe to rerun: `ON CONFLICT (source_id, external_id) DO NOTHING`.
- Runs every 30 minutes in GitHub Actions. Actual execution time may vary. Not a real-time feed.
- A new source is picked up if assigned to a `BOHUN_*` folder; unassigned subscriptions are ignored.
- Required GitHub Actions secrets: `INOREADER_CLIENT_ID`, `INOREADER_CLIENT_SECRET`, `INOREADER_REFRESH_TOKEN`, `SUPABASE_DB_PASSWORD`.
- Inoreader OAuth refresh token is never logged. If provider rotates refresh tokens, manually update the secret; future version should automate secure rotation.
- Limitations: this is a rolling recent-items window, **not guaranteed full historical backfill**; multiple folders for the same item retain the first folder only; deleted subscriptions are not deleted from historical DB. API quotas and schema compatibility must be verified in first run.
- The current EXP-009 plan uses the existing Supabase ingestion database. Historical
  D1 guidance in older repository documents is not evidence of today's canonical
  deployment. This input change does not connect the analytical pipeline or website.

## Content capture contract, version 1

Only `summary.content`, when returned as an HTML string, is captured. The input
HTML is preserved exactly and gets its own SHA256 over UTF-8 bytes; extracted text
has a separate hash and extraction version. `captured_at` is an aware UTC receipt
timestamp, separate from the publication date. Original feed/publisher labels stay
in their existing origin fields; their presence is not identity verification.

The HTML parser removes script/style/noscript/svg text without executing markup.
It does not evaluate CSS or establish that an excerpt is a complete article.
`completeness` always remains `UNKNOWN`. Capture grants no QA, evidence or public
release approval. The HTML is untrusted data and must not be rendered as active HTML.

| Status | Meaning |
| --- | --- |
| `METADATA_ONLY` | No nonblank provider body; titles are not promoted to article text |
| `BODY_PRESENT_COMPLETENESS_UNVERIFIED` | Bounded text exists; completeness and rights still need checking |
| `EMPTY_TEXT` | HTML exists but yields no extracted text |
| `UNSUPPORTED_SHAPE` | Unexpected field type or invalid UTF-8; no coerced body |
| `TOO_LARGE` | Per-message HTML or extracted-text limit exceeded |
| `CAPTURE_BUDGET_EXHAUSTED` | Body omitted to keep the complete run's capture JSON within budget |

Limits: **2 MiB HTML / message, 128 KiB extracted text / message, 5 MiB total
capture JSON / run**, measured in UTF-8 bytes (including JSON escaping). Oversized
bodies are not silently truncated. To retain room for later status records, the
latest retained bodies may be removed and explicitly marked before database import.
If even bodyless capture metadata cannot fit, the run fails with
`CAPTURE_METADATA_BUDGET_EXHAUSTED` before any database write. Input objects are not
modified. These limits do not cap the existing client's HTTP response download.

Only the first occurrence of each item ID is normalized and charged to the capture
budget. The existing insert and `ON CONFLICT DO NOTHING` are unchanged: this is
**new-record capture, not a backfill**. Re-reading a previously stored metadata-only
item will not enrich it. Updating old records needs a separate versioning/backfill
plan. No schema change or new table is introduced.

Logs show aggregate capture statuses and JSON bytes, never raw HTML, extracted text,
or OAuth credentials. The existing public wire's title/URL/date allowlist is not
expanded. No workflow, database permissions or publication gate is changed here.

## Offline checks

From the repository root, using the installed Python 3.13 and no new dependencies:

```sh
/opt/homebrew/bin/python3.13 -B -m unittest discover -s tests -p 'test_inoreader_content.py'
/opt/homebrew/bin/python3.13 -B -m unittest discover -s tests -p 'test_inoreader_rate_limit.py'
/opt/homebrew/bin/python3.13 -B -m unittest discover -s tests -p 'test_inoreader_source_manager.py'
```

The content tests mock OAuth/API/database boundaries, including dry-run. **Running
the collector itself with `--dry-run` is not an offline check:** it still refreshes
OAuth and reads Inoreader. Do not combine it with the commands above.

## Bounded acceptance probe for the existing INPUT-02 patch

`probe.py` reuses `sync.normalize` and the existing `CaptureBudget` unchanged.
It is a test runner around the committed capture patch, not a second text extractor.
The probe/workflow changes are local and uncommitted until separately reviewed.
No live probe has run. Running preview also needs approval: it refreshes OAuth,
reads Inoreader and performs database SELECTs; it is not an offline check.

- One subscription list, at most three alphabetically sorted BOHUN folders, one
  page of 20 items each, no continuation: at most 60 input positions.
- Select at most five genuinely new external IDs, preferring provider body strings;
  normalize only this batch. Existing items are not enriched or overwritten.
- Require exactly one existing `Inoreader BOHUN` source. Never create a source.
- Preview is read-only and prints only counts, statuses, bytes and fingerprints.
- Apply requires a reviewed full commit SHA, catalog fingerprint and batch
  fingerprint from preview. Batch identity excludes receipt time, but binds
  provider text/hashes/metadata; changed candidates abort instead of widening scope.
- Catalog guards require RLS, no public-role schema/table/raw-column SELECT access,
  one source-wire function and no enabled custom item triggers. Catalog/defaults/
  function text must also be reviewed separately before approving its fingerprint;
  a matching hash is not an automated proof of safe SQL or publication defaults.
- One transaction inserts at most five new rows with `ON CONFLICT DO NOTHING`,
  compares the stored batch, repeats the same inserted keys and verifies no change.
  Mismatch before commit rolls back. A separate read-only connection checks after
  commit. Post-commit failure is UNCONFIRMED and must not trigger automatic retry.
- No new body-bearing rows means INCONCLUSIVE (exit 2), even if the job stored
  metadata. It does not authorize backfill, more folders or more rows.
- Original HTTP download is still unbounded; these are capture/row limits. Existing
  bounded 429 retries remain. The workflow has a ten-minute timeout.

Offline probe tests, in addition to the three regression commands above:

```sh
/opt/homebrew/bin/python3.13 -B -m unittest discover -s tests -p 'test_inoreader_probe.py'
```

The feature copy of `inoreader-cloud.yml` is manual-only, has no schedule/push
trigger, and refuses refs other than `codex/exp009-cloud-pipeline`. It shares the
normal collector's concurrency group, defaults to preview and checks the reviewed
SHA before credentials. The workflow already exists on main; a future separately
approved feature-ref dispatch can test this copy without merging it into main.
The main collector/schedule has not been changed by local edits. Do not merge this
temporary probe workflow into main as a production replacement.

Before any cloud run: review the exact diff, commit and separately approve push,
the exact feature ref/SHA and dispatch. Before apply: inspect current catalog/
defaults/triggers/grants and the proposed batch; approve the exact fingerprints and
up to five inserts separately. Do not print raw payloads/credentials into Actions
logs or artifacts. Public source-wire headlines/URLs/dates may include the new rows
under its existing contract; its content need not remain identical. Its body access
and QA/public-release rules must not expand. No source-wire function is executed by
the probe: catalog definitions/permissions are compared before and after writing.

Only after a verified new body, conflict/replay and post-commit readback can this
input component be considered tested in cloud. That still does not connect the
analytical queue, models, Notion editor or automatic three-stream publication.

## Next work
- Incremental watermark and catch-up pagination.
- Per-feed registry and source health checks.
- Gemini classification with evidence checks and editorial publication gates.
- Reconcile target canonical DB with `docs/DECISIONS.md`.
