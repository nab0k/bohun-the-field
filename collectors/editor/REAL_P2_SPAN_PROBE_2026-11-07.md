# C3-10: real P2 article-excerpt probe over 25 syntheses (2026-11-07)

**Actual executed offline pilot.** The previous `25/25 probe_has_no_spans` result was a *missing-evidence-input* issue, not absence of stored captures. This step extracted **one literal source excerpt per synthesis** from the corresponding event-linked `intelligence.items.raw_data.content_capture.text` through **read-only** Supabase SELECT and attached it as an `EvidenceSpan` to the exact saved P2 URL source in `shadow._prepare()`.

## Verified database baseline

- Frozen syntheses: IDs **483–495** and **498–509**, 25 current public versions.
- **30** unique manifest P2 URLs have exact `knowledge.event_document → intelligence.items.url` matches and a saved article body. **25/25** syntheses have at least one matched P2.
- **8** P1 URLs still do **not** have a matching event-linked capture; no P1 source was corrected, relabeled or promoted.
- Some P2 URLs are linked to multiple `intelligence.items` rows. These duplications occur in syntheses **487** (2 item rows), **495** (3), and **502** (2); duplicated URL rows have identical stored-body hashes and do **not** establish independent sourcing.

## Executed evidence-packet probe

For each synthesis, selected a single linked P2 `item_id` deterministically and retrieved **the first 115 Unicode characters of the stored article body**, together with the MD5 of that exact excerpt and the URL lookup digest. The private Python script checks both digests, matches the lookup to the exact URL in the previously exported frozen material, and constructs an `EvidenceSpan` with a deterministic ID.

The quote is only a **prefix sample**, not an automatically selected supporting span, nor an independently verified publisher retrieval. Other manifest sources remain unspanned, and **every** source is `retrieval_verified=False`.

`checker_probe.prepare_probe()` and `core.review(..., assessment=None, published=())` were executed locally on all **25** full published materials and their newly span-bearing evidence:

| Result | Actual |
|---|---:|
| Successful private checker request packets | **25/25** |
| Event-linked P2 excerpts attached | **25** |
| Provisional sentence segments to assess | **349** (11–19 per material) |
| `PASS` | **0** |
| `REVIEW` | **23** |
| `BLOCK` | **2**: syn-489 v2, syn-507 v1 |
| Independent model/provider calls | **0** |
| Publication authorizations | **0** |

Both BLOCK records preserve `invalid_payload_date`, `invalid_source_date`. All 25 remain without independent semantic assessment or trusted source retrieval.

## Reproducibility and privacy

A private JSON report contains only material IDs, hashes, provisional-claim counts and decisions: SHA256 **`e89e0b00dfada1c10d1b1241acfea2e68ffce824ba8440ef4d45c9c4e6cf9809`**.

The private request packets containing synthesis sentences and source excerpts: SHA256 **`7a19204070197b4a493b75e3fff341087fcafb58a153d77466e877fc025b0c32`**. **These are private chat artifacts, NOT committed to public GitHub**, since they include copyrighted excerpts. Source-text MD5 values are only local consistency checks, never an authenticated origin attestation. No test workflow, Gemini/Groq API, production publisher, `.github/workflows`, `main` or database write was invoked.

## Next acceptance gate

Build a **human-labeled 5-material mini-holdout** with atomic statements and relevant 100–200-character exact source offsets, including numerical/negation/modality errors. Only after that, evaluate one explicitly budget-limited and author-independent Groq/Gemini checker against known ground truth. A successful response is not a `SemanticAssessment` attestation or permission to publish. C3-04 CAS and primary-source provenance remain separate blockers.

**Release decision: NO.** This milestone establishes **source-excerpt to checker-packet plumbing**, not truth, atomic claim coverage, independent P1 provenance, semantic model quality or production integration.
