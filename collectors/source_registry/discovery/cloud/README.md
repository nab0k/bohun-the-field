# Cloud execution adapter

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
Inoreader OAuth is still handled by existing Worker with explicit mapping; rotation
causes HOLD, not an automatic shared-secret replacement. DB reading stays optional;
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
