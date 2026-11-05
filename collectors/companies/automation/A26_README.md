# A26 integration overlay — candidate, not deployed

Adds event_triggers.py and profile_bridge.py to the existing shared automation
package plus one integration test. Requires the final reviewed EXP016 package;
never copy verification/ into production. No imports from local-runtime, local
paths, pins or snapshot loaders remain in runtime. No new queue, SQL, transport,
keys, model, scheduler or Notion writer.

Caller supplies admitted canonical Trigger objects and shared.PostgresBackend
through SharedBackendLink with its existing approved RPC and complete reader.
Tests use fake RPC, not PostgreSQL. Deployment remains disabled until the shared
SQL, reader, canonical bindings and cloud proof are admitted. See A26_DEPLOYMENT.md.
