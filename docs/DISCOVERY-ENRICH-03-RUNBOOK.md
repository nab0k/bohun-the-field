# DISCOVERY-ENRICH-03: primary-source capture and encrypted archive

**Status: review-only, no persistent remote evidence vault provisioned.**
Stacked on PR #80 / #79 / #78. No merge, company changes, promotions, Notion,
website publication, paid API, or database migration.

## Scope

The existing EXP-012 20-case evidence plan is used as a **strict URL allow-list**.
For every case, fetch at most one successful document from the predeclared
URLs, with fallback to the next URL on an ordinary error. HTTPS is mandatory,
cross-host redirects are rejected, request timeout 12 seconds and individual
response cap 5 MiB. HTML, plain text and PDF **bytes** may be preserved.
No PDF text extraction or legal-claim interpretation happens automatically.

Every available original byte stream is hashed with SHA-256. The private JSON
manifest stores which candidate/source URL it belongs to, fetched timestamp,
content type, content SHA-256, byte size and snapshot path. Every legal-status
and identity-verification field stays false. An unavailable or blocked source
is recorded as unavailable, never guessed.

## Encryption

The new `collectors.companies.evidence_archive` builds a bounded TAR
**in memory**, encrypts it using **OpenSSL CMS AES-256-GCM** to the **existing
public recipient certificate** `scripts/exp013_cms.crt`, and writes only
ciphertext + a nonsensitive aggregate checksum manifest to the encrypted
output directory. The recipient's private key stays with the owner and never
enters CI, Git, logs or Supabase.

AEAD authentication is tested with decrypt-after-encrypt and intentional
tampering. A plain source directory is used temporarily before encryption,
with 0700/0600 filesystem permissions. No plaintext file, company name,
source URL, LEI or legal identifier is uploaded as an Actions artifact.
The ciphertext is content-addressed, hash-checked and uploaded with a
**30-day retention limit** only when the reviewer authorizes the one-time
test job.

The existing `exp013_cms.crt` recipient certificate was created for EXP-013,
not evidence custody. Reuse here is a controlled trial of the cryptographic
mechanism, **not** a long-term cryptographic key policy. Before permanent
deployment the owner should approve a dedicated recipient certificate,
rotation/recovery, secure key backup and decryption smoke test on their Mac.

## Local invocation

```bash
python -m unittest discover -s tests/companies -t .
python -m collectors.companies.evidence_archive \
  --capture20 \
  --private-dir /ABSOLUTE/TEMP/private-evidence \
  --encrypted-dir /ABSOLUTE/ENCRYPTED-OUT \
  --cert scripts/exp013_cms.crt
```

Both private and encrypted output directories must be outside the checkout,
and separate. Plain files should be removed once an encrypted backup
has been verified recoverable by the owner.

## Honest acceptance boundary

A successful fetch establishes that **bytes** were retrieved from a specified
source URL at the recorded time and those bytes can be rehashed and decrypted.
It does NOT establish that an exhibitor equals a particular legal company,
or that a reported judicial liquidation is legally authoritative.
The Delfox claim requires original BODACC / tribunal evidence and a
separate legal-identity binding. Brand/group/HOLD remain unverified.

**No actual persistent encrypted storage has been configured** yet:
read-only SQL inspection on 22 Oct found zero existing Supabase Storage
buckets. A 30-day GitHub Actions artifact is NOT durable storage.
Do not declare operational evidence custody until a separate approved
persistent private ciphertext store is configured, proof of successful
download/decryption is recorded, and access/backup/retention is agreed.

Possible later implementation, not authorized by this PR:
an append-only ciphertext-only table or private object bucket in
existing Supabase, with RLS, no anon/authenticated grants, no decryption
keys on server and explicit rollback/disaster-recovery procedures.

## Actual public-source capture and encryption trial (2026-10-22)

A single label-gated read-only [GitHub Actions run 39707603739](https://github.com/nab0k/bohun-the-field/actions/runs/39707603739) completed successfully.

- 20 case source URL allow-list checked; **17 original source responses captured** and **3 unavailable**. Availability does NOT mean the page is a primary legal register, so none of these cases is automatically identity-verified.
- Raw source bytes and private URL/id manifest were packaged and encrypted with **OpenSSL CMS AES-256-GCM** to the existing owner public EXP-013 recipient certificate. Private key was not present in CI.
- Exactly 18 files in the encrypted TAR (17 snapshots plus private manifest). Ciphertext **2,867,765 bytes**, SHA-256 `5ae24a63ed89dd1fe4098c25feb8b6c6688319de8db5dd42c8f79ee1f0481c11`.
- Ciphertext and nonsensitive manifest only in artifact `bohun-evidence03-aesgcm-ciphertext` (artifact ID 12395289688, expires **2026-11-21**). No plaintext source URLs, names, LEIs or content uploaded.
- **138/138 offline tests passed** in the actual collection run; no Supabase connections, no company changes or publication.
- Actual recipient-owner private-key decryption has **NOT been tested**. Tests verified round-trip and authenticated tampering failure with an ephemeral TEST certificate.
- Temporary Actions label and one-time source capture job were removed after the successful run. No schedule or automated continuation.

## Design prepared for permanent ciphertext-only Supabase storage

A proposed **additive** `db/companies/003_encrypted_evidence_archive.sql` and `collectors.companies.evidence_vault_store` are included in PR #81. They create an access-restricted table in the **existing Supabase companies schema**, with RLS and no grants to anon/authenticated. The only retained document field is **CMS encrypted bytes**, plus ciphertext checksum, public cert fingerprint, count and timestamps. No legal names, URLs, extracted quotes, original text or private keys are stored as PostgreSQL columns. Upload requires `--approve-upload-ciphertext` and version 003 ledger preflight; it can only INSERT encrypted bytes, with idempotent replay.

**NOT DONE:** migration 003 has NOT been applied and the encrypted artifact has NOT been uploaded to permanent storage. This is deliberate: applying schema migrations and activating persistent storage need separately scoped owner approval, a check of the recipient private key and recovery/retention policy. Current 30-day Actions artifact is temporary and should not be mistaken for permanent storage.

Ordinary `scripts/companies-collect.py --store supabase --migrate` applies only
versions 001/002. Migration 003 requires separate owner approval for the
specific SQL contents: calculate the lowercase SHA-256 of
`db/companies/003_encrypted_evidence_archive.sql`, independently review the SQL,
then invoke a migration-only command with
`--approve-evidence-migration-003-sha256 REVIEWED_64_CHARACTER_HASH`.
Do not combine this option with collection, promotion or reset. This flag and
checksum are operational safeguards, not substitutes for owner authorization.

After owner approves the migration and recovers the test ciphertext locally with their key, upload would use:

```bash
python -m collectors.companies.evidence_vault_store \
  --cipher /ABSOLUTE/PRIVATE/evidence-5ae24a63ed89dd1fe4098c25feb8b6c6688319de8db5dd42c8f79ee1f0481c11.cms.der \
  --manifest /ABSOLUTE/PRIVATE/manifest-5ae24a63ed89dd1fe4098c25feb8b6c6688319de8db5dd42c8f79ee1f0481c11.json \
  --approve-upload-ciphertext
```

Use only a user-approved connection. Independently verify the ciphertext's DB length + SHA, RLS and absence of company changes. Database storage alone does not prove independent backups or WORM retention.
