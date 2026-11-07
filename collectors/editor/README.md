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

## Next integration slice: local captured-source integrity

`capture_adapter.py` introduces **offline-only** binding of a `LocalCapture` (exact URL, source_id, source-text SHA-256 and text) to a `QuoteOffset` (source_id, Python character start/end). `attach_local_captures(...)` returns an `EvidenceBundle` with deterministic span IDs and exact, maximum 200-character private quote strings. It rejects missing/extra/duplicate captures, URL mismatch, changed text/hash, invalid and duplicate offsets. Failure codes contain no text.

**CRITICAL trust boundary:** a SHA of text in a local snapshot proves integrity of that snapshot, **not** independent retrieval or factual truth. The function **always sets `retrieval_verified=False`**, even if a caller sends `True`; it does not supply a `SemanticAssessment`, assert independent roots, check claim truth or authorize publication. Treat the existing private capture text as an *input* only. Never log captures, quote strings or model payloads to public CI.

`test_capture_adapter.py` adds **13 synthetic regression tests**. These were run locally using Python `unittest`, with **13/13 OK**, before creation of the matching GitHub files. They have **not yet been rerun from a checkout of the new GitHub commit**, and the full branch test suite now contains **129 written test cases** (116 previously reported passing by Antigravity plus 13 new ones). The 129/129 result is **not** claimed.

**Next:** implement an independently trusted capture/provenance resolver using `knowledge.event_document` to link exact `intelligence.items` records to published URL and source body, then an independent checker that emits claim-level citations pinned to the exact candidate/evidence/policy hashes. Until trusted retrieval, gold/holdout benchmarks, dedupe authority and atomic publisher CAS exist, everything stays offline/REVIEW.

## Next integration slice: event-document lineage (07.11.2026)

See [LINEAGE_PREFLIGHT_2026-11-07.md](LINEAGE_PREFLIGHT_2026-11-07.md). An actual **read-only** comparison of the frozen 25 published syntheses showed **30/38** manifest URL links had an exact `knowledge.event_document` → `intelligence.items.url` match with a saved capture; **30/30** matched source publication dates; **8** URLs lacked exact linked items (syntheses **486, 489, 491, 493, 498, 503, 505, 507**). **17/25** syntheses had every manifest URL linked. No origin or content truth is inferred from these numbers.

`lineage_adapter.py` is an offline-only bridge that requires each manifest `source_id` to bind to exactly one saved item and event relation, with exact URLs/publication days and integrity-checked character offsets. It reuses `capture_adapter.py` to produce deterministic quote spans and **never** marks a source `retrieval_verified=True`. It is **not** a trusted provenance resolver and does not connect to live DB, fetch external URLs, invoke a model or publish. Sixteen new synthetic tests in `test_lineage_adapter.py` and the prior thirteen `test_capture_adapter.py` tests ran together **29/29 OK** locally, with exact blob SHA checks for the new files. This local discovery contained only these two test files. At this intermediate stage there were **145 written test cases** (116 previously externally passed, plus 13+16 tested locally in separate runs); these have since been supplemented by 23 checker-probe cases. The complete 168-test suite has NOT been executed together at the current HEAD. Do not call the combined suite passed.

## 07.11.2026: eight primary-source discrepancies and checker probe

The read-only primary-source investigation in [UNMATCHED_P1_TRIAGE_2026-11-07.md](UNMATCHED_P1_TRIAGE_2026-11-07.md) found a release-significant source-class gap: **0/8 P1/primary links** from the frozen 25 published syntheses match the correct event's saved `intelligence.items.url`, while **30/30 P2/report links** have exact matching saved event-document captures. The eight P1 links include two unrelated-topic articles, two publisher homepages, three apparent variations of the same already-saved P2 article, and one official-release link whose documented department path differs from a live government page. **No P1 was promoted to verified** and no production records changed. A plausible title or alias is never sufficient for an automatic repair.

`checker_probe.py` and `test_checker_probe.py` provide a **provider-neutral local input/output probe** for a future independently run Groq/Gemini checker. Full visible candidate content and saved short spans are pinned to content/evidence/policy/request hashes. Untrusted model JSON must cover all provisional sentence segments and use existing source/span IDs. Any self-declared attestation, `PASS` or publishing permission is ignored. The validator deliberately never creates a trusted `SemanticAssessment`. **No external AI call is implemented or executed**. Full details: [CHECKER_PROBE_2026-11-07.md](CHECKER_PROBE_2026-11-07.md).

**Test provenance:** 23 new probe tests plus 16 lineage and 13 capture tests ran together, **52/52 OK locally**; exact Git blob SHA of the two new files was compared with the tested bytes. Earlier stages were tested separately. The branch currently contains **168 written tests**; **the full 168-test suite on this HEAD has not been executed**.

