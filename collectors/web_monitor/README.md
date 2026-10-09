# Web Monitor v1: candidate discovery pilot

This is a **conservative pilot**, not a verified news extractor. The 12 sources are official-site seeds; individual news pages, API availability and sitemap formats have NOT been independently verified.

- Uses public HTTPS, robots.txt and a 0.5 second delay.
- Tries /sitemap.xml (direct urlset only) and the configured landing page.
- Finds up to 30 same-host URLs with news-like path tokens per source.
- Does **not** download article bodies, infer publication dates, or publish to the website.
- Dry-run by default. Workflow runs every 3 hours in dry-run mode; manual apply requires explicit checkbox.
- With --apply, writes URLs as `verification_status=unverified_candidate` into intelligence.items using existing Supabase secrets. Deduplicates by SHA-256 URL within each source.
- Returns nonzero when sources have no candidates. This is intentional: a green workflow must not falsely imply complete coverage.
- Robots unavailable = skip source (fail closed). Sitemap indexes and JS-only pages require individual adapters.
- Initial candidate discovery is **not** evidence that all pages are relevant, new, or accurately titled. Review candidates before enabling unattended database writes.
- Never enable automatic website publication for these candidate records.
- Existing Inoreader and TED collectors are unchanged.

Next: audit output, configure verified publication endpoints per source, handle sitemap indexes, add per-source freshness checks and structured metadata extraction, then enable scheduled apply.
