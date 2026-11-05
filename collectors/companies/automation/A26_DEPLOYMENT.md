# A26 — bounded deployment sequence

Status: local integration overlay ready; NOT_TRANSFERRED / NOT_DEPLOYED.
No cloud or SQL execution is authorized by this file. Actions below are for the
implementing agent; the owner is not expected to prepare data or run commands.

## Dependencies before transfer

1. EXP016 owner finishes and freezes backend RESULT/manifest/SQL candidate.
2. Recheck exact final shared contracts/checkpoint/postgres bytes against this
   SOURCE_MANIFEST, rerun this package's 14 integration cases if changed.
3. Compare a fresh GitHub main and target paths; select reviewed integration
   branch together with EXP016. Do not switch/reset the shared dirty clone.
4. Transfer only the five overlay files, review exact diff; commit/push/PR/merge
   and live DB actions remain separately controlled under repository AGENTS.md.

## Live readiness checks

The candidate SQL deliberately requires companies.automation_profile_read_v0(text[])
and companies.automation_source_read_v0(text,text,text,text). Merely installing
automation tables/RPC does NOT supply those readers or enable enrichment.
The profile function must deliver a complete admitted CardRead, review receipts
and authoritative field ownership. Do not substitute a single company row,
stale saved Notion export, relation URLs or empty defaults.

Existing pilot confirmed_companies reads knowledge.event_participant WHERE
link_status='confirmed'. Its company_id is NOT automatically assumed to be the
canonical companies.company ID. Check the actual crosswalk, identity evidence
and source hash before making a Trigger. A name, model output company list or
knowledge graph node ID is insufficient. Failed binding stays pending.

## First live acceptance — one event, one company

Choose one actually stored source observation with an admitted canonical company
binding. Freshly record its ID/origin/content hash, company version, full reader
receipt and code/policy manifest; do not reuse the historical Rheinmetall fixture
as a fresh live observation. No new model calls are required to prove enqueue.

With exact approved DB scope: read_current -> A26 prepare -> shared commit.
Verify at most ten section jobs plus one trigger observation, same transaction
with checkpoint; repeat produces zero new jobs/trigger rows. Record database
readback receipts, not just an adapter return value. Unknown outcome requires
lookup; no blind second commit. Pause if profile reader or ownership is missing.

This only proves event-to-queue. Enrichment acceptance additionally requires the
existing worker to read related records, retrieve genuine missing data, preserve
source provenance/manual fields, save HOLD claims and show a verified readback.
Model/provider and business writes need their exact approvals. It is a separate
criterion from enqueue; do not report autonomous enrichment after enqueue alone.

## Pipeline connection and rollback

Use committed source/observation IDs as the replayable input, outside publisher
SQL transactions. Do not add network company work inside publish_hold. Both
failed and successful deliveries need durable accounting in the shared backend.
A26 failure must not promote company QA or suppress the already working feed.

Do not enable a new schedule before the limited proof. Rollback first disables
the shared approved A26/enrichment scope; leave job/source/claim history intact
for reconciliation. Remove/revert only the reviewed A26 hook/code if necessary.
No DROP/TRUNCATE or rollback of unrelated publication/source changes.

## Release evidence

Final shared + A26 commit SHA, artifact hashes, exact targeted DB trial/activation
authorizations, fresh reader and binding receipts, one-event DB counts before/after,
repeat0, stop/recovery proof and existing feed health. The current offline test
fixture does not provide any of those live receipts.
