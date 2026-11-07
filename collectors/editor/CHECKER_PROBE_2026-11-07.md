# C3-10: provider-neutral checker probe (2026-11-07)

## What actually works

`checker_probe.py` builds a **private, offline, pinned request** from a `PublishCandidate` and `EvidenceBundle` containing literal saved source spans (maximum 200 Unicode characters). It covers headline, what_happened, context, policy_direction, bigger_picture, supply_chain, why_it_matters, body, baseline and falsifier, splitting into **provisional sentence segments** with deterministic `claim_id` values. These are **not independently verified atomic claims**.

Pins include exact `material_id`, type, version, writer model ID, full payload hash, evidence hash, policy version and policy hash. A `request_hash` is computed over the immutable request packet. To prevent unbounded content export, the module rejects excessive quote length, missing or duplicate source IDs/spans, empty packets, malformed or oversized visible sections, and more than 80 segments or 128 spans. The packet and quotations **must remain private**.

`validate_untrusted_reply(packet, reply)` requires every claim ID to be present exactly once and a valid `source_id/span_id` reference for `supported` or `contradicted` responses, rejects forged claim/span IDs, omitted/duplicated claims, unknown statuses, stale request hashes, nonstring citations and identical claimed writer/checker IDs. It never trusts self-reported `coverage_complete`, provider signatures or `publication_authorized` flags returned by an LLM. Its output contains only IDs/statuses/counters (never full article text), and hardcodes:

- `provider_identity_attested=false`
- `retrieval_independently_verified=false`
- `semantic_coverage_attested=false`
- `semantic_assessment_trusted=false`
- `publication_authorized=false`
- `offline_only=true`

**Important boundary:** structurally valid model-shaped JSON is **not a verified semantic assessment**, even when every claim says `supported`. The module deliberately does **not construct `SemanticAssessment` or call `core.review(assessment=...)`**. No production code may use its model ID or its `supported` statuses to set trust flags.

## Provider usage status

The module contains **no** HTTP/SDK integration with Groq or Gemini, no API keys, no live source fetch, no Supabase connection, no retries, no billing, no publication permission and no workflow. Model calls will require a separately reviewed and explicitly enabled trusted transport with provider-authenticated metadata, a cost/rate-limit budget, independent writer-checker separation, tight prompt/response privacy boundaries and integration with C3-04.

The eventual checker must compare claims against independently retrieved and attested source spans, not merely check whether numbers or quoted words are present. Sentence splitting here is intentionally **insufficient** to certify complete atomic claim coverage. No quality thresholds have been measured yet.

## Verified local tests

`test_checker_probe.py` adds **23 synthetic adversarial tests** including changed candidate/quote hashes, stale responses, hallucinated citations, missing/duplicate claims, same-model checker, model self-attestation, prompt-injection text and malformed list-valued citations.

Exact file Git blob SHAs:

- `checker_probe.py`: `8148d45223fe86f28d860f93e283b9b65d4e2429`
- `test_checker_probe.py`: `f0eecc644b99fdacc24b0690d5b91283c48b21f0`

Local Python invocation from the isolated editor staging checkout:

```bash
PYTHONPATH=. python -B -m unittest \
  collectors.editor.test_checker_probe \
  collectors.editor.test_lineage_adapter \
  collectors.editor.test_capture_adapter -q
```

**52/52 OK, 0 failures/errors** (23 checker probe, 16 lineage, 13 capture). The exact code/test bytes in GitHub match those used for this run. The entire branch now contains **168 written tests** (previous 145 + 23); **168/168 on this PR HEAD has NOT been run**. Past independent Antigravity run: 116/116 on the older branch before lineage and probe.

## Link reconciliation remains outstanding

See `UNMATCHED_P1_TRIAGE_2026-11-07.md`: exact event-document join yields **0/8** matches for published P1 sources, **30/30** for P2. Two are unrelated-topic URLs (486,498), two are homepages (489,507), three are same-article slug variants that must not count as independent P1 (493,503,505), one points at an official government announcement via an apparently wrong old department path (491), which may become usable only after independently capturing, verifying and attaching the correct official source.

## Next release gates

1. Authorized independent source retrieval, canonical URL/redirect confirmation, SHA and timestamp attestation; no trust promotion based on stored content hash alone.
2. Restore/fix the eight primary citations in a **separately authorized database migration**, including source-class review and rollback. Never invent P1 from a P2.
3. Independently operated Groq/Gemini semantic checker with provenance-pinned spans and a verified complete atomic claim inventory; benchmark on blind labeled corpus before adding a trusted assessment converter.
4. Authoritative duplicate index, immutable version and atomic C3-04 publisher compare-and-swap. Only explicit owner approval may change workflow, infrastructure or production publication.

**Current decision:** Prototype works offline; **NO** permission for automatic publishing. PR remains Draft. No production data or site was changed.
