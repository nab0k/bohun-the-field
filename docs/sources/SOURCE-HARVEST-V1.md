# BOHUN Source Harvest v1

Status: local implementation for review. It has not been pushed, run in GitHub Actions, or connected to production.

This is a one-shot, metadata-only assessment of candidate source channels. It does not subscribe feeds, alter Inoreader, write to Supabase, fetch article bodies, publish results, or promote candidates into `data/sources/registry.csv`.

## Safety contract

- Inoreader access is read-only: `subscription/list`, `tag/list`, and optional bounded `stream/contents` only.
- Stored item fields are URL, canonical URL, title, dates, author, categories, annotation length/hash, and outbound links already present in feed metadata.
- Article pages are not fetched. Gemini is off by default and receives title, publisher, and date only when explicitly enabled with a cap no greater than USD 1.00.
- Public HTTP discovery observes robots.txt, waits at least two seconds per domain, uses at most twelve workers, and times out after fifteen seconds.
- Outputs are candidate metrics for owner review, not an approved registry.

## Local offline validation

```bash
python -m pip install -r requirements-source-harvest.txt
python -m unittest discover -s tests/source_harvest -v
```

The fixture suite covers URL normalization, provenance-preserving deduplication, HTML feed discovery, RSS/Atom/news-sitemap parsing, undated items, pagination bounds, clustering, relevance explanations, yield math, budget enforcement, Inoreader 429 continuation, and forbidden-operation guards.

## Local small-run evidence — 2026-10-14

A bounded public-network run used `max_candidates=50`, `days_back=30`, `use_inoreader=false`, and `use_gemini=false`:

- 50 deterministic candidates; 28 valid feeds; 22 `pending-no-rss`;
- discovery methods: 15 input feed URLs, 5 HTML alternates, 2 section alternates, and 6 common paths;
- 590 metadata-only items; 6 channels with at least 14 days of visible history;
- 4 preliminary `keep_candidate`, 24 `probation`, and no archive decisions because the remaining channels had low-confidence history;
- Inoreader calls 0, Gemini spend USD 0, production writes 0.

The result lives in a temporary local directory and is not committed. It is evidence that the bounded path runs end to end, not a final registry or a substitute for the full GitHub run.

## Follow-up after the small run — 2026-10-14

The small run showed that most public feeds expose short history (6 of 28 channels had at least 14 days). Changes:

- every validated feed now keeps a row in `channel-yield.csv`, including feeds with zero items (`status=no_items`);
- each channel gets `history_bucket` (`none`, `<7d`, `7-13d`, `14-29d`, `>=30d`); the report and the `HARVEST_HISTORY` notice show the distribution;
- the Inoreader history fallback is on in the first full run: subscribed feeds with less than 14 days of visible history, shortest first, at most `inoreader_call_budget` calls in total, read-only `stream/contents` with titles/links/dates only, bounded to `days_back`. It uses the subscription's own stream id, records `history_source=feed+inoreader`, and recomputes coverage. A failing stream is skipped; HTTP 429 stops the fallback and the harvest continues;
- discovery stops starting new candidates after 80 minutes; the rest are listed as `deadline_not_attempted`, not dropped;
- the Source Hunter artifact (run 38573173331, expires 2026-11-13) is downloaded as input I3;
- until the workflow is on the default branch, a commit to `data/sources/harvest-request.json` on this branch starts one run (a `resolve` job validates the parameters: `days_back` in 30/45/60, Gemini cap ≤ USD 1, Inoreader budget ≤ 100);
- top channels are shown with names and feed URLs.

## Small public-network run

Only after dependencies and network access are approved:

```bash
python scripts/source-harvest.py collect --work-dir output/source-harvest-small --max-candidates 50
python scripts/source-harvest.py discover --work-dir output/source-harvest-small
python scripts/source-harvest.py backlog --work-dir output/source-harvest-small --days-back 30
python scripts/source-harvest.py analyze --work-dir output/source-harvest-small --days-back 30
python scripts/source-harvest.py report --work-dir output/source-harvest-small
```

## GitHub Actions

`.github/workflows/source-harvest.yml` has five bounded jobs and a final 30-day artifact. The workflow has read-only repository permissions. Inoreader requires both `use_inoreader=true` and `quota_confirmed=true`. Gemini remains disabled in the initial run.

The workflow deliberately does not commit or push its results. A commit, push, PR, or merge needs the owner's separate approval under the repository working rules. After review, selected CSV/JSON outputs may be copied to `docs/sources/harvest-<run_id>/` in a separately approved change.
