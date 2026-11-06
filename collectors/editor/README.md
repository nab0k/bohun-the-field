# C3-10: isolated offline editorial gate (draft)

**Card:** C3-10, dependent on C3-09 and C3-04. **Scope:** only collectors/editor/. The owner authorized code and offline tests on 2026-11-06, not merging, editing workflows, calling models, accessing live services or integrating the publisher. C3-09 design is in draft PR #176; C3-04 PR #184 is merged but its full acceptance/live comparison remains unverified. This PR does not close C3-10.

## What is implemented

- core.py: review(candidate, evidence, policy, assessment=..., published=...) returns EditorVerdict. PublishCandidate includes the entire visible publishing payload (headline, sections, sources, context and map), ID, version, material type, author model and event identifiers.
- EvidenceBundle carries source IDs, independent origin roots, dates, retrieval verification flags and evidence spans.
- Canonical content and evidence hashes, with policy version, bind a verdict to an exact version. Altering the payload, evidence, map or version invalidates earlier matches. This is only local checking, **not an atomic publisher transaction**.
- Deterministic reason codes: E00 (contract and mandatory fields), E01 (URL syntax and source manifest), E02 (numbers/currency in the cited span), E03 (span and claim evidence), E04 (event-linked context), E07 (date formatting and chronology), E12 (independent checker, stamps and semantic coverage), E13–E15 (partial signal requirements), E17 (headline), E18 (possible or definite duplicate). No real URL fetch or event-fingerprint generation is performed.
- SemanticAssessment can be supplied by a future trusted independent-model adapter with matching content/evidence hashes and claim-level evidence. Without it the verdict is REVIEW or BLOCK, not PASS. Matching the author model does not count as independent.
- metrics.py scores synthetic or real gold cases against the owner-approved **provisional targets**: deterministic recall ≥99%; semantic recall ≥85%; false BLOCK among good materials ≤5%; good material on REVIEW ≤20%. The output contains denominators and point estimates, NOT confidence guarantees.

## Hard boundaries

**Not production-ready.** EditorVerdict.offline_only is always True, including for an offline PASS. EditorVerdict.matches is NOT authorization to publish. There is no trusted checker adapter, evidence-extraction/storage interface, network link verification, publisher compare-and-swap, cross-run duplicate index, quota scheduler, alerting, workflow or publishing integration.

The caller currently supplies retrieval_verified and coverage_complete; both are unauthenticated local claims. A caller can fabricate them. Do not connect this module to production. Semantic categories E05/E08/E09/E11/E16/E17, causal overreach, stage and global semantic duplicates are NOT comprehensively verified. Source text is used only in memory for hashing and comparisons, and is not included in verdict reason strings.

No code from FActScore, FacTool, Loki, SAFE or other third-party solutions was copied or executed. Only Python standard library modules are imported.

## Offline tests

test_core.py contains **40** synthetic unittest methods: version pinning after corrections; edited headline/map/URL/evidence; numbers and currency; missing/contradictory claims; source and context validation; dates; duplicate stories across IDs; CaseEvak-shaped empty required section; preliminary scoring. The cases are fictional fixtures and are NOT validated on the production corpus.

Offline command in an isolated checkout of this exact PR branch:

    python -B -m unittest discover -s collectors/editor -p "test_*.py" -v

**Execution status as of 2026-11-06:** tests written but **NOT RUN**. The isolated test environment could not resolve raw.githubusercontent.com to fetch the new authored files. Its attempted download failed with DNS resolution errors and discovery yielded Ran 0 tests / NO TESTS RAN (NOT a test pass). Do not mistake this for a successful execution. No workflow or live provider request was run.

## Remaining C3-10 acceptance gates

1. Execute the offline tests on this exact branch and fix any failures. Check C3-04 compatibility in an isolated fixture.
2. Add trusted evidence provenance, an independent provider-checker over the complete final publishing payload, and source/date verification. Never log copyrighted full articles or confidential snippets.
3. Implement and verify a real atomic publisher compare-and-swap binding material ID, immutable version, content/evidence hashes, policy and expiry to the transaction. Without it a local PASS never permits publication.
4. Test E01–E18 on withheld synthetic and real errors including CaseEvak and syn-364/syn-378, as well as genuinely good stories. Report denominators, observed metrics, uncertainty intervals, false BLOCK, REVIEW and provider use. Target thresholds are not yet measured.
5. Keep main, .github/workflows, production data, the website and project-status unchanged. Workflow edits and live tests require a separate direct owner command. This PR remains a draft.

**Result:** a deliberately limited first code-and-tests slice of C3-10, not an integration or completed card.
