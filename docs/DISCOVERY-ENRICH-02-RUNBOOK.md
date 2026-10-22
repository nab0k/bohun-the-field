# DISCOVERY-ENRICH-02: Private evidence review

Status: CODE REVIEW ONLY. This is an extension of the existing EXP-012 collector's enrichment pilot.
Base branch: feature/discovery-enrich-01 (PR #79), itself stacked on PR #78.
No Supabase migration or writes, no Notion writes, no merge, and no website publishing.

## Motivation from the 20-case research report, 22 October 2026

The owner-provided report identified:
- Direct Action: brand identity must not be conflated with a legal company (Entire M).
- Combitech: a group with Sweden/Finland legal entities; the reported exhibition country is NOT incorporation jurisdiction.
- BAE, Diehl and Forcit: the exhibition label can identify a group, brand or local entity.
- Delfox: reports of June 2026 liquidation need checking against original primary legal notices before accepting the status.
- Prior five GLEIF calls produced only aggregates, and the ephemeral per-candidate file was deleted. Prior specific LEI leads are not recoverable from that data. Do not reconstruct them speculatively.

## Implemented

1. Frozen 20-case review plan, stored in the existing collector data folder. Every URL is flagged as unfetched_hint, not verified evidence. Names and registry identifiers are not written to public CI logs.
2. The same existing enrich_pilot CLI gets a --review20 mode that prepares 20 private packets from the frozen 81 candidates. Every packet starts with:
   identity_kind=unknown; lifecycle_status=unknown; review_status=unreviewed;
   exhibitor_to_legal_entity_verified=false; company_write_allowed=false; publishable=false.
3. Optional --captured-evidence-manifest records locally captured UTF-8 source text, an exact matching quote of 8 to 500 characters, HTTPS URL previously present in hints, capture SHA-256, quote SHA-256, UTC capture time, source-as-of date, research-only identity/lifecycle HYPOTHESES.
4. The locally stored evidence_chain.jsonl is append-only through application code, with ordered SHA-256 hash chaining, integrity verification before new writes, 0600 permissions and idempotent repeated receipt recording.
5. No full source body is stored in the ledger; only exact excerpts and hashes. PDF binary content is not accepted as UTF-8 evidence. It needs an explicitly designed trusted text-extraction step, not made-up quotes.
6. Existing 81-row Supabase SELECT remains READ ONLY. No changes to company, discovery_candidate, aliases, HOLD, QA, sanctions, publishing or site content.

## Reproducible commands

Offline tests:

    python -m unittest discover -s tests/companies -t . -v
    python -m py_compile collectors/companies/enrich_pilot.py collectors/companies/evidence_review.py

Prepare 20 link-only packets on an OWNER-APPROVED PERSISTENT ENCRYPTED filesystem, given a permitted read-only Supabase connection:

    python -m collectors.companies.enrich_pilot --read-supabase --review20 --private-output-dir /ABSOLUTE/APPROVED/PRIVATE-BOHUN-EVIDENCE

For locally captured sources, create an ABSOLUTE private JSON capture manifest with up to 20 items; each item has fields:
- candidate_id: integer (must be one of the 20 cases)
- source_url: exact HTTPS URL from that candidate's hint
- snapshot_file: absolute local source-text path outside Git
- exact_quote: verbatim UTF-8 text from that file, between 8 and 500 characters
- identity_kind: unknown/brand/corporate_group/subsidiary/legal_entity (RESEARCH HYPOTHESIS)
- lifecycle: unknown/active/insolvency/liquidation/dissolved (RESEARCH HYPOTHESIS)
- source_as_of: optional YYYY-MM-DD

Append captured receipts using:

    python -m collectors.companies.enrich_pilot --read-supabase --review20 --private-output-dir /ABSOLUTE/APPROVED/PRIVATE-BOHUN-EVIDENCE --captured-evidence-manifest /ABSOLUTE/PRIVATE/captures.json

The manifest and source files must remain outside Git. This command does NO external HTTP by default and never writes company/Notion/Supabase data. Do not launch it on an ephemeral GitHub runner as the sole evidence archive.

## Security and QA limitations

- Filesystem permissions 0700 directories / 0600 files are not encryption.
- SHA chaining detects accidental edits; an attacker able to rewrite the entire chain can forge it. There is no independently held signing key, WORM storage or audit server in this pilot.
- Files are written only to an absolute path outside the Git checkout. A single local writer is supported. Owner must approve an encrypted durable volume, backup/retention plan and access rights before operational retention.
- Existing source hints are not fetched or verified by these offline tests. Real page capture and genuine legal registry verification are separate tasks.
- No automatic company creation, merge, brand/group linkage, legal jurisdiction mapping or lifecycle approval.
- A business website remaining online does NOT establish active legal status. A single source quote does NOT prove identity between the exhibitor and a legal company.
- Product acceptance requires separately reviewing each primary source and recording independent corporate/registry evidence. Human QA must remain distinct from evidence collection.

## Next independent acceptance

Run full CI offline tests and read-only review20 pilot, verify exactly 20 private packets and zero auto-confirmations. Then independently capture 20 primary-source documents and assess how many have verifiable entity identifiers. Do not claim 20 identities verified based solely on URL coverage.

## Regression hardening: multiple research runs

The previous EXP-012 private writer used research.jsonl with write-mode and
could overwrite a prior registry research pass. It now preserves first-run
legacy filenames and writes different later results to immutable,
content-addressed research-SHA256.jsonl and aggregate-SHA256.json files.
Identical replay is a no-op. This only protects files on a persistent volume;
CI runner temporary files still disappear. Hashes are not encryption.
