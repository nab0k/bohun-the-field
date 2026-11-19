# A1SD-02 source search (shadow)

Finds new defence-industry sources and admits them by rules into a shadow registry (Supabase schema
`source_discovery`). Nothing here reaches product tables, collection or Inoreader. BOHUN-local EXP-034
(cycles 1–5 on the owner's Mac, 18–19.11.2026) holds the design, decisions and results.

- `run_daily.py` — one bounded cycle: plan → search (Gemini with Google Search grounding; Tavily when `TAVILY_API_KEY`
  is set) → candidates → site check → admission → measure → write back. `--dry-run` reads and plans only.
- `plan.py` + `categories.json` — what to ask: themes of our base × country × language, source types, "who else wrote".
- `search_cycle.py` — collect/rank/caps, two engines with a two-failure switch-off, network outage recovery, measures.
- `admission.py` — 8 rules (version 0.5): accept / reject / hold / general outlet / disputed (only what a rule cannot decide).
- `observer.py`, `transport_ext.py`, `ready_modules.py` — robots, feeds (feedparser, feedsearch-crawler), language (Lingua).
- `codex_discovery/` — frozen copy of Codex's bounded transport (robots, 2 s per host, budgets, public IPs only).
- `pg_sync.py` — state in/out; reads our company websites, known publishers and recent events from the base.
- `migration_source_discovery.sql` — the schema (applied 19.11.2026 as `a1sd02_source_discovery_shadow_registry`).

Workflow `.github/workflows/source-search-daily.yml`: manual only, dry run by default; the daily schedule is a separate
owner decision. The repository is public: logs carry counts only.

Offline tests: `for t in collectors/source_registry/search/tests/test_*.py; do python -I "$t"; done`
