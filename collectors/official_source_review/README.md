# UK / Canada / EU official-source review

This is an **offline-tested, review-only diagnostic prototype** for UK Find a Tender OCDS, CanadaBuys federal CSV and EU Funding & Tenders SEDIA grants. Not a production EXP-009 collector. No paid API, model, database access, website write, scheduler, or publication.

Run: `cd collectors/official_source_review && python -m unittest -v`.
No network on import. Manual opt-in: `python official_sources.py --live-read-only --source uk|ca_new|ca_open|eu`. A successful diagnostic **does not verify source relevance, completeness, rights, or semantic claims**.

The separate workflow `.github/workflows/exp009-uk-ca-eu-review.yml` supports **workflow_dispatch only**. Default input is `offline`; public HTTP calls require `live_read_only=true` and an explicit source. The workflow has `contents: read` permission, no secrets, no artifact upload, no Supabase client and no schedule. Only schema field names, counts, byte lengths and hashes enter logs. Public repository workflow logs are readable to other people. No source titles, sensitive locations, raw data or personal records should be logged.

Hard bounds: one UK hour and at most two pages; one Canada CSV <=16MB and <=50k rows; one EU EDF grants page. All fail or stop rather than claiming broad coverage. Canadian awards are intentionally excluded until real 2026 award CSV wire format is verified.

The existing `exp009.intake_batches` is still Inoreader-only. **Do not add sources or bypass constraints**, merge, schedule recurring jobs, run paid APIs, or publish unverified material. Treat every future candidate as `HOLD` until QA, source-rights clearance and explicit public_release.
