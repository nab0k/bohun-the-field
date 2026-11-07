# C3-10: local event-to-source lineage preflight (2026-11-07)

## Purpose

Bridge the read-only `knowledge.event_document` -> `intelligence.items` linkage to an already-frozen synthesis `PublishCandidate`/`EvidenceBundle`. This is an *offline local-integrity check*, not an independent retrieval or editorial approval.

### Actual real-data reconnaissance, SELECT-only

An exact-URL comparison across the frozen 25 publicly released synthesis versions (IDs 483–495 and 498–509) returned:

| Measured property | Count |
|---|---:|
| Syntheses inspected | 25 |
| Manifest source links | **38** |
| Source links with a matching `event_document` item whose `intelligence.items.url` equals the manifest URL | **30 / 38** |
| Exact matches with a captured article body of at least 100 characters | **30 / 30** |
| Exact matches with matching source publication day | **30 / 30** |
| Links without an exact matching event-document item URL | **8 / 38** |
| Syntheses where *every* manifest source had an exact event-document URL match | **17 / 25** |

The eight syntheses with at least one unmatched source URL are **486, 489, 491, 493, 498, 503, 505 and 507**, one unmatched link each. Absence of an exact URL match **does not prove an incorrect story**: redirects, canonical URL changes or independent links may explain it, but those cases must be investigated rather than silently matched by domain/title.

These counts reflect only database state at query time, not an executed 25-record `lineage_adapter` replay, nor retrieval verification. The previous 25-record shadow run remains **23 REVIEW / 2 BLOCK / 0 PASS** and is a structural review, not factual accuracy.

### Implemented

- `lineage_adapter.py`: pure Python `bind_event_document_captures(candidate, evidence, source_bindings, items, links, quotes)`.
- Requires exact `source_id → item_id` binding, exactly one linked saved item per manifest source, and an `event_document` relation to **the same event ID** as both candidate and evidence.
- Requires exact matching item URL and source publication day. Uses saved `content_capture.text` and SHA-256 integrity via `capture_adapter.attach_local_captures` to attach maximum 200-character, offset-bound spans; fails closed on duplicates, missing relations, altered content or offsets.
- Never calls a database, network, Gemini/Groq, secrets, or production publisher. Never changes `retrieval_verified` to `True`, never emits `SemanticAssessment`, never performs semantic entailment checking, never authorizes publication.
- Strict exact matches are intentionally conservative and will not silently auto-reconcile the eight unresolved links.

### Test provenance

`test_lineage_adapter.py` adds **16 synthetic regression cases**; they were run locally against the exact GitHub blob bytes along with the previous 13 `test_capture_adapter.py` cases. Local command:

```bash
PYTHONPATH=. python -B -m unittest discover -s collectors/editor -p 'test_*.py' -v
```

Only the two test files were present in that local working directory, thus the local discovery result was **29 / 29 OK**, **not 145 / 145**. Local `git hash-object` SHA matched the committed GitHub blobs: `lineage_adapter.py` = `0a8ba545166f66aae838cccbd17db63832f162e4`; `test_lineage_adapter.py` = `c11a11dbe50edf9bf781025df0067c49b488c2fa`. The repo branch now contains **145 written test cases** (116 previously reported passed by Antigravity, 13 previous capture tests, 16 new lineage tests). **The full 145-test suite has NOT been run at this HEAD**.

### Next integration gate

1. Build a protected read-only exporter of `event_document` + saved `intelligence.items.content_capture` with immutable URL/date/item ID/text-hash, without secrets or source body in public logs.
2. Resolve the eight URL mismatches using verified canonical/redirect metadata and manual review. Never rely on hostname similarity or an LLM-provided verification flag.
3. Independently retrieve/verify publisher provenance in a separate trusted process and pin its attestation to the exact content/evidence/policy hashes.
4. Then add independent claim-level Groq/Gemini checking with source/span citations, zero-budget local fixtures first, provider calls only after explicit allowed configuration. Benchmark on the owner-approved labeled corpus and integrate publisher CAS (C3-04).

**Release decision: NO.** Shadow and lineage modules are offline-only. PR remains Draft; no `main`, `.github/workflows`, site, source data or production publisher changed.
