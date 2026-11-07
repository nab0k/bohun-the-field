# C3-10: isolated offline editorial gate (draft)

**Card:** C3-10, dependent on C3-09 and C3-04. **Scope:** only collectors/editor/. The owner authorized code and offline tests on 2026-11-06, not merging, editing workflows, calling models, accessing live services or integrating the publisher. C3-09 design is in draft PR #176; C3-04 PR #184 is merged but its full acceptance/live comparison remains unverified. This PR does not close C3-10.

## What is implemented

- core.py: review(candidate, evidence, policy, assessment=..., published=...) returns EditorVerdict. PublishCandidate includes the entire visible publishing payload (headline, sections, sources, context and map), ID, version, material type, author model and event identifiers.
- EvidenceBundle carries source IDs, independent origin roots, dates, retrieval verification flags and evidence spans.
- Canonical content and evidence hashes, plus a hash of all candidate identity metadata (material ID/type, version, author model, event IDs and event fingerprint), with both policy version and the full policy configuration hash, bind a verdict to an exact version. Changed quality thresholds invalidate prior pin matches. Altering the payload, evidence, map or version invalidates earlier matches. This is only local checking, **not an atomic publisher transaction**.
- Deterministic reason codes: E00 (contract and mandatory fields), E01 (URL syntax and source manifest), E02 (numbers/currency in the cited span), E03 (span and claim evidence), E04 (event-linked context), E07 (date formatting and chronology), E12 (independent checker, stamps and semantic coverage), E13–E15 (partial signal requirements), E16 (source/span/event/date validation of explicit stage_assertions), E17 (headline), E18 (possible or definite duplicate). No real URL fetch or event-fingerprint generation is performed.
- stages.py + test_stages.py: optional structured event-stage checks (E16) require an explicit stage, event ID, date and existing source/span reference. An event is NOT automatically promoted from announced to contracted, delivered or demonstrated. A future-stage assertion relative to `as_of` goes to REVIEW. Only structural/date/provenance validation is covered, not semantic confirmation of the cited source text. 15 isolated tests passed locally (exact GitHub blob bytes verified).
- identity.py + test_identity.py: pure offline identity pin with 12 tests passed against GitHub-verified exact file bytes. Changing an event ID, event fingerprint or author model invalidates an old PASS; malformed metadata fails closed. The earlier 70 core integration tests passed in the previous 97-test snapshot; the current patched branch needs another complete run.
- SemanticAssessment can be supplied by a future trusted independent-model adapter with matching content/evidence hashes and claim-level evidence. Without it the verdict is REVIEW or BLOCK, not PASS. Matching the author model does not count as independent.
- metrics.py scores synthetic or real gold cases against the owner-approved **provisional targets**: deterministic recall ≥99%; semantic recall ≥85%; false BLOCK among good materials ≤5%; good material on REVIEW ≤20%. The output contains denominators and point estimates, NOT confidence guarantees.

## Hard boundaries

**Not production-ready.** EditorVerdict.offline_only is always True, including for an offline PASS. EditorVerdict.matches is NOT authorization to publish. There is no trusted checker adapter, evidence-extraction/storage interface, network link verification, publisher compare-and-swap, cross-run duplicate index, quota scheduler, alerting, workflow or publishing integration.

The caller currently supplies retrieval_verified and coverage_complete; both are unauthenticated local claims. The core now returns BLOCK on unserializable/cyclic publishing or evidence payloads and rejects malformed references, policy edits, unknown coverage and unassessed visible numbers. A caller can fabricate them. Do not connect this module to production. Semantic categories E05/E08/E09/E11/E16/E17, causal overreach, whether source evidence truly establishes an announced/contracted/delivered/demonstrated stage, and global semantic duplicates are NOT comprehensively verified. E16 is a structural guard only: it cannot infer a missing stage from an earlier one. Source text is used only in memory for hashing and comparisons, and is not included in verdict reason strings.

No code from FActScore, FacTool, Loki, SAFE or other third-party solutions was copied or executed. Only Python standard library modules are imported.

## Offline tests

**Previous baseline (2026-11-07): 97/97 offline Python unittest cases passed (OK), 0 failures, 0 errors, 0 skips; 0.027 s, Python 3.13.** That run used the exact GitHub blob SHA of every Python file **before** the Antigravity-review changes below. **Previously tested baseline: 104 tests (77 core + 15 stages + 12 identity); Antigravity reported that all 104 passed in a read-only, offline recheck on 2026-11-07 (0 failures, 0 errors, 0 skips).** This result is based on Antigravity's summary delivered by the owner, not an independently archived full console log. Antigravity used the extracted `chatgpt/C3-10` ZIP project root and adjusted the Python import root via `PYTHONPATH` / `unittest -t .`; it did not edit code. The exact checkout commit SHA was not present in the summary, so a byte-for-byte match to the present branch is not independently established for this external run. Nine focused local regex/IDN checks passed on a reproduced equivalent implementation (9/9 OK), but they are not a substitute for running all 104 tests on the checked-out branch.

- 77 tests in `test_core.py`: the prior 70 editor checks plus seven regressions for Unicode-spaced thousands, punycode/Unicode domains and private IP rejection. One new test exercises a whole offline review against a cited source span.
- 15 tests in `test_stages.py`: structural provenance and event-stage chronology.
- 12 tests in `test_identity.py`: publication material identity and event metadata pinning.

