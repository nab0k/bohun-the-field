# EXP-009 R6: UK Find a Tender bounded HOLD pilot (review only)

DRAFT ONLY. DO NOT MERGE, DEPLOY, IMPORT OR PUBLISH WITHOUT SEPARATE OWNER APPROVAL.

This PR is stacked on R5/R5.1 draft PR #76 because R6 imports the hardened identity guard. It is not independently mergeable into main until that dependency is resolved. R3/R4 draft PR #74 remains separate.

## Real upstream evidence
2026-10-21 live R5.1 GitHub Actions run 39614064835: historical UK hour yielded 42 releases (10 tender, 22 awards, 10 other). R6 writer has NOT run on live source or PostgreSQL. These are unverified notices, not confirmed defence procurements.

## Code
- uk_hold_pilot.py: in-memory selector, max 10 procurement notices from verified-complete single bounded window; exact IDs and minimal HOLD provenance; no guessed tender status/URLs.
- uk_live_pilot.py: read-only live diagnostic using official_sources.uk_probe. Fixed 2026-10-15 historical window; no logging of raw responses.
- uk_hold_writer.py: offline classification and SQL statement spec.
- uk_hold_apply.py: isolated, non-CLI guarded transaction executor, requires separate owner approval references, audited reader confirmation and credentials. Requires UK source pre-registration.
- tests/test_uk_pilot.py, test_writer_transaction.py, test_live_and_apply.py and synthetic_uk.json: 27 synthetic offline regression tests.
- uk_R6_READONLY_PREFLIGHT.sql: inspection only.
- uk_R6_SOURCE_REGISTER_REVIEW_ONLY.sql: source registration proposal; never executed.

Unlike the original local R6 review archive, this PR deliberately excludes reference/ copies of existing normalize.py, R5.1 hardened gate and test-only official_sources.py. The GitHub branch must run against its real source reader.

## Test
From collectors/official_source_review:
    python -m unittest discover -s tests -v
Run previous normalizer/source tests separately:
    python -m unittest -v

## Blocking checks before any real DB insert
1. Source licence and data retention/redistribution check.
2. Audit all site, backend, API, cache and export readers of intelligence.items. HOLD is in raw_data JSONB, not a first-class database column, and is not a guaranteed public-access gate.
3. SQL, RLS, identity, origin, transaction rollback and idempotency review.
4. Explicit owner approval to register UK source, then verification.
5. Separate owner approval for exact <=10 row bounded transaction with owner and audited-reader reference.
6. Reconcile inserted rows, rerun for zero duplicates, test updates and rollback on controlled fixture in a safe database.
7. Gap-free historic UK crawl, alerts/checkpoints and seven-day monitored soak, then Canada/EU.

No source registration, Supabase write, recurring job or website publication occurred.
