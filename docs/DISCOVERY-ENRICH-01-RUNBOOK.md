# DISCOVERY-ENRICH-01: read-only legal-identity research pilot

**Status: CODE REVIEW / NO PROMOTION.** Built on EXP-012, after DISCOVERY-SCALE01.
Separate research component inside the current collector, NOT a second collector.
No new database, migrations, Notion, publishing, sanctions actions, merges or QA changes.

## Source and fixed cohort

From already staged Eurosatory 2024 source (not legal entities): 81 frozen candidate IDs,
comprising 50 discovered, 20 candidate_existing, and all 11 HOLD cases.
These 11 HOLD items represent 25 pre-existing match-candidate alternatives.
The manifest in `collectors/companies/data/discovery_enrich01_cohort.json`
contains only IDs and checksums. SQL readback must match the exact manifest; no
silent selection replacement.

Initial live read-only Supabase audit: of the 20 existing candidates,
14 have a website, 3 have at least one registry ID, 20 have a stored signal.
None establishes that a specific Eurosatory exhibitor is that legal entity.

## Commands (only with owner-approved connected environment)

```sh
python -m unittest discover -s tests/companies -t . -v
python -m collectors.companies.enrich_pilot --explain
python -m collectors.companies.enrich_pilot --read-supabase \
  --private-output-dir /tmp/bohun-enrich01-private
# Optional 5 free external GLEIF API calls, stratified 2 discovered, 2 potential, 1 HOLD:
python -m collectors.companies.enrich_pilot --read-supabase --gleif --max-gleif 5 \
  --private-output-dir /tmp/bohun-enrich01-gleif-5
```

The SQL connection starts an explicit PostgreSQL READ ONLY transaction before
the SELECT query; no database changes. The output path MUST be absolute and
outside the repository checkout, with restricted directory/file permissions.
Do not upload detailed research.jsonl to public GitHub CI/artifacts; print only
aggregate totals. Secrets remain in GitHub/environment secrets, never source.
Do not run 81 external requests without separately approving expanded scope.

## Critical boundaries

- GLEIF search uses the existing Http/Gleif adapter, with paginated exact-normalized-name
  checking and an explicit "incomplete" state on truncation. No name-only match is
  authoritative, even if the registry returned exactly one LEI.
- Source/event COUNTRY **never** becomes registration country or HQ. Registry's
  jurisdiction may be included as an attributed **registry lead**, not a company fact.
- A missing LEI is NOT proof of absence. EU company registries and websites are not
  automatically researched in this pilot. Defence relevance is not inferred from
  participating in a defence exhibition alone.
- 11 HOLD remain HOLD. No create_company, resolve.resolve, merge, cards.promote,
  QA APPROVE, publication or automatic entity resolution.
- A candidate registry lead is neither verified nor ready for publication.
- To verify correctness, compare private evidence with primary legal registry and
  other independent sources; require human QA for any promotion.

## Deliverable

Aggregated counts across 81 rows, first 5 read-only GLEIF lookup outcomes,
clear list of ambiguous/error/no-LEI results in a private, temporary file,
and offline regression tests. If fewer than expected candidates are present,
fail closed rather than guessing.


## Actual read-only pilot results (2026-10-22)

The actual connected Supabase candidate cohort was read with an explicit
`SET TRANSACTION READ ONLY` (no DML/DDL). A bounded GLEIF smoke pass
made **five** free public registry queries, stratified across
2 discovered, 2 candidate_existing, and 1 HOLD case.

[GitHub Actions run 39701649195](https://github.com/nab0k/bohun-the-field/actions/runs/39701649195)
finished SUCCESS, after passing the full EXP-012 offline suite.

Aggregate-only observed outputs:
- **81** source-backed candidates read successfully, with unchanged group counts
  (50 discovered / 20 candidate_existing / 11 HOLD).
- Registry: **2** `single_exact_lei_candidate` provisional leads,
  **2** `no_exact_registry_lead`,
  **1** `registry_search_incomplete`,
  **76** not queried.
- **0** verified legal identities, **0** new companies,
  **0** publications, **0** source-country legal inferences.
- No research.jsonl, company names, registry LEIs or candidate IDs were
  uploaded as GitHub Actions artifacts. Private evidence was ephemeral
  on the runner and is **not persisted** outside its lifetime.
- The one-time GitHub Actions research job was removed from the branch
  immediately after the success; the branch contains normal offline CI only.

The results demonstrate that the code reads the existing 81-row Supabase
cohort and conducts bounded GLEIF lookups. They do **not** prove that
the 2 single-name registry leads are identical to exhibition participants,
nor do they establish that the other 76 have been enriched.

### Next quality gate

Use independent legal registration numbers and official websites to
verify identity in an owner-reviewed private evidence set, then assess
whether broader bounded GLEIF research across all 81 is cost-effective.
Do not promote, merge identities or publish automatically.
