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

## Second read-only SQL projection, current C3-10 shadow contract

Repeated read-only SQL projection on the same **25 real published synthesis IDs** verifies mandatory sections, source-date syntax, source/context URL HTTPS prefixes and whether any source is dated after the recorded publication date.

| **Projected** C3-10 decision | Count | Reason |
|---|---:|---|
| BLOCK | **2** | Missing source date (synthesis **489, 507**), E07 `invalid_source_date` |
| REVIEW | **23** | No trusted independent checker and independently verified retrieval provenance |
| PASS | **0** | Historical auto-admission cannot authorize a new editor PASS |

The query inspected all 25 rows and did not find non-HTTPS URL prefixes, future-dated source dates, or empty mandatory sections in this sample. However, the query does **not** implement every `core.review()` check (e.g., all embedded links, JSON contracts and claim spans). **These figures are projected structural outcomes, not observed Python `EditorVerdict` outputs.** Additional BLOCK reasons are possible when the full material is reviewed.

Local, user-facing evidence artifacts generated for this pilot: `BOHUN_C3-10_REAL25_shadow_projection_2026-11-07.csv` and `.md`; neither is committed to the public repo. No source bodies, passwords or private URLs were copied into this PR.

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


## Frozen 25-record execution protocol, follow-up 07.11.2026

**Status: NOT YET EXECUTED as Python.** A second **read-only SQL classification** of the exact 25 actual public rows reconfirmed 23 projected REVIEW for missing independent checker/provenance, and 2 projected BLOCK for missing source date (synthesis IDs 489 and 507). Neither SQL pass is `shadow_review(...)`, so no `EditorVerdict` is claimed.

Use branch `chatgpt/C3-10` **at revision `8210d816951f5e4833f854a56e90621bc7fa6595`** for a fully reproducible run; if HEAD is different, record it and compare code before presenting results. Use a local checkout with no credentials or external API calls for code execution.

**Step A: 116 exact offline unittests**, from the repo root:

```bash
PYTHONPATH=. python -B -m unittest discover -s collectors/editor -p 'test_*.py' -v
```

Success requires the console log to say `Ran 116 tests` and `OK` with zero failures/errors. Archive stdout/stderr privately, plus checkout SHA.

**Step B: export exact currently-published versions to a private local JSON file**, using read-only database access and no code changes. Capture no .env files or secrets. The following query intentionally pins the 25 synthesis IDs instead of using a moving "latest 25" window:

```sql
WITH selected AS (
 SELECT s.id, s.event_id, v.version, v.generator, a.published_at,
        v.headline, v.what_happened, v.context, v.policy_direction,
        v.bigger_picture, v.supply_chain, v.why_it_matters,
        v.sources, v.context_sources
 FROM knowledge.synthesis s
 JOIN knowledge.synthesis_version v
   ON v.synthesis_id = s.id AND v.version = s.active_version
 JOIN knowledge.synthesis_admission a
   ON a.synthesis_id = s.id AND a.version = s.active_version
 WHERE s.id IN (
     483,484,485,486,487,488,489,490,491,492,493,494,495,
     498,499,500,501,502,503,504,505,506,507,508,509)
   AND NOT s.is_hidden AND a.decision = 'PUBLISH' AND a.public_release
)
SELECT COALESCE(jsonb_agg(to_jsonb(selected) ORDER BY selected.id),
                '[]'::jsonb) AS shadow_snapshot
FROM selected;
```

Expect **exactly 25 rows**, no extras. Save the *value* of `shadow_snapshot` as a UTF-8 JSON **array**, not the SQL result wrapper, to `/private/bohun-c310-snapshot-25.json`. Save it OUTSIDE public GitHub/workspace folders; the snapshot contains copyright-protected published text. Check that the ID set and versions match the SQL preflight; a changed active version is a new sample requiring re-review.

**Step C: real Python shadow invocation**:

```bash
PYTHONPATH=. python -B -m collectors.editor.shadow \
  --snapshot /private/bohun-c310-snapshot-25.json \
  --report /private/bohun-c310-shadow-results.json
```

Record `count`, `decisions`, each `material_id`/`decision`/`reason_codes`, Python version, checkout SHA and exact command. Verify that `publication_authorized` is `false` and `offline_only` is `true` for all 25. Report only IDs/reason codes/aggregate metrics to public PR; no material body/captured article text, no credentials. No workflow runs, no API model calls, no writes to Supabase or site.

**Important:** the SQL-projected result is 23 REVIEW/2 BLOCK, but Python may find **additional** deterministic issues. Do not claim 23/2 from Python unless its actual output matches. If the private snapshot cannot be exported or tests cannot run, report the blocker, not a fabricated success.
