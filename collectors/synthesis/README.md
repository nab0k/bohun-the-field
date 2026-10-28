# Synthesis route — offline preparation

`python -B -m collectors.synthesis --help` lists the explicit local inputs. This
entrypoint accepts only `--mode offline`; it has no live mode, credential lookup,
Gemini transport or database adapter. No dependencies are installed by this code.

Pass `--selection`, `--corpus`, `--topics`, `--dictionaries`, `--prompt`, and a
private `--output` directory **outside the repository/public/dist**. Optional
`--catalog`, `--ner-predictions`, `--context` and `--writer-fixtures` are explicit
local snapshots. Source bodies, catalog exports and generated result JSON stay
outside Git. The CLI verifies document URL/text hashes against the selection;
expected event groups/types/decisions are visible only to the evaluator.

The output is `result.json` and `review.html`. The evaluator requires key facts
for the correct event type on at least 7 of 10 eligible A-06 controls, and rejects
E10. It runs the route twice and checks deterministic output and memory-store
idempotence. This is an extraction acceptance check, not evidence that the
result is a publication-ready synthesis.

## Implemented boundaries

- Literal sentence windows with exact offsets, <=200-character quotes, quote and
  source hashes. Amounts retain their literal currency: bare dollars never infer
  USD. A programme/sidebar total does not fill a missing participant award.
- EXP-013 rules from `8197629` are preserved in `legacy_rules.py`. NER catalog
  retrieval reuses `company_ner.linker`; all name links remain candidates. Saved
  NER offsets must agree with the source. No model downloads/native runs occur.
- Topic/dictionary resources are supplied and hashed. Unknown source class stays
  unknown; capture completeness and absence of flags never imply P0/P1 or QA.
- Conservative procedure/hard-feature matching; exact reprints do not count as
  independent sources. Weak name/title similarity creates separate events.
- Primary URL candidate selection is bounded to two; no pages are fetched.
- Context consumes source-bound snapshot records, not past synthesis prose.
- Writer request/schema preparation from the supplied Markdown, saved responses
  keyed by exact packet hash, mechanical fact-ID/block/numeric/name/modality/
  negation/copy checks. These checks do not prove semantic entailment.
- Simulated admission and atomic copy-on-write MemoryStore. Every offline result
  has `simulation_only=true`, `public_release=false`, cost=0. No production SQL
  or `item_processing` table is created.

## Live stages (manual workflow)

`.github/workflows/synthesis-live.yml` runs one reviewed stage per manual dispatch on an exact
`main` commit: `migration-trial` (item_processing DDL, rolled back), `migration-apply`,
`doctrine-load` (verified doctrine records), `control-live` (the A-06 control items from
`intelligence.items`, Gemini writer, write to `knowledge.*`). `live.py` keeps a cost ledger with a
$1 cap: a call is reserved from the request size before it is sent, charged from `usageMetadata`
after; missing usage or a transport error stops the run without retry, and nothing is written
after a stop. Stage A stores every would-be PUBLISH as `HOLD owner_review` with
`public_release=false`. The repository is public, so the workflow log prints counts, codes and
cost only — never source text, quotes or synthesis prose. Negation is stored as `predicate=not_*`,
`modality=announced` (dictionaries-v2). Resources used by the workflow live in `resources/`.

Not yet connected: primary-source fetching, topic chronology from past events, supply chain and
restrictions, company participant links, parent/participant events, `day-live`.
