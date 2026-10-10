# Source registry reconciliation, 2026-10-09

Inputs: Notion **INOREADER | Top 100 Defence Sources & Keyword Intelligence v1** and GitHub `global-source-candidates-20261009.csv` (84 candidates).

- Notion list: **100** candidates, imported into `notion-top100-reconciliation-20261009.csv`.
- **50** Notion URLs match a GitHub batch candidate by normalized hostname.
- **50** Notion URLs do not match that batch by hostname.
- Thus there are **134 distinct hostnames across these two lists** under this coarse method (84 + 100 - 50). This is **not** a deduplicated source count: a hostname can host multiple independent programmes, and one publisher may have several domains.
- Inoreader has **42 reported subscriptions**, but **their actual subscription identifiers and feed URLs have not been fetched or reconciled**. Therefore no row here is marked `active` solely on that claim.
- Web Monitor has 12 configured seeds; those have **not** been cross-referenced row by row.
- Notion multilingual keyword dictionary preserved at `collectors/inoreader/keyword_dictionary_top100.json`, **not enabled as monitoring rules**.
- Next: obtain a live Inoreader subscription export/API listing, compare canonical feed URLs and publisher identities, then verify each missing feed or web endpoint. Record method, feed URL, fetch evidence and last successful collection before promoting to active.
- Do not auto-import the 100 candidate homepage URLs or infer that RSS exists.
