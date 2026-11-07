# C3-10: isolated offline editorial gate (draft)

**Card:** C3-10, dependent on C3-09 and C3-04. **Scope:** only collectors/editor/. The owner authorized code and offline tests on 2026-11-06, not merging, editing workflows, calling models, accessing live services or integrating the publisher. C3-09 design is in draft PR #176; C3-04 PR #184 is merged but its full acceptance/live comparison remains unverified. This PR does not close C3-10.

## What is implemented

- core.py: review(candidate, evidence, policy, assessment=..., published=...) returns EditorVerdict. PublishCandidate includes the entire visible publishing payload (headline, sections, sources, context and map), ID, version, material type, author model and event identifiers.
- EvidenceBundle carries source IDs, independent origin roots, dates, retrieval verification flags and evidence spans.
- Canonical content and evidence hashes, plus a hash of all candidate identity metadata (material ID/type, version, author model, event IDs and event fingerprint), with both policy version and the full policy configuration hash, bind a verdict to an exact version. Changed quality thresholds invalidate prior pin matches. Altering the payload, evidence, map or version invalidates earlier matches. This is only local checking, **not an atomic publisher transaction**.
- Deterministic reason codes: E00 (contract and mandatory fields), E01 (URL syntax and source manifest), E02 (numbers/currency in the cited span), E03 (span and claim evidence), E04 (event-linked context), E07 (date formatting and chronology), E12 (independent checker, stamps and semantic coverage), E13–E15 (partial signal requirements), E16 (source/span/event/date validation of explicit stage_assertions), E17 (headline), E18 (possible or definite duplicate). No real URL fetch or event-fingerprint generation is performed.
- stages.py + test_stages.py: optional structured event-stage checks (E16) require an explicit stage, event ID, date and existing source/span reference. An event is NOT automatically promoted from announced to contracted, delivered or demonstrated. A future-stage assertion relative to `as_of` goes to REVIEW. Only structural/date/provenance validation is covered, not semantic confirmation of the cited source text. 15 isolated tests passed locally (exact GitHub blob bytes verified).
- identity.py + test_identity.py: pure offline identity pin with 12 tests passed against GitHub-verified exact file bytes. Changing an event ID, event fingerprint or author model invalidates an old PASS; malformed metadata fails closed. **Main EditorVerdict integration test methods are not yet executed.**
- SemanticAssessment can be supplied by a future trusted independent-model adapter with matching content/evidence hashes and claim-level evidence. Without it the verdict is REVIEW or BLOCK, not PASS. Matching the author model does not count as independent.
- metrics.py scores synthetic or real gold cases against the owner-approved **provisional targets**: deterministic recall ≥99%; semantic recall ≥85%; false BLOCK among good materials ≤5%; good material on REVIEW ≤20%. The output contains denominators and point estimates, NOT confidence guarantees.

## Hard boundaries

**Not production-ready.** EditorVerdict.offline_only is always True, including for an offline PASS. EditorVerdict.matches is NOT authorization to publish. There is no trusted checker adapter, evidence-extraction/storage interface, network link verification, publisher compare-and-swap, cross-run duplicate index, quota scheduler, alerting, workflow or publishing integration.

The caller currently supplies retrieval_verified and coverage_complete; both are unauthenticated local claims. The core now returns BLOCK on unserializable/cyclic publishing or evidence payloads and rejects malformed references, policy edits, unknown coverage and unassessed visible numbers. A caller can fabricate them. Do not connect this module to production. Semantic categories E05/E08/E09/E11/E16/E17, causal overreach, whether source evidence truly establishes an announced/contracted/delivered/demonstrated stage, and global semantic duplicates are NOT comprehensively verified. E16 is a structural guard only: it cannot infer a missing stage from an earlier one. Source text is used only in memory for hashing and comparisons, and is not included in verdict reason strings.

No code from FActScore, FacTool, Loki, SAFE or other third-party solutions was copied or executed. Only Python standard library modules are imported.

## Offline tests

test_core.py contains **70** synthetic unittest methods: version pinning after corrections; edited headline/map/URL/evidence; numbers and currency; missing/contradictory claims; source and context validation; dates; duplicate stories across IDs; CaseEvak-shaped empty required section; preliminary scoring. The cases are fictional fixtures and are NOT validated on the production corpus. Added malformed/cyclic payload, NaN, malformed evidence/claim/context/catalog, changed policy, evidence-event mismatch and unassessed visible number regressions. These tests have **not** been executed, so their expected outcomes remain unverified.

Offline command in an isolated checkout of this exact PR branch (no secret/env files required):

    python -B -m unittest discover -s collectors/editor -p "test_*.py" -v

**Execution status as of 2026-11-07:** **27/27 isolated helper tests passed** with Python unittest on exact GitHub blob bytes: 15 in test_stages.py; 12 in test_identity.py. Git blob SHAs: stages.py = 66d092277ed5299803595bf0b4d15bea7a3b1117; test_stages.py = 23b433ed20cf02c4ee0d443b267c40ffd10c2a05; identity.py = 8033b26ffbdaf66fe379f2b4fb329f6b905dfec3; test_identity.py = a52d4d331802bf546fa8be81f0ae2f987d924cca. The additional **70 test_core.py methods are written but NOT RUN** because the isolated execution environment cannot resolve raw.githubusercontent.com and retrieve the complete core/test files. The five new identity integration tests and four E16 integration tests are part of those 70 unrun tests. Prior discovery showed Ran 0 tests / NO TESTS RAN (NOT a pass). No workflow or live provider request was run.

## Remaining C3-10 acceptance gates

1. Execute the offline tests on this exact branch and fix any failures. Check C3-04 compatibility in an isolated fixture.
2. Add trusted evidence provenance, an independent provider-checker over the complete final publishing payload, and source/date verification. Never log copyrighted full articles or confidential snippets.
3. Implement and verify a real atomic publisher compare-and-swap binding material ID, immutable version, content/evidence hashes, policy and expiry to the transaction. Without it a local PASS never permits publication.
4. Test E01–E18 on withheld synthetic and real errors including CaseEvak and syn-364/syn-378, as well as genuinely good stories. Report denominators, observed metrics, uncertainty intervals, false BLOCK, REVIEW and provider use. Target thresholds are not yet measured.
5. Keep main, .github/workflows, production data, the website and project-status unchanged. Workflow edits and live tests require a separate direct owner command. This PR remains a draft.

**Result:** a deliberately limited first code-and-tests slice of C3-10, not an integration or completed card.
