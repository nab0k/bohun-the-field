# Companies collector (EXP-012)

Autonomous, bounded collection of defence and dual-use companies (world without RU/CN, down to
components and services). Design: BOHUN-local `docs/experiments/EXP-012-companies-coverage-audit/DESIGN_v0.md`.

Status at G3 (18.10.2026): offline implementation. Only the local SQLite store works. The
PostgreSQL migration `db/companies/001_companies_schema.sql` is **not applied**. Nothing is written to
Supabase or Notion. The workflow runs tests only and has no schedule.

## Flow

```
source adapter --fetch(cursor, limit)--> Record(payload, mentions)
  -> observation (idempotent: source + key + content hash)
  -> resolve: identifier -> exact name + country -> new company; unclear cases -> match_candidate
  -> apply: aliases, identifiers, signals (layer), categories by codes (with basis), supply links
  -> RU/CN rule -> layer recompute -> source_state cursor + run metrics
```

- **Sources (no keys):**
  - `usaspending_recipients` and `usaspending_subawards` — DoD primes and their subcontractors;
  - `ted` — EU defence award winners;
  - `prozorro` — Ukrainian defence award winners;
  - GLEIF — LEI and ultimate parent;
  - `legacy_notion` — one-time import of the 3429 Notion cards as leads.
- **Never merged automatically.** Same-name companies in the same country with conflicting identifiers
  stay separate, and so do the two Lockheed Martin UEIs; they go to `match_candidate`.
- **RU/CN.** If the legal country or the GLEIF ultimate parent is RU or CN, the status becomes `excluded`
  and the record is kept. Partial ownership is shown, not excluded: `company_ownership_link` plus the
  flag `OWNERSHIP_LINK_CN` / `OWNERSHIP_LINK_RU`. HK/MO get the flag `CN_SAR`. A GLEIF reporting
  exception gives `ownership_check = OPEN:GLEIF_<reason>`.
- **Ukraine.** No street address, city or coordinates. This is enforced in code and by a CHECK constraint.
- **Layer A–D.** Computed from signals by TAXONOMY v0. A legacy card's own discovery signal is always D.
  The old Notion assessment is kept as the `legacy_assessment` signal for review only and does not set
  the layer.
- **Source failure.** A failing source rolls back its own writes and keeps its cursor. After 3 failures
  in a row it is disabled; the other sources keep running.

## Data contract for consumers (Codex, showcase, analytics)

Read these tables:
- `company` (`status`, `layer`, `flags`, `ownership_check`);
- `company_identifier`;
- `company_category` (`axis`, `value`, `basis`);
- `company_relation` (`supplies`, `parent_of`);
- `company_ownership_link`;
- `signal`.

Rules for consumers:
- Public output only follows D02: a registry legal binding plus at least one defence or dual-use source.
- `excluded` rows and `match_candidate` rows are never shown publicly.
- `observation.payload` is the raw source record and is not public.

## Run locally

```bash
python3 -m unittest discover -s tests/companies -t .
python3 scripts/companies-collect.py --db /tmp/companies.sqlite3 --sources ted,prozorro --limit 10 --report
```

The `--legacy-snapshot <pages_dir>` option reads the private Notion snapshot. It runs locally only:
never in CI, and the snapshot must never be committed.

## Known limits (G3)

- No PostgreSQL/Supabase store yet (TODO v1 T08, needs separate consent).
- Several UEIs of one group are not yet linked by `parent_of`; that needs USAspending parent data or GLEIF.
- USAspending sub-award amounts are unreliable and are ignored.
- TED often gives no registry number.
- Wartime Ukrainian defence procurement is mostly absent from Prozorro.
- The capability mapping is TAXONOMY v0 (POST-MVP REVIEW).
