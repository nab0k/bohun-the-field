# Web Monitor v1.2: URL quality audit

This iteration adds **heuristic** URL classification, not verified article extraction. Candidate classes:
- `possible_article`: path contains an article-like section and slug, or a date path.
- `index`: homepage or common section listing.
- `review`: ambiguous candidate requiring review.
- `reject`: off-site URL or non-HTML asset (excluded).

**Safety:** `--apply` is explicitly rejected; no candidate URL is written to Supabase. The scheduled workflow remains dry-run. This prevents accidental publication of navigation links.

## How to test
Run `python collectors/web_monitor/monitor.py --report-candidates`. Review `CANDIDATE` lines and each `SOURCE ... quality=...` summary. A green run only indicates completion, not source coverage or verified content.

## Next adapter work
- DIU: identify public press/news endpoint and respect robots.txt; do not assume `/sitemap.xml` is parseable.
- DGA: identify a publication-specific endpoint and distinguish it from the parent ministry.
- EDA: identify public news listing and handle access restrictions without bypass.
- Audit DARPA, NATO and Canada URLs for false positives before any ingestion.
- After verification: fetch article metadata, dates, canonical URLs, deduplication and safe database writes.