## Verified current 168-test milestone and offline probe, 07.11.2026

**All 168/168 editor unit tests passed in one locally executed Python 3.13.5 run, exit code 0, 0 failures/errors/skips.** The editor-only working copy contained exactly the **16** current branch Python files; each local `git hash-object` matched the GitHub blob SHA of `chatgpt/C3-10` at commit `92fe64341bf3ca9b4594df03e557eecb8d5becb5`. See [EDITOR_168_AND_PROBE_AUDIT_2026-11-07.md](EDITOR_168_AND_PROBE_AUDIT_2026-11-07.md) for the command, checksums and limitations. This result supersedes **older interim documentation** saying 168 tests had not been run.

A **real 25-record private JSON preflight** with `shadow._prepare()` + `checker_probe.prepare_probe()` found `probe_has_no_spans` in **25/25** because the material export has URLs and synthesis texts but does not include source article capture quote spans. A separate 3-claim synthetic reply with all `supported` statuses was accepted as *untrusted structure only*: **no trusted assessment, 0 provider calls, 0 publication permission**. These results do not measure model accuracy or prove any P1 provenance. A 25-record source capture export and independently verified retrieval are still outstanding.

## Real P2 excerpt-to-checker probe (07.11.2026)

The earlier `25/25 probe_has_no_spans` issue is now **resolved for a narrow offline smoke test**: [REAL_P2_SPAN_PROBE_2026-11-07.md](REAL_P2_SPAN_PROBE_2026-11-07.md) documents a **completed 25/25 local replay** with one actual **stored P2 article prefix** (max 115 characters) attached to each synthesis. All 25 produced pinned `checker_probe` requests with **349 provisional sentence segments**, while `core.review` retained **23 REVIEW, 2 BLOCK, 0 PASS**. No LLM was called. The private text-bearing request packets and JSON report were delivered in chat, **not added to public GitHub**.

**Do not confuse a captured prefix with a supporting passage:** this checks data plumbing only, not claim relevance or entailment. All sources remain `retrieval_verified=False`. **0/8 P1 manifest sources** in the frozen 25 are independently linked to event captures. The full 168 offline unit tests passed at the preceding code revision; only a documentation file has been added since then. Real full-article semantic evaluation, complete atomic claim coverage, independently attested retrieval, labeled holdout and C3-04 CAS are still open.

## 5-synthesis provisional gold candidate and 183-unit-test milestone (07.11.2026)

A **real, private, read-only 5-synthesis pilot** uses final versions **483, 488, 495, 501, 502**, with **25 manually first-pass-labeled atomic statements**, **18 exact source excerpts** from saved event-linked P2 articles and 10 deliberately edited negative/uncertain examples. The expected article-entailment labels are **14 supported, 7 contradicted, 4 insufficient**. Crucially these are **not** 25 independently verified real-world facts and **not** a blind second-annotated gold set. See [GOLD_CANDIDATE_5_AUDIT_2026-11-07.md](GOLD_CANDIDATE_5_AUDIT_2026-11-07.md); private text-bearing JSON and the run log remain chat attachments only.

The five materials successfully formed **5/5 checker packets** with actual relevant source spans (69 *provisional sentence* segments across full narratives). Running the real offline editor with these spans and `assessment=None` yielded **5 REVIEW, 0 BLOCK, 0 PASS**. No trusted retrieval, provider calls, API costs or publication actions occurred.

`holdout_contract.py` and `test_holdout_contract.py` add **15 synthetic, fail-closed tests** for tampered texts/hashes, invalid offsets and cross-material span references, forged second review or trust flags and permission escalation. **All 183 editor tests passed in one local suite at exact Git blob byte hashes** (previous 168 + 15). This supersedes older documentation listing 168 tests as the latest count. The **full application integration tests and model quality thresholds remain open**.

## Blind second review: 24/25 agreement (07.11.2026)

See [SECOND_REVIEW_AGREEMENT_2026-11-07.md](SECOND_REVIEW_AGREEMENT_2026-11-07.md). The owner's submitted second-pass labels matched the provisional first-pass labels in **24/25 cases (96.0%, Cohen's κ=0.9307)** across five syntheses. **One unresolved disagreement**, `c310-holdout-488-05`, concerns whether a proposed future funding discussion disproves or merely fails to establish a claim that funding was already enacted. Its final label is deliberately blank and awaiting third independent adjudication. Three other insufficient cases requested full-source review; an owner-only blind four-case adjudication packet was provided. This is *label consistency on P2 snippets*, **not** model accuracy or verified ground truth. No code, source secrets or infrastructure was changed in this document-only step.

## Third adjudication: provisional consensus across all 25 cases (07.11.2026)

