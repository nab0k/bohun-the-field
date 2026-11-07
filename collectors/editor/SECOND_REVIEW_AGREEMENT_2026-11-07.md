# C3-10: blind second-review reconciliation (2026-11-07)

## Evidence received

The owner supplied a 25-row `SECOND_REVIEW_RESPONSE.csv` with labels and short explanations for the same private 5-synthesis provisional holdout (syn-483, 488, 495, 501, 502). The reviewer was instructed to work blind to the original labels. **This report verifies the submitted data and agreement, not the reviewer's actual process or independence.**

The original 25 `case_id` values and `material_id` values were checked against the privately stored first-pass JSON. No IDs were absent, extra, or duplicated. The pasted CSV had **17 free-text reason cells with unquoted commas**, in two cases more than one; they were normalized without altering case IDs, labels, or explanation meaning. A second invocation of the original `SCORE_SECOND_REVIEW.py` reproduced the comparison.

## Actual label agreement

| Source-entailment label | First pass | Submitted second pass | Uncontested consensus |
|---|---:|---:|---:|
| supported | 14 | 14 | **14** |
| contradicted | 7 | 8 | **7** |
| insufficient | 4 | 3 | **3** |
| **Total** | **25** | **25** | **24** |

**Observed agreement 24/25 = 96.0%; Cohen's kappa = 0.9307**. Confusion matrix, first-pass rows and second-pass columns: supported (14,0,0), contradicted (0,7,0), insufficient (0,1,3), with label order supported/contradicted/insufficient. These are *annotation consistency* metrics, **not Groq/Gemini accuracy, semantic recall, truth verification, or safe publishing thresholds**. The small sample is intentionally seeded with synthetic errors.

Second reviewer: 22 high-confidence, 3 medium-confidence labels; all 25 source snippets marked relevant. Three cases were marked for full article review: `c310-holdout-483-01`, `c310-holdout-495-04`, `c310-holdout-502-04`.

## Unresolved one-case disagreement

- `c310-holdout-488-05` (syn-488; intentionally modified test claim about an already enacted/funded 40-ship plan).
- First pass: **insufficient**. Second pass: **contradicted**.
- The supplied excerpt reports a *planned discussion with lawmakers about funding*. This strongly challenges the claim's completion wording but does not on its own document the legislative negative. A strict distinction between **lack of confirmation** and **positive contradiction** remains important.

**No automatic adjudication**: this case retains `needs_third_adjudication` and **no final label**. The three additional medium-confidence cases remain provisional despite two-pass agreement.

## Private artifacts

A **private** reconciliation report, corrected 25-row CSV, 25-row first-vs-second CSV, discrepancy JSON, and third-review blind mini-packet for **four priority cases** were produced in chat. The blind packet intentionally excludes first/second labels and reviewer explanations. Full claim text and article excerpts are not committed to this public PR.

## Next acceptance gate

Have an independent third adjudicator inspect the **four priority cases** and their full P2 article context, with clear rules for `contradicted` versus `insufficient`. Freeze the agreed labels only after arbitration, then independently attest relevant P1 provenance and run a separately budget-authorized Groq/Gemini test. No provider calls, workflow runs, Supabase writes, publishing or integration changes occurred in this step.

**Release: NO.** Draft PR stays open. Earlier 183/183 offline editor unit tests are unchanged by this documentation-only addition.
