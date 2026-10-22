-- DISCOVERY-ENRICH-03. ADDITIVE, ciphertext-only evidence custody.
-- Requires separate owner approval before APPLYING to existing Supabase.
-- No decrypted bytes, source URLs, names, LEIs or other identifying evidence columns.
CREATE TABLE IF NOT EXISTS companies.encrypted_evidence_archive (
  cipher_sha256 TEXT PRIMARY KEY
    CHECK (cipher_sha256 ~ '^[0-9a-f]{64}$'),
  cms_ciphertext BYTEA NOT NULL
    CHECK (octet_length(cms_ciphertext) BETWEEN 1 AND 52428800),
  envelope TEXT NOT NULL DEFAULT 'CMS-AES-256-GCM'
    CHECK (envelope = 'CMS-AES-256-GCM'),
  recipient_cert_sha256 TEXT NOT NULL
    CHECK (recipient_cert_sha256 ~ '^[0-9a-f]{64}$'),
  batch_key TEXT NOT NULL
    CHECK (batch_key IN ('DISCOVERY-ENRICH-03')),
  file_count INTEGER NOT NULL CHECK (file_count BETWEEN 1 AND 80),
  cipher_bytes INTEGER NOT NULL CHECK (cipher_bytes BETWEEN 1 AND 52428800
                                 AND cipher_bytes=octet_length(cms_ciphertext)),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  note TEXT NOT NULL DEFAULT 'unverified source research; no automatic identity confirmation'
    CHECK (note='unverified source research; no automatic identity confirmation')
);
ALTER TABLE companies.encrypted_evidence_archive ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE companies.encrypted_evidence_archive FROM PUBLIC, anon, authenticated;
-- No public RLS policy: the existing owner-controlled PostgreSQL connection is
-- the only permitted writer/reader. No DELETE/UPDATE endpoint is implemented.
-- Not a WORM/compliance vault and not an independent encrypted backup.