Run from the **repository root**, the folder containing `collectors/`. If using ZIP downloads, open the extracted branch directory, **not** its parent. Verify `collectors/editor/test_core.py` exists before running. The `collectors` import error in the external Antigravity report occurred in its execution environment; do **not** add `sys.path.append('..')` to package code.

On Linux/macOS (from repository root), without external API requests:

    PYTHONPATH=. python -B -m unittest discover -s collectors/editor -p 'test_*.py' -v

On Windows PowerShell (from repository root):

    $env:PYTHONPATH = (Get-Location).Path
    py -B -m unittest discover -s collectors/editor -p "test_*.py" -v

The 104/104 external recheck is attributed to Antigravity's owner-supplied report; it does not supersede the historical hash-verified 97-test baseline below. For future reproducibility, record `git rev-parse HEAD` and the full test log before merge.

The following **historical** Git blob SHA list documents the exact eight Python files used for the 97/97 baseline run, **not** the current versions of `core.py` and `test_core.py`:

| File | Verified Git blob SHA |
|---|---|
| `__init__.py` | `ac1e3480c2ed2f9df19c63eac6910e56bcb33f12` |
| `core.py` | `6c99d487e0416b6b4a72a97bcdbdbe0b2cc9ac77` |
| `identity.py` | `8033b26ffbdaf66fe379f2b4fb329f6b905dfec3` |
| `metrics.py` | `b5906429e19e117bf6640af16e599261d4ea98e4` |
| `stages.py` | `66d092277ed5299803595bf0b4d15bea7a3b1117` |
| `test_core.py` | `f078fb0c87dd7bcc247a1a40c82ce3e2809f4ce3` |
| `test_identity.py` | `a52d4d331802bf546fa8be81f0ae2f987d924cca` |
| `test_stages.py` | `23b433ed20cf02c4ee0d443b267c40ffd10c2a05` |

**Scope:** the test suite uses synthetic fixtures only; no production data, real article text or independent model check was used. Passing these tests is **not** evidence that the provisional 99%/85%/5%/20% quality targets have been achieved, and not authorization for production publishing. Missing trusted checker, CAS publisher, live source validation and C3-04 integration still block C3-10 acceptance.

## Independent audit follow-up

See [ANTIGRAVITY_AUDIT_2026-11-07.md](ANTIGRAVITY_AUDIT_2026-11-07.md) for the disposition of findings C1–C9 and M1–N3. The external FAIL verdict for **production readiness** is accepted. Confirmed regex issue M2 was corrected; other asserted bugs were either not reproduced or are unresolved **integration/trust-boundary** requirements rather than defects in the current offline-only prototype. After this audit, the owner provided a second Antigravity run reporting 104/104 passing after the correct import root was set; no test failures were reported. This is external report evidence, not a production quality benchmark or a trusted checker evaluation.

## Read-only real-data pilot and shadow adapter (07.11.2026)

See [SHADOW_DRY_RUN_2026-11-07.md](SHADOW_DRY_RUN_2026-11-07.md) for the actual **25**-record published synthesis metadata preflight. It used only SELECT statements, with no source/article text committed. On the live sample: 25/25 mandatory headline/what/why sections populated; 25/25 had stored source captures; 17/25 had no P0/P1 link in the published manifest; 0/25 had `knowledge.synthesis_claim` rows; 152 context source references lacked `event_ids`/`span_id` links; two published source dates were blank (synthesis IDs 489 and 507). These are observed facts about stored metadata, **NOT 25 executed `EditorVerdict` results**.

`shadow.py` is a pure offline adapter over a locally supplied JSON array of at most 30 records. It creates candidates/evidence from the existing schema, **never** upgrades source-capture existence to independently verified provenance and never supplies a synthetic `SemanticAssessment`; old PUBLISH records cannot become new PASS. It returns only IDs, decision codes and integration gaps, not source texts. Twelve regression tests in `test_shadow.py` bring the suite to **116 cases**. The owner has provided an **Antigravity report of 116/116 OK** on the fresh branch; ChatGPT has not separately re-run these 116 unittests. A **separate verified local Python shadow replay over all 25 full published synthesis payloads** has now executed successfully: **23 REVIEW, 2 BLOCK (`syn-489`, `syn-507`), 0 PASS, zero exceptions**. All 25 results are `offline_only=true` and `publication_authorized=false`. Only the IDs, reason codes, aggregate counts and SHA checksums are in the public PR; the JSON input containing full published narratives was delivered privately. See the last section of `SHADOW_DRY_RUN_2026-11-07.md` for exact test evidence and limitations.

## Remaining C3-10 acceptance gates

1. Execute **all 104 offline tests** on the current branch and fix any failures. Check C3-04 compatibility in an isolated fixture.
2. Add trusted evidence provenance, an independent provider-checker over the complete final publishing payload, and source/date verification. Never log copyrighted full articles or confidential snippets.
3. Implement and verify a real atomic publisher compare-and-swap binding material ID, immutable version, content/evidence hashes, policy and expiry to the transaction. Without it a local PASS never permits publication.
4. Test E01–E18 on withheld synthetic and real errors including CaseEvak and syn-364/syn-378, as well as genuinely good stories. Report denominators, observed metrics, uncertainty intervals, false BLOCK, REVIEW and provider use. Target thresholds are not yet measured.
5. Keep main, .github/workflows, production data, the website and project-status unchanged. Workflow edits and live tests require a separate direct owner command. This PR remains a draft.

**Result:** a deliberately limited first code-and-tests slice of C3-10, not an integration or completed card.
