# C3-10: five-synthesis provisional claim-evidence holdout (2026-11-07)

## Executed, read-only

A **first-pass manual source-entailment annotation** was prepared from the published synthesis versions **483, 488, 495, 501 and 502** and privately read-only captured P2 article text already linked by `knowledge.event_document → intelligence.items`. This is an intentionally small **gold candidate**, not an independently validated benchmark or trusted statement of real-world truth.

| Source-entailment label | Cases |
|---|---:|
| `supported` by the quoted P2 article | **14** |
| `contradicted` by the quoted P2 article | **7** |
| `insufficient` cited evidence | **4** |
| **Total** | **25**, five per synthesis |

**18** unique exact source excerpts (each at most 200 Unicode characters) were selected with SQL-derived 0-based character offsets and quote MD5. All **18/18** were successfully rechecked against the same literal excerpt bytes locally. Each private excerpt also carries a SHA-256 digest, stored item ID and a marker that **independent retrieval is NOT attested**. Only one exact event-linked P2 article per synthesis was used. These excerpts are private and **NOT committed to GitHub**.

**10/25 claims are deliberate synthetic modifications** to test numerical changes, dates, order/delivery status, ranking, and deal-completion overstatement. These must never be described as errors found in original BOHUN output. Some of the remaining cases are source-faithful paraphrases rather than literal claims extracted from the synthesis. The claim labels are the assistant's **first-pass provisional annotation**; **zero independent second reviews** have been completed.

**One real textual caution:** the cached P2 article concerning syn-483 reportedly describes the German KNDS stake as apparent/reported (`offenbar`), not as an independently confirmed unconditional final agreement. In the provisional set, the unqualified stronger claim is labeled **insufficient**. This is a reason for editorial REVIEW of modality, not a conclusion that the transaction is false.

## Actual offline execution

1. Locally invoked `shadow._prepare`, `checker_probe.prepare_probe` and `core.review(assessment=None,published=())` on **5/5** complete previously exported published synthesis versions with **18 actual, relevant P2 excerpts** attached as untrusted `EvidenceSpan` objects.
2. The five `checker_probe` input packets were successfully created and contained **69 provisional sentence segments** across their full published narratives. No provider was called. The final `core.review` verdicts stayed **5 REVIEW, 0 BLOCK, 0 PASS** and authorized no publication.
3. Added `holdout_contract.py` and `test_holdout_contract.py` to validate the private candidate's internal integrity: sha256 of claims and excerpts, offset lengths, unique IDs, correct material/span references, valid labels, refusal of self-asserted source trust, independent review and publication. Reports include only counts and IDs, never private article or claim text.
4. Executed exact **183/183 editor unit tests** in one isolated Python 3.13.5 suite (the prior 168 + **15** new synthetic contract tests): `PYTHONPATH=. python -B -m unittest discover -s collectors/editor -t . -p 'test_*.py' -v`, exit code 0, zero errors/failures. New GitHub file blob SHAs matched tested local files:
   - `holdout_contract.py` = `abc2e93211c06020281bdb19f41eebf9709067d1`
   - `test_holdout_contract.py` = `853532b69903d4050ae74cac939bf11120172051`

## Private artifacts and hashes

The private first-pass fixture `BOHUN_C3-10_GOLD_CANDIDATE_5_PRIVATE_2026-11-07.json` contains the 25 labeled cases, 18 excerpt texts, Unicode character offsets, hashes and 5 probe metadata records. SHA-256: `04d0090c8fc6a55b558d42394847aa7a4fc3be01d999418a99ebe96505b4b9da`. Its private ZIP, CSV index and narrative report were delivered as conversation artifacts, not copied into this public repository. The full 183-test log is also retained privately.

## What has NOT been established

- **No independent second annotator:** disagreements and correctness of the 25 labels have not been resolved.
- **No independently authenticated primary sources P1:** the captured articles are stored P2, with no trusted HTTP acquisition/redirect verification.
- **No actual Groq/Gemini or other model semantic scoring:** provider calls and costs were **zero**. A valid offline checker packet is not a trusted `SemanticAssessment`.
- **No statistical acceptance:** five samples and 25 first-pass cases cannot establish error recall ≥99%, semantic recall ≥85%, false BLOCK ≤5% or good REVIEW ≤20%.
- **No full-repo integration, C3-04 publisher CAS or live release.** `main`, workflows, site, secrets, and Supabase write operations remain untouched.

**Next gate:** independent blind second review of this provisional fixture, then a separately authorized and rate-limited independent-model evaluation, and only then larger gold/holdout samples, independent P1 acquisition and C3-04 atomic publishing safeguards.

**Release: NO. PR stays Draft.**
