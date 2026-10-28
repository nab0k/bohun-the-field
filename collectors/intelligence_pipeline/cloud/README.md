# INPUT27: opt-in cloud adapters

Local implementation candidate. **Not deployed; SQL execution and live model /
Notion acceptance have not run.** These implement the existing Worker ports;
they do not collect a second source feed, enable a schedule or publish content.
The parallel AUTO07 top-level adapters are preserved and are not selected by
this runner. Select one reviewed runtime before deployment.

- `gemini.py`: Standard `gemini-3.5-flash-lite`, extract/analyze/review, one
  candidate, 6000 output tokens including thinking, no tools/fallback/retry.
  The adapter reserves the full documented input window rather than guessing
  tokens from characters. A wider `requires_dossier` contract fails before IO;
  this version does not silently downgrade it to the draft schema.
- `postgres.py`: parameterized private `exp009.cloud_rpc_v1` only; actual
  session/current role must be `bohun_analysis`, autocommit off, statement/lock
  timeouts, explicit TLS `verify-full` DSN. It never installs SQL or falls back
  to the admin password. Lost COMMIT responses stop processing.
- `notion.py`: new private child pages, exact material/version marker, bounded
  blocks and complete readback. No PATCH/DELETE/publish operation. Discovery
  is bounded; an incomplete scan refuses creation. Uncertain POSTs are never
  repeated blindly. Human changes cause CONFLICT, not overwrite.
- `runtime.py`: composition with the existing Worker, `max_attempts=1`, at most
  15 ticks / 2 deliveries, immutable intake read by UUID, no OAuth or web fetch.
- `http.py`: fixed HTTPS hosts, no redirects/proxies/retries, bounded response,
  fixed error codes; tokens, evidence and raw provider errors are never logged.

## Storage candidate / deployment prerequisites

The SQL is a separate local review artifact:
`BOHUN-local/docs/experiments/EXP-009-signal-editorial-automation/CLOUD_STORAGE_INPUT27.sql`.
It is **not** an applied migration. It adds private cloud scope/document/receipt/
job/usage/outbox tables under `exp009`; existing intake v1 stays intact. A
NOLOGIN runtime role receives one RPC grant. No scopes, passwords, LOGIN or
public grants are created. No public-release operation exists.

A separately approved administrator must provision ONE concrete scope with
expiry, model, cap **1,000,000 micro-USD total**, and at most 10 generations.
There is no midnight/run reset. Scope locks serialize reservations; unknown
requests retain their full charge. Expired model leases become HOLD; expired
delivery leases become UNKNOWN. Reconciliation does not imply a refund.
An acknowledged over-reservation usage freezes the scope before returning.

Before deployment: actual PostgreSQL/RLS/role/SQL and crash/concurrency acceptance;
exact source eligibility; secure limited DB/Notion credentials; exact parent
approval; reviewed commit and separate push/run permissions. The new workflow
is manual-only and may also require registration on the default branch; it is
not evidence of an active scheduler. A default-branch change requires approval.

## Input eligibility

`--batch-id` reads a retained intake snapshot and checks its hashes. Body
presence does **not** establish full text, rights, safe data or company identity.
Without an exact payload-hash decision receipt the Document is incomplete,
rights-unverified and sensitive; Worker holds it without a model call.
`BOHUN_SOURCE_DECISIONS_JSON`, if separately approved, contains bounded reviewed
receipts with complete/rights_verified/sensitive/source_stage/verified_entity_ids
and a nonempty basis. These are not LLM decisions and not publication approvals.
SQL binds submissions to retained intake text and metadata and retains receipts.
RSS capture's original UNKNOWN completeness remains unchanged.

This is an explicit outstanding launch gate. Do not hand-assemble news bodies
or invent receipts to call an offline test an autonomous MVP. A future approved
source adapter can provide eligible evidence through the same Store port.

## Entrypoint

`scripts/intelligence-cloud.py` needs `--allow-live`, reviewed exact clean SHA,
explicit scope and server-only environment:
`BOHUN_ANALYSIS_DSN`, `GEMINI_API_KEY`, `BOHUN_NOTION_TOKEN`,
`BOHUN_NOTION_PARENT_ID`, optional `BOHUN_SOURCE_DECISIONS_JSON`.
No values belong in Git, command arguments, artifacts or logs.

`--reconcile-key` uses Notion GET only and no Gemini key, then CAS-updates an
UNKNOWN private outbox after verified readback. Absence is still UNKNOWN.
Never reset its state to PENDING or create another page to recover a timeout.

Offline checks: `python -B -m unittest discover -s tests/intelligence_cloud`.
Tests use synthetic HTTP and RPC faults. They do **not** execute PostgreSQL,
prove real API access or replace a subsequent autonomous cloud acceptance run.
