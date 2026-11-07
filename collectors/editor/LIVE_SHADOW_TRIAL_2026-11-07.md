# C3-10: first live-data shadow integration slice (2026-11-07)

## Scope and owner authorization

On 07.11.2026 the owner explicitly authorized infrastructure changes and a working shadow-mode trial. This change stays on Draft PR #196, feature branch chatgpt/C3-10. It does NOT alter main, change the production synthesis/knowledge workflow, trigger Actions, call external models, publish, or write to Supabase. The workflow's first run has NOT been executed. Manual workflow_dispatch only works once a reviewed workflow exists on the default branch.

## Live database baseline: actually read

SELECT-only connected Supabase queries were executed against real knowledge.synthesis and knowledge.synthesis_version on 07.11.2026 (latest version per material, sorted by version creation time). The first lookback check returned **107 latest versions in the prior 24 hours**. The query shape was validated against actual schema.

The precise planned integration SELECT with lookback_hours=24 and limit=30 was independently executed read-only through the connected SQL tool: **30/30 well-shaped records**, all fifteen expected fields, first syn-556 v2, last syn-534 v2 ordered by creation timestamp. Aggregate **live SQL preflight**:

| Selected field / issue | Count |
|---|---:|
| Fresh material versions selected | **30** |
| Manifest source entries | **35** |
| Materials with at least one source | **30** |
| Missing source-date entries | **4** (four distinct materials) |
| Non-HTTPS source URLs | **0** |
| Missing mandatory headline, what_happened or why_it_matters | **0** |
| Missing generator identity | **0** |
| Future version timestamps | **0** |

These are **database query results**, NOT a real execution of the new Python run_shadow() against a live PostgreSQL connection. No live-editor PASS/REVIEW/BLOCK distribution is claimed. The missing dates are expected to trigger fail-closed handling but must be verified by running real Python.

## Code and workflow now prepared in the Draft PR

- collectors/editor/live_shadow.py: a bounded parameterized SELECT of newest nonhidden latest synthesis versions, including candidates without a public admission, passed to the existing shadow.shadow_review(). It never trusts stored PUBLISH.
- Before any SELECT, the Python connection executes SET TRANSACTION READ ONLY and verifies SHOW transaction_read_only = on; if not, it aborts. CLI always rolls back and closes without commit. The already pinned-CA collectors.synthesis.live.connect() is reused. No publisher or external model is called.
- For unpublished versions the output field published_at is taken from synthesis_version.created_at **solely as an as-of date** for offline structural inspection, not as a claim of actual publication.
- Max 30 rows / 168 hours. Output is IDs, reason codes and counts only, with publication_authorized=false, model_calls=0, database_writes=0 and independent_fact_check_performed=false. These are not factual/editorial trust attestations.
- collectors/editor/test_live_shadow.py contains **12** new synthetic read-only DB adapter tests: transaction guard, SQL parameters, bounds, malformed sources, no raw text in report, and empty window.
- .github/workflows/editor-shadow.yml is a new **manual-only** workflow, owner actor nab0k, exact 40-character reviewed main commit SHA, first attempt only, fixed lookback/limit choices, editor tests before credentials, pinned driver and metadata artifact retention 7 days. It has contents:read only, no schedule, no publication permissions and no Gemini/Groq API keys.

## Tests, provenance and limitations

1. Python 3.13.5 py_compile of both new Python files passed locally.
2. **12/12 synthetic SQL-contract tests passed locally**, with an explicitly **test-only stand-in** shadow_review in place of the actual editor code. This validates the new adapter contract but does NOT verify real-editor runtime verdicts. Full **195/195** tests (historical 183 + new 12) have not been executed on the PR HEAD.
3. Exact GitHub blob SHA values agree with locally tested files:
   - live_shadow.py: 81a13b7690daf0806f14276d481e94714ee361b3
   - test_live_shadow.py: 19300486e2d2eee1e4338a2b3ecd601140516b97
   - editor-shadow.yml: 5253b1a9d33ab3dee39d70bfc36999a012a116bb
4. The workflow YAML was parsed locally; the GitHub Actions workflow was NOT launched. The prior **183/183** editor tests are a historical result from the preceding code revision.
5. The available connected GitHub tools do not provide a workflow_dispatch action. This manual workflow does not exist on default main yet, so it cannot be run there until an owner-reviewed merge. We did not bypass the main-branch revision guard.

## Next acceptance gate

Run all 195 editor tests against a checkout of this PR HEAD with the real core.py and shadow.py, review and merge the manual workflow, then initiate ONE Actions run with exact reviewed main SHA, lookback_hours=24, limit=30. Inspect its metadata-only artifact. Only after a verified actual run should recurrence, independent source checking or C3-04 publishing-admission integration be considered.

**Release: NO.** This is a reviewed branch integration candidate with a real live SQL preflight, not an activated production editorial gate.
