# Bohun Intelligence working model

This review-only vertical slice proves one complete, reproducible path through the
system. It does not write to production, call Gemini, deploy the public site, or
represent a QA approval.

## Proven path

`CORDIS XML -> raw_item -> exact evidence -> typed claim -> grant event -> HOLD publication candidate -> internal feed/dossier`

The source is the European Commission CORDIS XML record for SMART, grant
agreement 101289409. The original XML bytes are stored in an ephemeral SQLite
file using the existing D1-compatible schema.

## Run

```sh
python scripts/intelligence_live_pilot.py ingest
python scripts/intelligence_live_pilot.py ingest
python scripts/intelligence_live_pilot.py readback
python scripts/intelligence_live_pilot.py export
python scripts/intelligence_live_pilot.py validate
```

The first ingest must create one raw version. The second must create zero. The
readback command opens the database in a fresh process and verifies the stored
content hash. Export creates `contract-packet.json` and the internal UI feed.
Validate fails unless all IDs resolve, every evidence excerpt matches the stored
original at its locator, and every publication candidate remains fail-closed.

## Acceptance evidence

The workflow uploads:

- the SQLite staging database;
- ingest and restarted-process readback logs;
- the Source/Evidence/Claim/Event/Publication contract packet;
- contract validation results;
- desktop and mobile screenshots plus browser audit.

Expected contract counts are one source, one document, two exact evidence
records, two typed claims, one event, and one publication candidate. The
publication status must be `HOLD`, `public_release=false`, and `published_at=null`.

## Deliberate boundaries

- Coordinator country is context only; it is not an event venue or company pin.
- No company identity, procurement contract, ownership relation, or AI analysis
  is inferred.
- Rights, independent claim QA, and entity crosswalk remain explicit blockers.
- Supabase and public Cloudflare deployment are outside this slice.

The next source can enter the same contract only after it provides original
content, a stable source identity, exact evidence locators, and a rights decision.
