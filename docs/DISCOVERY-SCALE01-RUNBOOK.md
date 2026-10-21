# DISCOVERY-SCALE01: source-first autonomous company discovery

Status: staging acceptance **completed** on 2026-10-21; PR remains DRAFT / HOLD for merge and weekly automation.
Target: EXISTING Supabase schema `companies`; no new Companies DB.

Discovery is NOT a legal entity. Exhibitor/member COUNTRY is source/event country,
not registration country or headquarters. Never store it in `company.country`.
Exact name matches only create `candidate_existing`, never confirmed identity.
Ambiguous matches create HOLD and independent match_candidate rows.

Source: https://www.eurosatory.com/en/trends/exhibitors-2024

Read-only source review on 2026-10-21: the official public rendered
EXHIBITOR/COUNTRY table contains 2,015 visible exhibitor rows. This validates
the published-list row-count target only. The exact HTML bytes, deduplicated
adapter output, and supplied historical hash have NOT been independently checked.
A separate **synthetic 2,015-row offline fixture** proves pagination and
idempotent staging at that scale, not the original source acceptance.

Known snapshot SHA-256: `57741139274266eff5c0f1900790f88f42f8b806d7747192603a8ef70ef1bc96`.
For EXACT matching bytes expected 2,015 parsed observations and 2,015 discovery
candidates, zero new company rows, final cursor `done:<hash>`; rerun unchanged
snapshot returns zero fetched and zero new observations. Not independently
validated here against original 2024 snapshot. New site content may differ.

## Procedure requiring separate owner approval
1. Run offline suite: `python -m unittest discover -s tests/companies -t .`.
2. Run dry-run: `python scripts/companies-collect.py --dry-run --sources eurosatory2024 --limit 2500 --raw-dir /tmp/eurosatory-raw --report`.
   SQLite memory only; HTTP access to official HTML but NO Supabase writes.
3. Verify original snapshot hash and applicable website reuse terms, inspect aggregate metrics.
4. Approve then separately execute additive numbered migration:
   `python scripts/companies-collect.py --store supabase --migrate`.
5. Approve then separately stage:
   `python scripts/companies-collect.py --store supabase --sources eurosatory2024 --limit 2500 --raw-dir /tmp/eurosatory-raw --report`.
6. Independently read back observation, discovery_candidate, run and source_state.
   Verify company row count unchanged; no promotion.
7. Approve enabling weekly schedule (the scheduled workflow starts after merge!).

No automatic merge/delete, sanction, publishable or QA changes. No `cards.promote`;
no Notion writes. Only aggregate metrics appear in workflow logs.

## Review hardening
- Eurosatory is a `discovery_only` adapter. A malformed future adapter output
  containing `Mention` is rejected and rolled back before entity resolution.
- HTMLParser preserves structural separators in nested `<br>`, `<p>`,
  `<div>` and list elements inside source cells.
- On an existing `companies` namespace, numbered migrations verify
  core tables from 001 exist before assuming the legacy schema was installed.
  Otherwise they fail closed and do not modify the database.

## Deterministic offline acceptance (no HTTP; no PostgreSQL)
If the exact historical HTML snapshot bytes become available as a local file,
verify the hash **before** parsing or writing any candidate:

```bash
python scripts/companies-collect.py --dry-run --sources eurosatory2024 \
  --snapshot-file /path/to/original-eurosatory-2024.html \
  --expected-sha256 57741139274266eff5c0f1900790f88f42f8b806d7747192603a8ef70ef1bc96 \
  --limit 2500 --report
```

Invalid hash immediately fails closed. This reuses the existing Eurosatory
adapter and pipeline, with an in-memory SQLite store and a fixed byte response
in place of its HTTP port. No second collector or cloud service is created.

Counters: `discoveries` means **all** newly created discovery candidates;
`candidate_existing` and `holds` are subsets. `discovery_by_status`
contains the full status distribution, so this remains accurate even when
the Companies schema already has thousands of company rows.

## Raw HTML volatility and event-pair idempotence

Read-only probes on 2026-10-21 returned different full-page HTML SHA-256
values in two requests despite the same **2,015 unique exhibitor-country
pairs**. This is a source-level volatility risk, not verified evidence
of additions or removals. The specified cursor remains hash-based:
a new full-page SHA starts a **new scan from offset 0**, with
`done:<raw_sha256>` once complete.

