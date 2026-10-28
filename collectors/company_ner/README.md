# Saved news → native NER → private company candidates

This stage reads saved `intelligence.items.raw_data.content_capture` bodies and
the existing `companies` catalog. It never creates/merges companies, confirms
legal identity, infers deal roles, writes Notion, or publishes to the site.
`organization` output is deliberately unverified: the measured native run also
returned countries, vessels, products and programmes in this class.

## Durable state

Review-only SQL: `db/companies/company_ner_storage_candidate.sql`. Three new
private tables owned by the existing `exp009_owner` in the existing schema;
no changes to INPUT29 jobs, runtime RPC, old intake, catalog data, schedules or
public permissions. Installation temporarily enables SET to that owner using
the backend's existing ADMIN membership, then removes only the temporary
self-grant. The original supabase_admin membership remains unchanged.

- `company_ner_cache`: one exact body/model/schema/adapter computation,
  `pending → running → complete|hold`. A claim commits before inference.
- `company_ner_inputs`: separate immutable source versions (including holds for
  invalid input). Duplicate bodies share a computation, not a source identity.
- `company_ner_links`: immutable candidate packets per source, catalog snapshot
  and linker implementation. Includes empty results so unmatched sources do not
  monopolize every run. A catalog change causes re-linking without inference.

Exact text, model revision, schema, extraction parameters and executable adapter
hashes determine computation identity. Linker/catalog/normalization code hashes
determine the matching version. Existing completed results are not rewritten.
On connection loss around COMMIT the invocation stops. Completed caches can be
read next time; abandoned `running` work becomes HOLD after ten minutes, never
an automatic repeat. Ordinary inference failures preserve safe reason codes.
The summary field `model_calls` counts adapter attempts, including failures
before inference; `completed` counts validated saved outputs. Replay also uses
the adapter interface, so its count must not be reported as new model calls.

Intake handles up to50 rows/tick, inference up to50 unique texts/tick, linking
up to50 sources/tick (default12). The catalog bound is5000; crossing it fails
explicitly instead of silently omitting companies. Scale this after measurement.

## Running after separately reviewed storage/deployment

The repository does not install the model or provision access on import/run.
Required: the existing Ubuntu24.04/Python3.13 CPU environment and already
acquired model from the successful native trial; `native-pins.json` preserves
the same46 package versions and five model-file hashes. No dependencies or
model files have been newly downloaded for this implementation.

The parent needs `psycopg[binary]==3.3.6` and `psutil` (the latter is in the
native lock). The child uses pinned GLiNER2.1.0 and the pinned
`fastino/gliner2.5-multi-v1` revision. It receives only text, no DB credentials;
network is disabled, all package versions/model hashes are checked, CPU uses
two threads, the parent enforces180s and a sampled3GiB RSS cap. Native source
code is used as supplied; application adapters live in this module.

`acquisition-lock.json` reuses the full measured download/wheel SHA lock.
`bootstrap.py` reuses its checked acquisition/native-integrity functions.
The manual-only `.github/workflows/company-ner.yml` prepares an ephemeral CPU
environment and runs at most12 extractions, reading the existing database secret
only in the processing step. It requires an exact reviewed main SHA and has no
schedule, artifacts or source-data logging. Code integrity is checked before
and after the processing step. The new per-text child adapter has not yet had
a live cloud inference test; no new installation/download has happened locally.

Supply a backend PostgreSQL DSN only through an environment variable, with
`sslmode=verify-full` and `sslrootcert`. A reviewed backend identity must have
read access to the selected source/catalog columns and access to these private
state tables. The owner-fix candidate grants the existing postgres backend only
SELECT/INSERT/UPDATE on the new cache and SELECT/INSERT on new inputs/links.
It creates no roles, no permanent owner membership and no schema CREATE grant.
The backend already bypasses RLS; frontend/API roles retain no access to these
tables. Do not expose private state through API roles to make access work.

```sh
python -B scripts/company-ner.py --run \
  --model-python /private/runtime/venv/bin/python \
  --model-dir /private/runtime/model --limit 12
```

Default secret variable name: `COMPANY_NER_DATABASE_URL`. Never put its value
in a command line, source file or workflow log. CLI stdout contains only counts;
failures stop without printing database exceptions/source payloads. `--run`
is required. The script installs nothing and applies no SQL. The proposed
GitHub entry point builds this DSN only in process memory from the existing
`SUPABASE_DB_PASSWORD`, using the same project/backend identity as Inoreader;
it creates no new credential; only the reviewed new-table backend grants are added. This identity is privileged: the
bounded source/catalog reads and private-state writes are enforced by this
reviewed code. A dedicated lower-privilege identity is a later improvement.

## Verification and limits

Unit/process-boundary tests: `tests/test_company_ner.py` and
`tests/test_company_ner_native.py`. The process tests use a fake child and do
not prove native model quality or the new Linux adapter's live behavior.

Real PostgreSQL tests: `tests/test_company_ner_postgres.py`; they accept only a
private local Unix-socket disposable `bohun_ner_test` database. No network or
production DSN is accepted. They cover restart, concurrent claims, unknown
commit outcomes, source/catalog changes, malformed bodies/spans and RLS.

Private end-to-end replay:12 saved source rows,11 original GLiNER outputs,
3751 real catalog rows; the same four candidate links. A second tick performs
zero extractions and zero re-linkings. Replay is not a new model generation.

SQL was exercised on disposable PostgreSQL17, not applied to Supabase.
This code is not deployed or scheduled. Source completeness remains UNKNOWN;
same-name retrieval does not establish the legal entity or analytic conclusion.

## Verified TLS root and safe startup diagnostics

The pooler chain uses Supabase Root2021 CA, which is not in the ordinary OS
trust bundle. The workflow supplies the bundled public
`supabase-prod-ca-2021.crt` while retaining `sslmode=verify-full` and hostname
verification. PEM SHA256:
`700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`.

Official download configuration:
https://github.com/supabase/supabase/blob/845ed5a1e3e8e39f8eef41b2875be4e7a9b8bdfc/apps/studio/hooks/custom-content/custom-content.json
Download URL:
https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt
The certificate is public, not an authentication credential. Do not replace it
with a certificate learned only from an unverified database handshake.

A credential-free OpenSSL PostgreSQL STARTTLS check failed with the OS bundle
and passed certificate+hostname verification with this official CA. This
confirms the TLS configuration repair, not database authentication or inference.
The first cloud run stopped with empty state tables; its generic error did not
record an underlying category. The root mismatch is independently reproduced;
a subsequent cloud run is needed to rule out additional startup problems.

The CLI now reports only phase/category, an allowlisted exception class and a
bounded SQLSTATE. Raw exceptions, DSNs, credentials and source rows are never
printed. It still stops without an automatic retry. CLI tests execute a real
entrypoint with a fake connection failure to verify both the diagnostic and
non-disclosure.
