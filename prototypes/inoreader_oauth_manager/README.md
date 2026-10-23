# Inoreader OAuth Token Manager: offline prototype

**Status:** research prototype. Not deployed, not wired to scheduled collectors.

- `manager.py` defines storage and OAuth client interfaces and a synchronous token manager.
- `MemoryStore` and fake clients are test doubles, not a secure secret store.
- A per-process lock serializes local refresh calls only. **It does not protect multiple GitHub Actions runners or Edge Function instances.**
- A timeout after submitting a refresh request is ambiguous; the manager fails closed and requires recovery.
- If a rotated token cannot be saved, recovery requires a new authorization. No automatic retry.
- No real credentials, Supabase Vault access, HTTP calls, database migrations or GitHub Secrets changes.

Run: `python3 -m unittest discover -s prototypes/inoreader_oauth_manager -p 'test_*.py' -v`

## Before production integration
1. Implement a dedicated Vault adapter with least-privilege DB credentials and explicit authorization boundaries.
2. Implement **cross-process** locking and generation-based compare-and-swap in PostgreSQL.
3. Decide on access-token caching and provider refresh expiry using verified Inoreader semantics.
4. Verify Vault permissions and recovery procedures in a non-production environment.
5. Review concurrent scheduled workflows and disable duplicate refresh paths before rollout.
6. Perform a controlled live test only after explicit approval.

**Security note:** An application-level transaction cannot atomically commit an external OAuth refresh and a PostgreSQL write. The uncertainty window remains and requires operational recovery.
