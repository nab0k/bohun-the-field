# C3-10: verified offline tests and checker-probe feasibility (2026-11-07)

## Executed on actual editor code, not a simulated suite

The **16 Python source and test files** under `collectors/editor/` were recovered from the GitHub connector at commit `92fe64341bf3ca9b4594df03e557eecb8d5becb5`. Their **16/16 Git blob SHA values were independently checked against the actual Python files** by `git hash-object`; all matched at the time of test. Combined SHA256 of the sorted JSON file-to-blob manifest is `be3a95ab8ce861af42399bc3bb9f6938067f7986e3957c8d2286b8117a055f4e`.

From the local editor-only checkout root (Python 3.13.5), executed:

```bash
PYTHONPATH=. python -B -m unittest discover \
  -s collectors/editor -t . -p 'test_*.py' -v
```

**Actual result: `Ran 168 tests in 0.046s; OK`, exit code 0, no failures, errors or skips.** This closes the prior full-suite *execution gap*, not the release-acceptance criteria. The full repository, workflow, publisher CAS and source services were **not** run. The private log is retained as `EDITOR_168_FULL_TEST_2026-11-07.log` in the chat artifacts, not uploaded to a public workflow.

## Actual 25-record checker-probe preflight, ZERO provider calls

Used the previously privately exported JSON of 25 real published synthesis versions, SHA256 `ad9f333cc05ddf23ff390604d33242f8c0ef149dfd078ddc08d179aae60e52ee`, with `shadow._prepare()` followed by `checker_probe.prepare_probe()` for each record. **25/25 returned `probe_has_no_spans`**, because the frozen publishable-material export contains source URLs and final synthesis text, **but not the actual saved source article capture excerpts/offsets**. This does *not* imply the underlying Supabase `intelligence.items` lacks captured articles: it means the exact evidence packets were missing from the local export.

A **separate synthetic** two-stage dry run constructed a fake evidence span, prepared three provisional claim segments and validated a fabricated reply that labelled all three `supported`. The validator correctly kept `provider_identity_attested=false`, `semantic_assessment_trusted=false`, `publication_authorized=false` and `offline_only=true`. This does **not** demonstrate semantic truth or Groq/Gemini model quality. **0 model requests, 0 secrets, 0 billing and 0 publisher writes**.

The resulting private IDs/status JSON is `BOHUN_C3-10_PROBE_25_OFFLINE_2026-11-07.json`. No full narrative or article capture text has been committed to GitHub.

## P1 source provenance: public corroboration but no new attested origin

From the prior SELECT-only exact `event_document`/`intelligence.items.url` comparison: **0/8 P1/primary URLs** had an exact event-linked saved item, while **30/30 P2/report URLs** did. This discrepancy is not repaired by the successful unittest suite.

Public publisher documents support specific candidate replacements but do **not** establish HTTP redirect chains or trusted saved capture provenance:

- **syn-491:** official Canadian government announcement exists at [ISED Canada.ca](https://www.canada.ca/en/innovation-science-economic-development/news/2026/11/government-of-canada-announces-new-president-for-the-canadian-space-agency.html), dated November 4, 2026; the current P1 URL has a different department path and is not the saved event document.
- **syn-493:** [Militär Aktuell article](https://militaeraktuell.at/afrika-experte-gerald-hainzl-ein-neuer-wettlauf-um-afrika/) has `afrika` in its saved slug, not the manifest's `africa`.
- **syn-503:** [The War Zone article](https://www.twz.com/sea/marine-corps-declares-it-needs-nine-more-amphibious-warships-to-carry-out-its-missions) is at the saved P2 URL, not the claimed P1 slug.
- **syn-505:** [Defence Industry Europe article](https://defence-industry.eu/castelion-shows-blackbeard-hypersonic-missile-mounted-on-a-u-s-navy-f-a-18f-super-hornet/) uses a different punctuation/slug, without proving independent P1.
- Cases 486 and 498 have apparent wrong-topic P1 URLs; 489 and 507 use homepages. See `UNMATCHED_P1_TRIAGE_2026-11-07.md`. No URL/primary class was changed.

## Next check, not yet performed

1. **Read-only, privacy-protected source-capture snapshot** for the already frozen 25 records: exact event_id/item_id, original source URL, publication date, stored source body and SHA256, plus a separately verified provenance attestation (only if independently acquired). Never push copyrighted source text into a public PR.
2. Generate actual bounded `EvidenceSpan` quote offsets for the correct event-linked documents. **Never** set `retrieval_verified=true` from a database row or self-reported model claim.
3. Only then perform a separately authorized, budget-limited provider test with independent model identity, semantic review on a labeled holdout and C3-04 publisher atomic CAS. No authorization to publish has been obtained.

**Conclusion:** the 168 editor unittest pass milestone is achieved; the factual-source and publisher integration milestones are not. PR remains Draft; `main`, `.github/workflows`, production Supabase data, provider secrets and site were not changed.
