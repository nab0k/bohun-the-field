# DISCOVERY-SCALE01: source-first autonomous company discovery

Status: REVIEW ONLY. Migration not run, workflow not launched, staging not authorized.
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
