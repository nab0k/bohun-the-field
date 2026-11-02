# Continuous companies core — EXP-016, stage A

This is the isolated implementation of the approved first stage of
DESIGN_CONTINUOUS_v0 (2026-11-01). It is not deployed or connected to a database,
Notion, a website, a source service or a model. All tests use synthetic data.

## Included

- `resolve(..., policy="continuous_v0", evidence=...)`: a strict alternative to
  the existing legacy resolver. Name/country alone, similarity and a shared group
  domain never confirm a link. Conflicting identifiers hold the mention. Existing
  links require documented identifiers, an admitted saved binding, or the exact
  official-name/country/unique-domain route of ADR-018. Legal-form stripping is
  not used to confirm a domain-based link. Explicit source roles/types are checked.
- `planner.plan_page`: ten sections per registry ID, stable versioned job keys,
  keyset checkpoint, repeat suppression, changed-section invalidation and due times.
  Excluded/blocked profiles are accounted for with reasons. Discovery source jobs
  have stable per-source due windows and cursor versions.
- `changes.evaluate`: exact source hash/quote/subject/section checks, preservation
  of extracted qualifiers, private HOLD claims, protected fields and guarded fills.
  Only missing `/identity/website` and `/identity/country` can be filled under v0.
  Protected/nonempty values, rejected content, null/unknown, conflicting batch
  values and sensitive/unresolved geography do not receive automatic replacements.
- `worker.run_one`: one enrichment job through explicit ports. It handles lease
  expiry, source/quota waits, partial results and uncertain commits. A prepared
  result is saved through a port before commit; a reclaimed job reuses it rather
  than calling the processor again. Unknown external outcomes are held, not retried.

There is no native-library reimplementation here. The original libraries tested
in cloud Run02 belong in the later source/extraction adapter. This stage implements
planning, consistency and write-policy logic around those adapters.

## Trust and limits

`IdentityEvidence`, `Evidence.binding`, `Evidence.observed`, observed value/path and source kind are
admitted receipts from the later source/native ports, not labels that an LLM may
self-certify. The core can verify their consistency; it cannot establish their
truth from arbitrary prose. It does not detect natural-language negation or legal
identity by itself. Quoted text and explicit qualifiers are retained, never
silently changed from notice to award or planned delivery to actual delivery.

`https_host` checks URL syntax and IP literals only. It does not perform DNS,
robots, paywall or redirect admission. It must not be used as a live fetch guard.
Cross-source assessment requires distinct declared origin groups and different
content hashes; this is not an independent truth verification. All new claims
remain HOLD / public_release=false.

Country syntax validation here is not a complete legal registry or ISO dataset.
Live source admission must validate country codes and identifier jurisdiction,
including whether a supplier/facility ID identifies the legal entity in question.
An identifier scheme named in a model answer cannot admit itself.

The strict resolver runs against the existing SQLite store only in tests. Creation
returns a candidate identity; it does not call legacy `apply()` or add identifiers,
categories, roles or evidence to a live company. In production, the future writer
must atomically reserve/legal-bind the identity and source event. Never connect
the resolver to production by passing it an unrestricted PgStore and calling apply.

The worker handles enrichment only. Source/discovery jobs are planned and identity
logic is tested, but actual source fetching and discovery commits are later adapters.
Scheduling and the SQL/API/roles proposed in the design are absent from this stage.

## Required durable port behavior

`QueuePort` is a contract, not an implemented persistent queue:

1. Atomically claim eligible work, increment attempts and return a fencing token.
2. Load the exact input version; no stale/partial source as an empty result.
3. Save a complete immutable prepared result, with references to already persisted
   source receipts. Never rely on process memory after an external response.
4. Under the same transaction lock, validate token/expiry, policy hash and the
   current `CompanyView.fingerprint`, using `check_preconditions`; write private
   claims/source links/coverage and finish the job together.
5. Return the same receipt on exact replay. Reconcile unknown COMMIT through
   `lookup_result`, with no blind second write. Unexpected/ambiguous requests hold
   their quota reservation in the future shared ledger. Complete responses with
   conflicting/rejected fields retain NEEDS_REVIEW while valid private claims are
   saved. Explicitly partial extractions never finish a job.
6. Persist jobs and planning checkpoint atomically. A later sweep must find IDs
   inserted behind the keyset cursor. Source/quota waits need explicit not_before
   release; a model restart is not a durable scheduler.

The fake queue tests interleavings, manual changes and restarts; they do not prove
PostgreSQL concurrency, RLS, grants, network safety, model quotas or persistence.
Those require later disposable-PostgreSQL/cloud tests and addressed permissions.
Do not mark a real company enriched based on this test suite.

## Offline verification

Use the already installed Python 3.13, standard library only. In the isolated
stage, discover tests under `tests`, with the stage root on `sys.path`. Disable
bytecode writing (`-B`); `-I -S` removes user/site packages and ambient project paths.
The external `checks.txt` receipt records the exact command, interpreter, counts,
failures and duration. No pip, venv, Docker, network, model or secret is required.

The 3751-ID test is synthetic. It verifies 37510 accounted sections, stable keys,
paging/resumption and repeat behavior; it does not read or enrich the real registry.
Fixtures have no real company IDs, snapshots, personal data or precise UA locations.

Five original modules are byte-identical to pinned FIELD
`b9df9dec1dd1e1f4c040af2f5aa10d69caab9ba7`; `resolve.py` is the sole modified original.
The package is exactly 18 files. Source and final manifests, diff and result are
outside the stage and must be reviewed before any integration or Git operation.
