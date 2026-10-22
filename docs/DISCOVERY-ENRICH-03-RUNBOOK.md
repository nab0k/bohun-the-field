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
