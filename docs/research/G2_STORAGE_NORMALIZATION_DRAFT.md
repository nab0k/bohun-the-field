# BOHUN G2 storage & normalization design (DRAFT)

Status: design only; no migrations, no Supabase changes. Based on successful G1, G2, G2.1, G2.2, G2.3. Revise BORME key constraints after G2.4.

## Evidence vs assumptions
- CDTI 2026: 1,069 records, 925 distinct nonempty NIF values; NIF + project title unique across that one snapshot (G2.2). All seven tested fields populated. **Not proof of cross-snapshot stability**.
- BORME 2024-05-29: `data.sumario.diario[].seccion[].item[]` has `identificador`, `titulo`, `url_html`, `url_xml`, `url_pdf` in inspected examples (G2.3). Uniqueness not yet measured.
- ARES Czech record has `ico`, `obchodniJmeno`, `sidlo`, etc. (G2); no population-wide validation.
- Document is not equivalent to company, and a publication can describe multiple events.

## Logical entities
1. `source_system`: source_code PK (CDTI, BORME, ARES), source_url, jurisdiction, terms_review_status.
2. `ingestion_run`: run_id UUID PK, source_code FK, started_at, completed_at, status, source_period, record_count, error_class. No credentials or sensitive HTTP headers.
3. `organization`: organization_id UUID PK, country_code, legal_name, created_at, updated_at. Do not use mutable name as PK.
4. `organization_identifier`: identifier_id UUID PK, organization_id FK, country_code, scheme (ES_NIF/CZ_ICO), normalized_value, validation_status, provenance. Conditional UNIQUE(country_code, scheme, normalized_value) **only after jurisdiction-specific normalization/validation**. Restrict access to raw identifiers as needed.
5. `source_document`: document_id UUID PK, source_code FK, external_id nullable, issue_date nullable, title nullable, canonical_url nullable, first_seen_at, last_seen_at, payload_fingerprint nullable. Proposed UNIQUE(source_code, external_id) WHERE external_id IS NOT NULL, **defer enforcement pending G2.4 and multi-issue checks**.
6. `funding_project`: project_id UUID PK, source_code FK, organization_id FK nullable, project_title, decision_date, budget_amount NUMERIC nullable, contribution_amount NUMERIC nullable, currency_code nullable, sector_code nullable, region_code nullable, first_seen_at, last_seen_at.
7. `project_source_key`: project_id FK, source_code FK, candidate_key_version, candidate_key_digest, first_seen_at, last_seen_at; UNIQUE(source_code, candidate_key_version, candidate_key_digest) **only as a guarded candidate after longitudinal validation**. Keep collisions reviewable. Do not treat NIF + title as permanent ID.
8. `corporate_event`: event_id UUID PK, document_id FK, event_type nullable, occurred_at nullable, extracted_at, extraction_method, review_status. Multiple events may link to one document.
9. `entity_mention`: mention_id UUID PK, document_id FK, organization_id FK nullable, mention_role, confidence, review_status; do not auto-link on name alone.
10. `source_observation`: observation_id UUID PK, run_id FK, entity_kind, entity_id nullable, source_record_locator (access-controlled), content_digest, observed_at, change_kind. Raw payload retention is a separate privacy/legal decision.

## Source mappings
CDTI:
- `NIFRazonSocial` → organization_identifier candidate (ES_NIF); `RazonSocial` → legal_name.
- `TituloProyecto` → funding_project.project_title; `FechaAprobacionResolucion` → decision_date.
- `Presupuesto` and `AportacionCDTI` → parsed NUMERIC; parse locale, separators, missing values; **do not assume currency without checking source documentation**.
- `CNAE` → sector_code; `CCAA`, `Provincia`, `Localidad`, `CodigoPostal` → structured geographic attributes with provenance, not geocoordinates.
- `UltimaFechaActualizacion` → source_modified_at; not the same as ingestion time.
BORME:
- `data.sumario.metadatos.fecha_publicacion` → issue_date.
- `diario[].seccion[].item[].identificador` → source_document.external_id candidate.
- `titulo`, `url_html`, `url_xml`, `url_pdf` → document metadata; `url_pdf` is an object, not a URL string.
- Corporate events require subsequent XML/HTML parsing, classification and entity linking; not inferred from the summary alone.
ARES:
- `ico` → CZ_ICO candidate, `obchodniJmeno` → organization. Historical aliases and addresses must not create separate entities.

## Normalization and upsert safety
- Normalize source values deterministically, version every transformation, keep source provenance.
- Identifier normalization is jurisdiction-specific; preserve leading zeros, validate before matching.
- Normalize text using Unicode NFKC, trim, collapse whitespace, casefold for comparison; preserve original display text separately.
- Dates: explicit source formats and UTC where time exists. No invented timezone for date-only values.
- Amounts: parse into NUMERIC with decimal precision; report failures, do not silently coerce.
- Dedup: exact verified IDs first; composite candidate keys are hints, not automatic destructive merges.
- Reprocessing must be idempotent; maintain observation history and a review queue for collisions.
- Restrict sensitive fields and raw payloads; no raw records in diagnostic artifacts.
- No website publication before source licensing, provenance, quality and privacy review.

## Validation gates
1. G2.4 confirms BORME ID completeness and within-issue uniqueness.
2. Compare multiple BORME issue dates for cross-issue ID reuse.
3. Compare CDTI snapshots over time for project title changes and composite-key stability.
4. Validate ES_NIF and CZ_ICO formats and organization linking.
5. Only then propose actual Supabase SQL migrations in a separate reviewed PR.
