# C3-10: controlled independent Groq shadow pilot (2026-11-08)

After the real 30-material read-only shadow run (26 REVIEW, 4 BLOCK, 0 PASS; 195 editor unit tests passed on GitHub Actions), this PR adds `live_factcheck.py`, `test_live_factcheck.py` and a **manual-only** `.github/workflows/editor-independent-shadow.yml`. It is a restricted **external-model observation** and never a publish/admission gate.

## Inputs and trust

- A verified server-side PostgreSQL `READ ONLY` transaction loads at most 20 newest candidate rows (lookback 24h or 72h) and exact `event_document -> intelligence.items.url` matches of saved **P2** capture texts.
- Requires an exact matching manifest/P2 publication date. Does **not** use unlinked P1 URLs, promote missing P1 dates, label an article's opening as semantic evidence, or treat a historical `PUBLISH` as proof of truth.
- Candidate's generator must explicitly contain `|gemini`; **Groq-authored syntheses are skipped**, as the checker must not be its own author. The independent model is Groq's `openai/gpt-oss-20b` (separate model provider and family).
- Up to **two** independent API call attempts per manual run; each checks up to **five provisional, not guaranteed atomic** text segments, with up to **ten 200-character** lexical passages from exactly linked P2 captures. These are **private source snippets and private draft sentences**, transmitted to Groq over HTTPS **only when the owner explicitly opts in**. No full articles are sent. Groq's account usage and billing settings remain outside this code's control.
- Input payload limited to **12,000 bytes**, output `max_completion_tokens=1600`; documented production model pricing as of this note: $0.075 per million input tokens / $0.30 per million output tokens, so a two-call upper estimate is under one cent at those rates. This is **not an API-enforced monetary limit**; the owner must maintain external provider billing limits. Errors/uncertain network responses are **never retried**.
- The model outputs JSON; `checker_probe.validate_untrusted_reply` checks claim IDs, outcome values, citations and coverage **within the selected five segments**. The result is explicitly **UNTRUSTED**: partial coverage, unauthenticated publisher retrieval and no validated real-world truth. It is never promoted into `SemanticAssessment` or `publication_authorized`.

## No-leak output

Only material IDs/versions, source item IDs, selected claim/excerpt counts, provisional supported/contradicted/insufficient counts, attempted call counts, and safe error codes are logged/saved. No prompt, API key, article text, claim text, untrusted model prose, tokens or headers are logged. Report retention seven days. `GROQ_API_KEY` and `SUPABASE_DB_PASSWORD` are read from GitHub Secrets during the single opt-in step and are never displayed. No tokens are accessed by the assistant.

## Workflow release boundary

Workflow only starts manually from `main`, when `github.actor == nab0k`, when the exact 40-digit reviewed main SHA matches, and when `send_excerpts_to_groq=yes`. It runs full editor unit tests before loading secrets. It is **not scheduled** and does not modify writer, database, publisher, site, or C3-04 CAS. If the Groq key is absent, the workflow stops before the DB connection and before any model call. No new secret was created.

**No verified independent API call has yet occurred** while this documentation is written. A green unit test does not prove the model's semantic accuracy, cover all claims or validate P1. The independent test result must be evaluated separately against a versioned holdout.