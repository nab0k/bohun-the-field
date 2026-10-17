# #55 proposed Supabase publication migration (NOT APPLIED)

This is additive and **not** permission to publish any record. No rows are seeded. Production Cloudflare API stays BLOCKED until rights, immutable source capture, reviewer identity, the actual item ID type, and server credentials are verified. The SQL assumes `intelligence.items.id` is UUID; **validate this against the #54 artifact before running**. A SHA-256 string provided by a reviewer must be checked against the captured original before approval; a CHECK constraint only validates its format.

Files: `docs/sql/20261013_publication_gate_up.sql` and `docs/sql/20261013_publication_gate_down.sql`.

Risks: (1) wrong FK type / schema privileges can fail migration; (2) invalid manual approval can publish false information if a future endpoint trusts it; (3) DROP loses review decisions; (4) country fields do not prove event geography; (5) the database alone does not prevent self-approval or approval edits. **Before application**, add immutable decision history, reviewer separation and one-way approved SELECT projection; confirm actual primary key, grants, rights and compliance. Do not grant anon or authenticated access. No credential or production settings changes in this proposal.

Rollback: export any release decision history; execute down script; check tables/permissions. Safe initial rollback only while empty. Test upgrade and rollback in a disposable clone before production.