The owner returned four third-review answers from Antigravity (provided as chat text, not as an independently attached CSV). See [THIRD_REVIEW_CONSENSUS_2026-11-07.md](THIRD_REVIEW_CONSENSUS_2026-11-07.md). All four case IDs and source-span IDs matched the blind fixture. **Provisional source-entailment labels for all 25** now total **14 supported, 7 contradicted, 4 insufficient**: 21 cases agreed on first/second pass, three `insufficient` cases were unanimous among three reviewers, and the one disputed `c310-holdout-488-05` was resolved **insufficient by 2 of 3**. **Full-article review is still requested for all four third-pass cases**, so this is **not a fully validated gold standard**, real-world truth attestation or AI quality measurement. No editor code was changed at this step; previous **183/183 unit tests** were not rerun.

## Owner-authorized live shadow trial adapter (07.11.2026)

See [LIVE_SHADOW_TRIAL_2026-11-07.md](LIVE_SHADOW_TRIAL_2026-11-07.md). The current branch now adds [live_shadow.py](live_shadow.py) and [test_live_shadow.py](test_live_shadow.py), plus a **new manual-only** workflow at .github/workflows/editor-shadow.yml. The adapter reads the **most recent generated version for each nonhidden synthesis**, including unpublished candidates, with a server-verified READ ONLY transaction, an upper bound of 30 materials, no writer or independent checker API, and an ID/reason-code-only report. It never authorizes publication. A version's created_at serves only as the as-of date in this structural diagnostic, **not its real public publication date**.

**Actual live SQL preflight:** **107** newly generated latest versions within 24 hours; an exact 30-row query from the real DB returned **30 well-shaped materials, 35 sources, 4 missing source dates, 0 non-HTTPS URLs and 0 absent required sections**. This is **not** an actual execution of the full Python editor against a live DB connection, and no live editor verdict distribution was measured. The new 12 SQL-contract tests passed **12/12 locally against a test-only stand-in reviewer**, not the real editor. The previous **183/183** result is historical; **195/195 now passed in one exact-source local unit-test run**. See the full-test section below. No real DB CLI shadow run has occurred.

The workflow is manual-only and needs an owner-reviewed merge to main before GitHub Actions can dispatch it. No workflow was launched: the connected GitHub actions here cannot dispatch a workflow and this workflow is not on the default branch. The existing production synthesis-live workflow was not altered. First do the full 195-test checkout, then review/merge, then dispatch exactly one 24-hour/30-record test and inspect artifact. No automatic gate, schedule or production publisher integration is authorized by this draft.

## Full 195/195 editor tests verified against real editor (07.11.2026)

The **complete 195/195** unit tests **passed**, exit code 0, 0 failures/errors/skips, on Python 3.13.5 with the **actual core.review and shadow.shadow_review**. Twenty source/test Python files were fetched from this Draft PR (code HEAD 1a5a4823fab86c1fbb63e80c5997efbf5f787531), and **all 20 exact Git blob SHA values** were verified locally before running. New live-shadow 12 tests now pass **without any reviewer stub**. Read [EDITOR_195_VERIFIED_2026-11-07.md](EDITOR_195_VERIFIED_2026-11-07.md) for command and manifest digest. This updates/supersedes the earlier interim language saying 195 tests had not been run; full verbose log is private in the chat.

**Still not executed:** no GitHub Actions workflow_dispatch, no true PostgreSQL CLI connection, no production publication/admission integration, no independent Gemini/Groq assessment. The real 30-row Supabase SELECT preflight is distinct from the local Python unit tests. PR #196 stays Draft. Reviewer acceptance and owner-authorized merge are still required before first manual Actions shadow trial.

## Remaining C3-10 acceptance gates

1. **COMPLETE:** 183/183 editor unit tests on the exact checked source bytes (168 prior + 15 holdout-contract tests). **OPEN:** integration tests in a full repo checkout, especially C3-04 publisher compatibility and CAS.
2. Add trusted evidence provenance, an independent provider-checker over the complete final publishing payload, and source/date verification. Never log copyrighted full articles or confidential snippets.
3. Implement and verify a real atomic publisher compare-and-swap binding material ID, immutable version, content/evidence hashes, policy and expiry to the transaction. Without it a local PASS never permits publication.
4. Test E01–E18 on withheld synthetic and real errors including CaseEvak and syn-364/syn-378, as well as genuinely good stories. Report denominators, observed metrics, uncertainty intervals, false BLOCK, REVIEW and provider use. Target thresholds are not yet measured.
5. Keep main, .github/workflows, production data, the website and project-status unchanged. Workflow edits and live tests require a separate direct owner command. This PR remains a draft.

**Result:** a deliberately limited first code-and-tests slice of C3-10, not an integration or completed card.
