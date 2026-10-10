# Signal-led source expansion | 2026-10-10

**Principle:** Monitor signals through independent source families, not only company websites. Association directories supply entity leads, not verified news subscriptions.

The package contains `docs/sources/signal-led-source-seeds-20261010.csv` (**53 thematic seed channels**, many already in Top100 or existing feeds), `collectors/source_registry/signal_taxonomy_v1.json` (**14 signal types**) and offline consistency tests. This is **not 53 new or live feeds**.

## Sources to monitor

News: specialist defence media, state press agencies, corporate pressrooms, research institutes and think tank publications.

Structured primary records: tenders, procurement plans, contract awards, grant calls, R&D challenges, legislation, export restrictions, sanctions, budget and strategy documents. Prefer official APIs to feed autodiscovery.

Industry and resources: production capacity, expansion, shortages, critical minerals (titanium, tungsten, gallium, germanium, rare earths), refinery/recycling projects and export dependencies. Prioritize official materials datasets and company disclosures.

Events and networks: conferences, exhibitions, accelerator demo days, association industry days, publicly listed speaker programmes and exhibitors. Event participation is not proof of a defence contract.

Capital: fund portfolio updates, investment rounds, venture accelerators and investment agencies.

Forum/community observations remain leads, not facts without corroboration.

## Shared signal contract

`source_id`, `original_url`, `publisher`, `published_at`, `observed_at`, `jurisdiction`, `signal_type`, `canonical_entity_refs`, `event_date_or_deadline`, `description`, `evidence_class`, `confidence`, `review_status`, `licensing`.

## Coverage KPIs

Track separately:
- candidate publisher domains and channel endpoints
- active collectors confirmed to fetch items successfully
- number of sources supplying genuinely new dated items during the last 30 days (respect slower official cadence)
- signal categories and jurisdictions with active official coverage
- fresh, deduplicated, provenance-preserving items stored downstream

## Next implementation gates

1. Compare 53 signal-led seeds to Top100 and existing Inoreader subscription IDs; do not inflate unique source counts.
2. Prioritize official procurement, grants, regulations and critical material APIs. Implement read-only pilots and source-level recency checks.
3. Validate news/event/investment publication pages or RSS and connect only verified working endpoints.
4. Align event normalization and country tagging with Notion Sources Intelligence and existing Methodology v1.
5. Determine site canonical store (Supabase vs D1) before publishing verified signals.

Status: prepared taxonomy and candidate channels, not live ingestion.
