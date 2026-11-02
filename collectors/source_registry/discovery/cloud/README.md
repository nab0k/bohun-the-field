# Cloud execution adapter

## Existing Bohundefence application: shared OAuth and quota

The account permits one API application. Both consumers use the same existing
coordinator lease and operational Store; no second Worker or application is needed.
`runtime.oauth_mode="coordinator"` and `runtime.api_quota_mode="shared"` are required
for live cloud Inoreader input. This is opt-in: the existing collector workflow and
its default `sync.py` entry point have not been switched.

`shared.py` checkpoints each API reservation before sending it. Inoreader quota
headers reconcile both zones without refunding attempts or assuming an unused
100-call window. A missing/expired window admits one collector read probe; Discovery
waits for it, preserves twenty requests for the collector, and has a separate
twelve-attempt daily cap. Calls outside this coordinator cannot be prevented;
all automatic API consumers must be moved before claiming shared ownership.
Missing headers, unknown probe outcomes and lost checkpoint acknowledgements hold
execution. No quota override, automatic retry or silent paid upgrade exists.

The optional collector entry point is:

```sh
python -B -m collectors.inoreader.shared_runtime --config /private/runtime/config.json --allow-live --max-api-attempts 2 -- --max-pages 1 --max-items 12 --backfill-bodies
```

It restores the same journal, holds the coordinator lease throughout intake,
reuses the existing durable OAuth cache, and injects services into the existing
collector. Normalization, cycle IDs, batching, inserts and backfill SQL are unchanged.
The shared mode bounds API bodies to 32 MiB, total provider bytes to 64 MiB, API
attempts to 60 and the invocation to 900 seconds. The legacy entry point retains
its existing behavior. New shared calls do not follow redirects or retry 429.
`--max-api-attempts` lowers the bound for a manual pilot. Empty folders still
consume calls; the process stops before exceeding the bound.

Before cutover: review the release/config pins, preserve the operational journal,
wait for existing collector runs to finish, establish one refresh owner in the
coordinator and reconcile a fresh quota observation. Do not seed the same refresh
grant while the legacy collector is still refreshing it. The local read OAuth
session alone neither switches production nor admits write scope/pilots.

## Manual first pilot

`pilot` uses the existing coordinator, Store and Source Manager. It requires an
explicit private manifest and a separately reviewed hash, live/writer/collector/
delivery gates and the approved policy hash. It does not require or set the two
continuous `pilot_proofs` flags. Normal `once --apply` still requires both flags.
No pilot or schedule is enabled by these instructions.

The manifest has exactly these fields: `version: 1`, `stage: single|batch3`,
`deployment_id`, `code_sha256`, `policy_sha256`, `expires_at` (UTC, at most one day),
`max_attempts` (1 for single, 3 for batch3), and `sources` (exactly 1 or 3 distinct
objects containing `feed_url` and the existing manager `group`). Hashes match the
reviewed runtime; the manifest SHA256 covers its exact UTF-8 bytes. The private
workflow proposal writes sorted compact JSON, so its approved hash must cover that
serialization. A changed URL, folder, revision, expiry or policy needs a new review.

```sh
python3.13 -B -m collectors.source_registry.discovery.cloud.run validate --config /private/runtime/config.json --pilot-manifest /private/runtime/pilot.json --approved-manifest-sha256 REVIEWED_MANIFEST_SHA256 --approved-policy-sha256 REVIEWED_POLICY_SHA256
python3.13 -B -m collectors.source_registry.discovery.cloud.run pilot --allow-live --config /private/runtime/config.json --pilot-manifest /private/runtime/pilot.json --approved-manifest-sha256 REVIEWED_MANIFEST_SHA256 --approved-policy-sha256 REVIEWED_POLICY_SHA256
```

