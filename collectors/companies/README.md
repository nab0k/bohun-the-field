
## Fact linking (C2‑03)

## Pilot 30+10

## Auto‑record new companies (C2‑05)

The **Auto‑record** module adds new company entries from raw observations without requiring manual data entry. It is idempotent – duplicate mentions are ignored.

* **Module** – `collectors/companies/auto_record.py` with function `auto_record_new_companies`.
* **Test** – `tests/collectors/companies/test_auto_record.py`.
* **Run** – `python -m scripts.auto_record_new_companies.py <observations.json>`.
* **Metrics** – `new_companies` counter in the `run` table reflects how many records were added.
 (C2‑04)

The **Pilot 30 + 10** script selects a pilot set of 30 existing companies and 10 candidate companies, runs the fact‑linking process for them and reports how many new `company_facts` rows were created.

* **Script** – `scripts/pilot_30_10.py`
* **Test** – `tests/collectors/companies/test_pilot_30_10.py`
* **Run**:
```bash
python -m scripts.pilot_30_10.py
```
The script is idempotent; repeated runs do not create duplicate links.

---


The **Fact linking** module connects factual entries (e.g. from `atlas/undersea.json`) to existing company records.

* **Table `company_facts`** – stores `(company_id, fact_id, source_url, confidence)`.
* **Method `store.add_fact_link`** – inserts a link idempotently.
* **`fact_linker.py`** – provides `load_facts()`, `match_fact()`, and `link_facts(store)` for batch processing.
* **Sanity script** – `scripts/run_fact_linker_test.py` runs basic checks without external dependencies.

Typical usage:
```python
from collectors.companies.store import SqliteStore
from collectors.companies.fact_linker import link_facts

store = SqliteStore()
link_facts(store)
```

See the experiment log `docs/experiments/EXP-###‑fact‑linking.md` for detailed results.

Autonomous, bounded collection of defence and dual-use companies (world without RU/CN, down to
components and services). Design: BOHUN-local `docs/experiments/EXP-012-companies-coverage-audit/DESIGN_v0.md`.

Status (18.10.2026): SQLite store for local runs and PgStore for the Supabase schema `companies`
(one trial run on PR #72, T08). Nothing is written to Notion. The workflow has no schedule; the collection
job runs only on the PR label `exp012-collect-trial`.

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
  - `atla` — Japan ATLA central procurement contracts (monthly xlsx, PDL1.0), identifier `JP_CORP`;
    paused: mod.go.jp answers 403 to the collector's User-Agent;
  - `edf` — European Defence Fund project participants (EU Funding & Tenders Search API, CC BY 4.0),
    identifier `EU_PIC`, flags `EDF_COORDINATOR`, `SME`, `ORG_TYPE:*`;
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

## Known limits (T08)

- Supabase holds only the staging schema `companies`; wider limits and a schedule are T09 (separate consent).
- Several UEIs of one group are linked by `parent_of` through the USAspending recipient profile; the profile
  and PSC-breakdown request formats follow the API docs and still need one live check.
- Layer A from procurement awards requires a defence buyer (`normalize.DEFENCE_BUYER`); other buyers give C with
  the flag `NON_DEFENCE_BUYER`. The buyer list is a starting list (TAXONOMY POST-MVP REVIEW).
- USAspending sub-award amounts are unreliable and are ignored.
- TED often gives no registry number.
- Wartime Ukrainian defence procurement is mostly absent from Prozorro.
- ATLA: item names are Japanese free text without a code, so no capability is assigned. Fuel, food and similar
  civil supplies are skipped, and so are governments (FMS). Trading houses are marked `TRADING_INTERMEDIARY`.
  Officer names and addresses are never stored. Not yet run live (needs consent).
- The capability mapping is TAXONOMY v0 (POST-MVP REVIEW).
