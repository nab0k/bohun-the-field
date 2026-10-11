# OpenAlex research metadata pilot (2026-10-11)

This is an **additional collection channel independent of Inoreader**, not an RSS feed.
Official API documentation: https://help.openalex.org/api/ and https://help.openalex.org/api/filtering/

- Scheduled daily at 06:15 UTC, manual dispatch supported.
- Searches six dual-use/defence-adjacent research phrases for the past 14 days.
- Deduplicates by OpenAlex work ID, retains DOI, source URL, journal/source, publication date and discovery query.
- Produces an Actions artifact `openalex-research-candidates`; **no database writes**.
- Keyword matches are **unverified candidates** and must not be described as confirmed defence research.
- A successful workflow means execution succeeded, not that all queries returned results.
- Check `unique_candidates`, `errors`, duplicates, relevance and license before wiring to Supabase.
- Optional `OPENALEX_API_KEY` repository secret; if API limits or policy change, supply a valid key. Do not bypass rate limits.
- Publication-date filtering is not a complete incremental change feed; paid created/updated filters are a separate capability.

Next: inspect the artifact and only then implement provenance-preserving DB ingestion.