Validation performs no credential lookup, state creation, source I/O or DB access.
One invocation uses one lease and one shared source HTTP/byte/time budget, with at
most one attempt per normal cycle and at most three cycles. Daily limits remain
durable. A manifest+feed reservation cannot be reused after a restart or on a later
day. Unknown outcomes reconcile first; the remaining batch is never sent blindly.
The persisted `PILOT_RESULT` and public count summary confirm connections only,
never delivery or autonomous operation. A replay may confirm existing connections
with zero additions; that does not establish a fresh-source onboarding proof.

The private workflow proposal is not installed. Its Action pins reuse the reviewed
collector pins; optional delivery uses the same pinned psycopg version as that
collector. No dependency is installed by local tests. Deployment, dedicated OAuth,
bootstrap, real radar contract, pilot sources and schedule remain separate actions.

One existing Python discovery cycle on GitHub Actions, with a private authoritative
checkpoint in one SQLite-backed Cloudflare Durable Object. No import-time HTTP,
credential discovery, job dispatch, account setup or background process.

Current status: local implementation; deployment, real inputs, quota, API contracts
and single/batch proofs still require their own reviewed live scope. The example
configuration is disabled and deliberately fails validation until its empty pins,
endpoint and private runtime paths are filled. No migration of Supabase is involved.

## Components

- `run.py`: validates executable/policy pins before credentials, restores a fresh
  ephemeral local working copy, calls existing `Worker.cycle`, returns only counts.
- `state.py`: pinned public HTTPS, explicit runtime token provider, owner/revision
  validation and fatal poison after any uncertain response. No redirect/retry.
- Existing `Store` accepts `checkpoint=None` by default. With a callback, each local
  write transaction waits for a remote durable ACK before returning. Failed ACK
  poisons the Store with a `BaseException` that source backoff cannot swallow.
- `cloudflare/source-discovery/state-core.mjs`: atomic bootstrap/lease/CAS/checkpoint;
  `state-worker.mjs`: authenticated binding to one configured DO instance.

The SQLite file remains the same operational journal. It contains provenance,
cursors, policy assessment, attempts and receipts. It is not a second business master.
Never upload it, raw reports or capability-bearing radar URLs to public GitHub
artifacts, logs, cache, source control, website, or the analytics Worker.

## CLI and validation

From FIELD, using installed Python:

```sh
python3.13 -B -m collectors.source_registry.discovery.cloud.run --help
python3.13 -B -m unittest discover -s tests/source_registry/discovery/cloud -p test_cloud.py -v
node --test tests/source_registry/discovery/cloud/state-core.test.mjs
```

`validate --config /absolute/private/config.json` checks code/policy/runtime paths
without token lookup, HTTP, creating state, or reading DB credentials. Get the
executable hash locally by importing `code_hash()`; `code_pins()` lists included files.
The policy hash is SHA256 of the reviewed policy JSON. Config belongs outside Git.

Future live modes (not enabled by these instructions): `bootstrap` creates an empty
journal only when both explicit `--allow-live` and server `ALLOW_BOOTSTRAP=true`
are approved; it never runs source discovery. Turn server bootstrap off afterwards.
`once --allow-live` acquires/restores existing state. `--apply` additionally requires
the explicit approved policy hash, manager, delivery reader and both real pilot proofs.
Periodic execution never bootstraps or resets missing state. Use a fresh private
runtime directory for every Actions job; a stale local directory causes HOLD.

Only live modes look up `SOURCE_DISCOVERY_STATE_TOKEN`, after pins and permissions.
Live cloud Inoreader requires `runtime.oauth_mode: "coordinator"`; legacy `oauth_env`
is rejected before credential access. DB reading stays optional;
psycopg is not installed by this adapter. Existing collector writes materials.

## Failure and recovery

Snapshots are bounded at 2 MiB, stored in 64 KiB chunks with current/previous
revisions only. Client limits: 300 total state calls, 64 MiB request+response bytes,
900 seconds. Source request/quota limits are unchanged. Exceeding a limit stops the
run; there is no automatic pruning, replay or reset of counters. Actual Cloudflare
CPU/quota performance must be measured by a separately approved platform smoke.

