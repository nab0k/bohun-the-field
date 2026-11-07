# C3-10: complete 195-test editor verification at live-shadow PR HEAD (2026-11-07)

## Actually executed, reproducible local evidence

**Result: 195/195 passed, 0 errors, 0 failures, 0 skips, exit code 0.** The tests ran on **Python 3.13.5** with the real editor implementation (core.review and shadow.shadow_review), *not* a stand-in reviewer.

From the editor-only local checkout root:

    PYTHONPATH=. python -B -m unittest discover -s collectors/editor -t . -p 'test_*.py' -v

Observed result: **Ran 195 tests in 0.035s; OK**.

All **20 editor Python files** were fetched from GitHub branch chatgpt/C3-10 at PR HEAD **1a5a4823fab86c1fbb63e80c5997efbf5f787531**. Each exact local file's Git blob SHA was checked with git hash-object against the original GitHub SHA. SHA256 of the sorted JSON file-name / Git-blob-SHA manifest:

    51581d721d9c824ef29fca0823f7bcbd4207ed9c1a31b879ce2e2c938139dbc4

New verified file Git blob SHAs:

- collectors/editor/live_shadow.py: 81a13b7690daf0806f14276d481e94714ee361b3
- collectors/editor/test_live_shadow.py: 19300486e2d2eee1e4338a2b3ecd601140516b97
- .github/workflows/editor-shadow.yml: 5253b1a9d33ab3dee39d70bfc36999a012a116bb

The 12 new live_shadow tests are included in the 195 and now ran against the **real editor**, superseding earlier 12/12 with a test-only stub. The full verbose test log is privately available as BOHUN_C3-10_195_FULL_TEST_2026-11-07.log; no private article, candidate body, or log is copied to public GitHub.

**Scope:** isolated Python unit tests only. They do not run a real database connection, GitHub Actions, Gemini/Groq checker, production publisher or CAS.

## Separate real Supabase data inspection (read-only SELECT)

The connected SQL inspection confirmed the actual knowledge.synthesis and synthesis_version schema. At query time on 2026-11-07, **107** latest generated versions were in the last 24 hours. The newest **30** nonhidden synthesis versions contained **35** manifest sources; **4 missing source dates**, 0 non-HTTPS URLs, 0 missing mandatory sections and 0 missing writer identity.

This is a database structural **SQL preflight**, not an actual Python live_shadow.run_shadow connected to PostgreSQL. No live Python PASS/REVIEW/BLOCK distribution can be claimed. The manual workflow exists only in Draft PR #196 and has not been run.

## Next gate

Claude reviews the PR, then owner explicitly authorizes reviewed merge into main. Only thereafter the owner can dispatch exactly **one** manual Actions run on the reviewed main SHA, lookback 24h / limit 30. The reader must never change publication decisions. Further independent P1 acquisition, independent semantic model evaluation and C3-04 atomic CAS remain open.

**Release: NO**. PR still Draft; no workflow dispatch, no main merge, no live DB write, no website change, no provider calls or expenses.
