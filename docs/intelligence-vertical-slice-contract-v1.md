# Bohun Defence Intelligence: Vertical Slice Contract v1
Owner approved 2026-10-11. REVIEW ONLY. This document defines a development contract, not an assertion that the pipeline is live.

## Product
One Intelligence Map, three synchronized views: LEFT Feed (Source Update, Intelligence Brief, Intelligence Analysis); CENTER Map (vetted geo role, no invented coordinates); RIGHT Dossier (entity, event, claim evidence and timeline). Search and layers in separate drawer; Light Minimal and far-zoom Night Side remain. On mobile use accessible drawer/bottom sheet; no fabricated cards or company markers.

## Source -> Evidence -> Claim -> Event -> Analysis -> Publication
- Source: stable source_id, original_url, publisher, fetched_at UTC, content_sha256, published_at (or unknown), rights/reuse, source_family, raw source version.
- Evidence: evidence_id, source_id, content_sha256, exact excerpt and offset/locator, extraction_version, archival reference. Resolver verifies original excerpt, NOT just valid evidence_id.
- Claim: claim_id, predicate/subject/object with literal/units, linked evidence_ids, attribution, epistemic_status OBSERVED/PUBLISHER_REPORTED/ANALYSIS/HYPOTHESIS, independent validation SUPPORTS/REFUTES/NOT_ENOUGH_INFO.
- Event: event_id, event_type (announcement/procurement/grant/financing/participation/etc.), actor entity IDs and explicit roles, stage, source/event/valid dates, dedup cluster, geo_role (HQ/VENUE/BUYER/PROGRAMME/COUNTRY_ONLY/NONE); never infer contract awards or locations.
- Analysis: analysis_id, input claim/event IDs with frozen versions, Gemini model + prompt/schema version, evidence-linked interpretation, limitations and alternative explanations; do not treat AI-generated assertions as sources.
- Publication: publication_id, type SOURCE_UPDATE/INTELLIGENCE_BRIEF/INTELLIGENCE_ANALYSIS, version, citations, qa_status HOLD/APPROVE/REJECT, rights review, reviewer or independent calibrated QA identity, published_at only after release gate.

## Gemini
Use existing working gemini-3.5-flash-lite; supplied with bounded original evidence fragments and linked existing KB records. Generate *structured candidates*, claim-by-claim with evidence_id references, never model-authored exact quotes as proof. No further paid calls without fresh owner permission. No source-index publication by implication: even Source Update needs source authenticity, rights, metadata and publication policy. All AI-derived candidate cards stay internal HOLD until QA08 independently accepts.

## MVP acceptance test
Test three *real* source packets: Frequentis official material, AU-F35/Ferra original documents, TED 699594-2026 official notice once Proc04 supplies it. Per fixture: store source+hash; check resolver and exact evidence; return typed claims; validate schema; QA label and reasons; show internal candidate in feed/map/right dossier linked to same entity/event IDs. No asserted APPROVE by merely passing tests. If TED unavailable, explicitly BLOCKED, never substitute a synthetic notice and claim 3/3 real fixtures.

## Implementation guardrails
Use existing approved Supabase + Cloudflare/D1 architecture and resolve source of truth explicitly; no fresh database or dual master. Preserve public site and contact. Branch-only, no production deploy, no secrets in logs/front end. Reuse existing M1 UI and Engineering09 evidence code. Document screenshots desktop/mobile, CI logs, fixture outputs, zero public writes. Release requires HQ01 approval after QA08 signoff.

## Work handoffs
02: 10 item-level source manifests, real RSS/API independent of exhausted Inoreader Zone1.
03: accepted legal entity IDs for Frequentis, Ferra, Lockheed Martin AU and two-country company export.
04: official TED primary document with stable ID, dates, exact evidence and notice stage.
05/06/07: compatible typed grant, financing and event records; hold unsupported relationships.
08: validator and claim-level adjudications, privacy/rights and release criteria.
09: branch implementing full contract and three-pane internal view with tests and screenshots.
01: release gate, schema and workstream reconciliation, blocker ledger.