No timeout-based lease stealing. An interrupted writer, CAS conflict or accepted
checkpoint with lost response leaves a lease that blocks another run. Verify the
old GitHub run has terminated, inspect its private checkpoint and unresolved
operations, then prepare a separately reviewed recovery. No recovery/rollback/delete
HTTP endpoint is exposed. Previous checkpoints are retained for inspection, not
automatic rollback of attempt/day counters. A lost release ACK can leave either a
released or held lease; the caller stops without any further source I/O.

The proposed workflow is kept outside `.github/workflows/`. It is manual-only and
disabled until deployment review; the proposed hourly minute23 schedule is commented.
Fill verified immutable action/code SHAs before activation. GitHub schedules can be
delayed, dropped, or disabled after public-repository inactivity. No hourly SLA is
claimed. No migration, route, secret or setting of the existing site/collector changes.

Python/Node tests use artificial transports and a local SQLite implementation of the
storage interface. They do not establish live Cloudflare behavior, publisher quality,
OAuth scope, actual subscription membership, Supabase delivery or ACTIVE status.

## Private OAuth lifecycle (local implementation only)

`oauth.py` provides a lease-scoped access token in job memory. The existing Worker
accepts an optional provider factory after its HTTP budget and durable API ledger
are constructed. Before an OAuth RPC, it reserves one possible upstream refresh and
128 KiB of bounded exchange bodies. A DO cache hit is conservatively counted too.
Known quota failures before the RPC remain safe partial results; uncertain replies
poison the client and stop without release/retry. Cached access is never returned
after lease loss or poison. Apply requires a `read write` token as well as existing
policy/pilot/delivery gates. An insufficient scope is never silently upgraded.

The same `DiscoveryState` DO stores credentials in a separate `oauth_credentials`
table. Operational Python snapshots/download/status never contain these records;
checkpoint restoration does not roll their generation back. Expiry uses a 60-second
margin. READY access is reused; expiry commits REFRESHING and waits for durability
before exactly one fixed HTTPS POST, then commits the new pair and expiry before
returning only access/expiry/scope plus lease fences. Unknown upstream outcome,
malformed tokens/scope/expiry, crash or lost durable ACK gives HOLD. No automatic
refresh retry, credential reset, lease stealing or recovery endpoint exists.

Future separately approved setup uses a dedicated Discovery OAuth client/grant,
not the working collector's grant. Only the DO receives these Cloudflare Secrets:
`INOREADER_DISCOVERY_CLIENT_ID`, `INOREADER_DISCOVERY_CLIENT_SECRET`, and the initial
`INOREADER_DISCOVERY_BOOTSTRAP_REFRESH_TOKEN`. Python/GitHub jobs receive only the
existing `SOURCE_DISCOVERY_STATE_TOKEN`. Examples contain no credential values.
Actual registration conditions, isolation, API scope/quota and Cloudflare behavior
remain unverified; no account/resource/secret has been created by this local code.

Credential bootstrap is distinct from operational bootstrap: an explicit
`StateClient.oauth_bootstrap()` under a current lease and server
`ALLOW_OAUTH_BOOTSTRAP=true` calls `/v1/oauth-bootstrap`. It seeds once from the DO
secret, never overwrites a record and never refreshes a token. There is deliberately
no automatic CLI/bootstrap fallback. Close the server flag after approved setup.
`/v1/oauth-access` requires the runtime bearer plus current owner/generation/revision.
Refresh transport accepts no caller URL, uses no redirect/retry, allows 16 KiB
response bodies and has a 15-second abort. Auth/body/token errors are redacted.

Unresolved REFRESHING blocks release and operational checkpoint writes; a restarted
job cannot obtain the held lease. Reconcile only after the old job has terminated
and the owner has reviewed the credential outcome. Whole-DO platform PITR also
restores credentials; it is not a safe automatic operational checkpoint rollback.

The code pin includes `collectors/inoreader/body_backfill.py` when that target
dependency exists. Current main has it; the older local execution line does not.
No collector file is replaced by this conditional pin. Main-based transfer must
preserve current sync/body bytes and revalidate dependency pins and tests.
