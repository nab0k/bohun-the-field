# G1 · Closed company-profile integration (review branch only)

**Scope:** Read-only Supabase source through existing EXP-012 PgStore, private off-repo review snapshots, existing BOHUN site view model. No public release.

* The frozen review cohort has 20 existing Eurosatory discovery candidates: 12 `discovered`, 6 `candidate_existing` and 2 `hold`. Source-reported country is **not legal country**.
* The script `scripts/g1_readonly_cohort.py` reads the existing `companies` schema in an explicit read-only transaction. It fails on source/cohort/status drift, never creates a company, and refuses output within Git.
* Run only with approved private DB connection configuration. `python scripts/g1_readonly_cohort.py --private-out /ABSOLUTE/PRIVATE/cohort20.json`.
* The detailed 20-company internal VM generator and Accurision source pack currently live in the owner-facing G1 prototype package, not in the Git checkout. They are deliberately not checked into a public repository.
* Site implementation lives on `nab0k/bohundefense` branch `feature/g1-private-profile-review-20261022`. The `?g1_candidate=<id>` route is loopback-only and loads `/__g1_review/<id>.json` from a local review server; no public host can request it through the application.
* Local trial as of 2026-10-22: 20 internal VMs, 1 partially enriched Accurision model, other 19 source-only with missing fields, 0 QA approvals, 0 Supabase writes, 0 public releases. These are **not** 20 completed dossiers.
* Under no circumstances merge this review branch, run an automatic collector, upload internal VMs as public assets, expose DB credentials to the browser, or enable publication without separate owner approval.

Next: connect an owner-approved encrypted private evidence ledger and claim-level Supabase staging, then legal-identity QA, then a site publication-only export.
