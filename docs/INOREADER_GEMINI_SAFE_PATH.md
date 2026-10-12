# Inoreader → Gemini → QA → Supabase → site

Status: implementation started; publication remains disabled.

## Confirmed cause of metadata-only records

The Stream Contents endpoint can return article HTML in `items[].summary.content`. The existing Bohun collector discarded that field inside `normalize()` and deliberately wrote only title, URL, timestamps, folder, and origin metadata. The observed metadata-only Supabase records therefore do not prove an Inoreader API limitation.

The collector now preserves returned HTML, SHA-256, and visible text. It does not call this content “full text”: feeds may expose only an excerpt even when a body is present.

## Shortest safe flow

1. **Source / discovery.** Inoreader supplies item ID, feed identity, canonical URL, timestamp, folders, and any `summary.content` body.
2. **Raw capture.** Store the exact returned HTML and hash in isolated staging. Classify it as `METADATA_ONLY` or `BODY_PRESENT_COMPLETENESS_UNVERIFIED`.
3. **Original retrieval.** Fetch the canonical publisher URL when access, robots, and reuse conditions allow. Save bytes, retrieval time, media type, URL, and hash. Record an explicit failure reason otherwise.
4. **Evidence.** Create evidence only from exact spans found in a stored source version. Inoreader text may generate candidates, but it is not independently verified merely because the API returned it.
5. **Gemini candidate extraction.** Send bounded evidence blocks with immutable IDs. Require structured JSON; every claim must reference evidence IDs and reproduce exact source spans.
6. **Deterministic QA.** Code checks that each span exists byte-for-byte in the stored version, IDs resolve, dates and numbers match, required source classes are present, and safety restrictions pass. Unsupported output is rejected, not repaired by the model.
7. **Analyst QA.** Candidate remains `HOLD` until review. Translation and summaries are labelled model-generated and always retain links to original evidence.
8. **Publication.** Only a separate approved projection can reach the website. Staging roles have no public grants; `HOLD` and `REJECT` are excluded by construction.

## Gemini output contract (minimum)

```json
{
  "model_run_id": "immutable-id",
  "source_document_ids": ["document-id"],
  "language": "en",
  "translation": {"text": "...", "evidence_ids": ["evidence-id"]},
  "candidate_claims": [
    {
      "subject": "...",
      "predicate": "...",
      "object": {"value": "...", "datatype": "string", "unit": null},
      "evidence_ids": ["evidence-id"],
      "exact_spans": ["verbatim stored-source span"],
      "confidence": 0.0
    }
  ],
  "candidate_event": {"type": "...", "claim_ids": []},
  "uncertainties": [],
  "qa_status": "HOLD"
}
```

Gemini structured output constrains syntax, not truth. The validator, source policy, and human QA remain authoritative.

## Acceptance gates for the first live Inoreader item

- API response demonstrates whether `summary.content` exists for that specific feed.
- Raw API body and canonical publisher version have separate hashes and provenance.
- At least one candidate claim has an exact evidence span that the validator finds in stored bytes.
- A deliberately unsupported claim fails validation.
- Reprocessing creates no duplicate document or claim.
- Supabase readback succeeds from a fresh process.
- Website query returns zero rows while the candidate is `HOLD`.

Official references:

- Inoreader Stream Contents: https://www.inoreader.com/developers/stream-contents
- Inoreader Full content view: https://www.inoreader.com/blog/2022/11/how-to-take-advantage-of-the-full-content-view-in-inoreader.html
- Gemini structured output: https://ai.google.dev/gemini-api/docs/structured-output