For `discovery_only` sources, however, an already observed stable
`source_key = SHA-256(name + country)` is **not reinserted** just because
the WordPress page changed its unrelated HTML. An added exact pair
becomes one new immutable observation and discovery candidate. Old
observations and their original `snapshot_sha256` provenance are preserved;
the raw HTML of the changed snapshot is kept in the optional hash-named
artifact, while `source_state.cursor` records the latest full-page SHA.
No deletions or automatic revocation of earlier leads.

This intentionally separates **fetched (scanned) rows** from **new
observations** and prevents weekly re-ingestion of all 2,015 records
when the page markup changes.


## Verified live-source acceptance (2026-10-21; GitHub Actions, no Supabase)

Read-only Actions [run 39687248851](https://github.com/nab0k/bohun-the-field/actions/runs/39687248851) downloaded the official listing bytes directly and ran the **actual** Eurosatory adapter and existing discovery pipeline over SQLite in-memory:

- Raw downloaded HTML SHA-256: `74088e16c28fdf200b38a29b474ea8f20fef3d9e76adb21666c9e6e3164a8359` (807,489 bytes).
- Exactly **2,015 unique EXHIBITOR / COUNTRY pairs** parsed and staged locally.
- First pass: **2,015 fetched, 2,015 new observations, 2,015 discovery candidates, 0 company rows**.
- Cursor: `done:74088e16c28fdf200b38a29b474ea8f20fef3d9e76adb21666c9e6e3164a8359`.
- Same captured-bytes replay: fetched **0**, new observations **0**.
- Public raw HTML and JSON report archived for 30 days in GitHub Actions artifact `eurosatory-source-readonly-probe`, run 39687248851.

A separate read-only run [39687003270](https://github.com/nab0k/bohun-the-field/actions/runs/39687003270) received SHA-256 `e1e1c143d6b0c889f28ca0ea39dc49c17ccb3f51e53c74512e2f36cbc218c920` (807,462 bytes) but also 2,015 unique pairs.
**Both snapshots differ from the specified historical SHA-256 `57741139274266eff5c0f1900790f88f42f8b806d7747192603a8ef70ef1bc96`.**
Do not rewrite that acceptance target, and do not claim these are byte-identical to the historical snapshot.

On a changed raw-page hash, the adapter rescans from offset zero. The new discovery-only
`source_key` guard prevents duplicate observation/candidate rows for identical
EXHIBITOR/COUNTRY pairs; only materially new exact pairs are inserted.
Offline regression coverage confirms this guard. On the public live page, this
cross-snapshot re-scan was not applied to an existing Supabase source state.

**Operational HOLD remains:** source historical hash discrepancy and no owner-reviewed migration
or Supabase staging readback. A real-source SQLite run is verified; real PostgreSQL is not.


## Verified actual PostgreSQL migration and staging (2026-10-21)

Owner approved the verified live Eurosatory variant and one controlled migration/staging run
in this conversation; original raw HTML SHA acceptance target remains an **independent
historical-byte check**, not silently overwritten.

- Supabase `companies` additive 002 applied successfully; `schema_migration` = [1,2],
  RLS enabled for `discovery_candidate` and migration ledger, no anon/authenticated SELECT.
- Company rows **3,751 before / 3,751 after**. Canonical company-content fingerprint
  `6b3b6ab3af3fc20b0bc10eb6a6068c74` **unchanged**.
- Existing observations **3,518 before**; new Eurosatory observations **2,015**.
  Discovery candidates `2,015`, comprising:
  `discovered=1,807`, `candidate_existing=197`, `hold=11`.
  These are staging classifications, **not** verified identities.
- New company rows created by discovery: **0**; no merge, no promotion, no sanctions,
  publication, QA or Notion changes.
- Exact accepted live HTML SHA-256:
  `74088e16c28fdf200b38a29b474ea8f20fef3d9e76adb21666c9e6e3164a8359`;
  snapshot size **807,489 bytes**. Cursor `done:<this-live-hash>`.
  The original historical SHA-256
  `57741139274266eff5c0f1900790f88f42f8b806d7747192603a8ef70ef1bc96`
  is still **not byte-verified**.
- Immediate unchanged-snapshot replay: **0 fetched**, **0 new observations**,
  **0 company rows created**. Independent Supabase SQL readback confirmed all counts,
  cursor and unchanged company fingerprint.
- [One-time staging GitHub Actions run 39690314548](https://github.com/nab0k/bohun-the-field/actions/runs/39690314548)
  finished SUCCESS. Exact source HTML and aggregate report archived there for 30 days.
- One-time PR label removed and one-time staging job removed from
  `.github/workflows/companies-collect.yml` after success; no continuing
  staging permission. **Scheduled company discovery remains disabled until merge,
  which still requires owner approval.**
