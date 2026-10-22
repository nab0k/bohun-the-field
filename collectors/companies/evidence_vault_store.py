"""Opt-in ciphertext-only archive port to EXISTING Supabase companies schema.

No plaintext enters PostgreSQL. No schema migrations here. Never invoked
unless caller explicitly opts in after owner approval. No company writes.
"""
import hashlib
import json
import pathlib
import re
import subprocess

MAX_CIPHER = 50 * 1024 * 1024
_HEX = re.compile(r"^[0-9a-f]{64}$")


def load_encrypted(cipher_path, manifest_path):
    cipher_path, manifest_path = pathlib.Path(cipher_path), pathlib.Path(manifest_path)
    if cipher_path.is_symlink() or manifest_path.is_symlink():
        raise ValueError("cannot upload symlinked files")
    if cipher_path.suffix != ".der" or not cipher_path.is_file():
        raise ValueError("expected CMS DER ciphertext")
    if not 0 < cipher_path.stat().st_size <= MAX_CIPHER:
        raise ValueError("ciphertext must be bounded")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cipher = cipher_path.read_bytes()
    digest = hashlib.sha256(cipher).hexdigest()
    if (manifest.get("envelope") != "CMS-AES-256-GCM" or
            manifest.get("cipher_sha256") != digest or
            not _HEX.fullmatch(manifest.get("recipient_cert_sha256", "")) or
            manifest.get("cipher_bytes") != len(cipher) or
            not 1 <= manifest.get("file_count", 0) <= 80 or
            manifest.get("company_writes") != 0 or
            manifest.get("identity_confirmations") != 0):
        raise ValueError("ciphertext manifest fails integrity/bounds checks")
    if cipher_path.name != "evidence-" + digest + ".cms.der":
        raise ValueError("ciphertext filename is not content-addressed")
    if manifest_path.name != "manifest-" + digest + ".json":
        raise ValueError("manifest filename does not match envelope")
    # Parsing DER validates the structure. AEAD integrity can only be proven
    # later during private-key decryption by the owner.
    inspection = subprocess.run(["openssl", "cms", "-cmsout", "-print",
                                 "-inform", "DER", "-in", str(cipher_path)],
                                capture_output=True, check=True, timeout=20)
    if b"algorithm: aes-256-gcm" not in inspection.stdout.lower():
        raise ValueError("CMS envelope does not actually use AES-256-GCM")
    return cipher, manifest


def upload_encrypted(cipher_path, manifest_path, *, approved=False, conn=None):
    """Ciphertext-only, INSERT-or-existing; no schema creation or company writes."""
    if approved is not True:
        raise PermissionError("owner-approved ciphertext upload flag required")
    cipher, manifest = load_encrypted(cipher_path, manifest_path)
    from .pg_store import PgStore
    own = conn is None
    store = PgStore() if own else conn
    db = store.db if own else conn
    digest = manifest["cipher_sha256"]
    try:
        table = db.execute("SELECT to_regclass('companies.encrypted_evidence_archive') AS t").fetchone()
        if not table or not table["t"]:
            raise RuntimeError("003 encrypted-evidence schema is not applied")
        v = db.execute("SELECT version FROM companies.schema_migration WHERE version=3").fetchone()
        if not v:
            raise RuntimeError("003 schema migration ledger entry is absent")
        existing = db.execute(
            "SELECT cipher_sha256, cipher_bytes, recipient_cert_sha256, file_count "
            "FROM companies.encrypted_evidence_archive WHERE cipher_sha256=%s", (digest,)).fetchone()
        if existing:
            if (existing["cipher_bytes"] != len(cipher) or
                    existing["recipient_cert_sha256"] != manifest["recipient_cert_sha256"] or
                    existing["file_count"] != manifest["file_count"]):
                raise ValueError("existing ciphertext metadata differs; do not overwrite")
            if own:
                db.rollback()
            return {"status": "already_present", "cipher_sha256": digest}
        db.execute(
            "INSERT INTO companies.encrypted_evidence_archive "
            "(cipher_sha256,cms_ciphertext,envelope,recipient_cert_sha256,batch_key,file_count,cipher_bytes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (digest, cipher, "CMS-AES-256-GCM", manifest["recipient_cert_sha256"],
             "DISCOVERY-ENRICH-03", manifest["file_count"], len(cipher)))
        db.commit()
        return {"status": "ciphertext_inserted", "cipher_sha256": digest}
    except Exception:
        db.rollback()
        raise
    finally:
        if own:
            store.close()


def main(argv=None):
    """Manual, separate-owner-approved Supabase ciphertext-only upload."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cipher", required=True, type=pathlib.Path)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--approve-upload-ciphertext", action="store_true")
    args = parser.parse_args(argv)
    if not args.approve_upload_ciphertext:
        parser.error("explicit approval flag required for any Supabase write")
    result = upload_encrypted(args.cipher, args.manifest, approved=True)
    # Only SHA and lifecycle outcome, never source or legal identity.
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
