# C3-10 read-only shadow rollout: first 25 published syntheses (07.11.2026)

**Scope:** card C3-10, `chatgpt/C3-10` draft PR only. No write to Supabase, site, secrets, model APIs or workflows. No production release. The experiment is **not** an end-to-end publication test.

## Actual read-only reconnaissance, not simulated stories

A read-only Supabase SQL check examined the 25 most recently **publicly released, non-hidden** syntheses and their current `knowledge.synthesis_version`/`synthesis_admission` rows, plus linkage to existing source captures. At query time there were **159** public, non-hidden syntheses. The snapshot IDs were 483–495 and 498–509 (25 rows); latest publication was 2026-11-06 around 21:31 UTC. No full published texts or captured articles were committed to GitHub.

| Real-data finding | Count |
|---|---:|
| Active published material IDs examined | **25** |
| Headline + what_happened + why_it_matters non-empty | **25/25** |
| URLs in published sources manifests | **38**; all 38 use HTTPS prefix |
| Materials with a P0/P1 link in the published manifest | **8/25** |
| Materials with **no** P0/P1 link in the published manifest | **17/25** |
| Materials with linked source captures at least 100 characters | **25/25** |
| Structured `knowledge.synthesis_claim` rows for these active versions | **0** |
| Context reference rows missing both `event_ids` and `span_id` attribution | **152/152** |
| Missing/blank source date in published manifests | **2** (syntheses **489** and **507**) |

**Interpretation:** this is a **metadata readiness preflight**, not 25 actual independent fact-check decisions. The old `auto-admission-v1` `PUBLISH` flag must **not** be imported as a trusted `SemanticAssessment`. With no trusted claim-level checker, none of these 25 is eligible for an automatic *new* editor PASS. On known contract fields, **2** would require a deterministic source-date block, and **23** need independent REVIEW. These are *projected readiness classes*, not Python `EditorVerdict` outputs. Published context strings and source captures alone do **not** verify context/event relevance or semantic entailment.

## Prepared safe adapter

`shadow.py` converts a **local** JSON snapshot into `PublishCandidate` + `EvidenceBundle` and calls real `core.review()` with `assessment=None`, no trusted retrieval flags and no publication access. It emits only material IDs, reason codes and counts. Input cannot promote sources to verified or fabricate a checker. `PASS` in the shadow path is treated as an error. `test_shadow.py` holds twelve synthetic regression cases. **The full Python shadow CLI has not yet been executed on these 25 fully exported records**, so do not label the counts above as its results.

Minimal read-only SQL snapshot (use export-to-local-file tooling; do not check raw text into git):

```sql
WITH selected AS (
  SELECT s.id, s.event_id, v.version, v.generator,
         a.published_at, v.headline, v.what_happened,
         v.context, v.policy_direction, v.bigger_picture,
         v.supply_chain, v.why_it_matters, v.sources,
         v.context_sources
  FROM knowledge.synthesis s
  JOIN knowledge.synthesis_version v
    ON v.synthesis_id = s.id AND v.version = s.active_version
  JOIN knowledge.synthesis_admission a
    ON a.synthesis_id = s.id AND a.version = s.active_version
  WHERE a.decision = 'PUBLISH'
    AND a.public_release AND NOT s.is_hidden
  ORDER BY a.published_at DESC NULLS LAST, s.id DESC
  LIMIT 25
)
SELECT COALESCE(jsonb_agg(to_jsonb(selected)
       ORDER BY selected.published_at DESC NULLS LAST, selected.id DESC),
       '[]'::jsonb) AS shadow_snapshot
FROM selected;
```

A local tool can save the JSON array under a private path outside the repository. From the repository root (without credentials or network activity):

```bash
PYTHONPATH=. python -B -m collectors.editor.shadow \
  --snapshot /private/bohun-shadow-snapshot.json \
  --report /private/bohun-shadow-report.json
```

The CLI does not download the feed, connect to Supabase, call Gemini/Groq, create PRs, write production tables or publish. It does not print source texts. **Do not run it from a workflow until it has been independently reviewed and the local-input contract verified.**

## Required before switching from shadow to an automatic editorial gate

1. Source adapter: authoritative `EvidenceSpan`/source publication date from the linked `intelligence.items` capture, with independently verified retrieval (not just `retrieval_verified=True` set by a caller).
2. Semantic checker adapter: author-independent, provider-verified `SemanticAssessment` matching exact content/evidence/policy pins, with claim coverage and source attribution. Its actual API calls must be budgeted and explicitly enabled.
3. Context lineage adapter: source/span and event links for `context_sources` lines; do not treat mere URLs as proof.
4. Full authoritative duplicate snapshot + transaction-bound publisher compare-and-swap (CAS), including version and hash at commit.
5. Frozen gold/holdout corpus with both real good materials and seeded errors; report denominators and uncertainty against the provisional 99/85/5/20 targets.
6. Independent review of adapter and all new tests. Only after these gates is a production switch proposal appropriate.

**Decision:** C3-10 offline unit suite had an externally reported 104/104 pass before this adapter. The 12 new `test_shadow.py` methods are **written but not yet executed in the full Python checkout**. PR remains Draft. No `.github/workflows`, `main`, `project-status`, source material database, nor live feed were modified by this C3-10 experiment.
