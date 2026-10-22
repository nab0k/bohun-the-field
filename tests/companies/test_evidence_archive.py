"""Offline encrypted-evidence capture and authentication tests."""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from collectors.companies import evidence_archive as archive
from collectors.companies.evidence_review import load_hints


def fake_fetch(url):
    payload = ("<html><title>Unverified original primary page</title>"
               "<p>Source text, do not assume the identity is verified.</p></html>")
    return payload.encode("utf-8"), "text/html", url


@unittest.skipUnless(shutil.which("openssl"), "openssl required")
class TestEncryptedEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.cert = self.root / "recipient.pem"
        self.key = self.root / "private.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-nodes", "-keyout", str(self.key), "-out", str(self.cert),
                        "-days", "1", "-subj", "/CN=BOHUN-evidence-test"],
                       check=True, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_twenty_manifest_fake_http(self):
        stats = archive.collect_twenty(self.root / "private", fetcher=fake_fetch)
        self.assertEqual((stats["total"], stats["captured"], stats["unavailable"]), (20, 20, 0))
        evidence = json.loads((self.root / "private/source_snapshot_manifest.json").read_text())
        self.assertEqual(len(evidence["results"]), 20)
        self.assertFalse(evidence["legal_identity_verified"])
        self.assertFalse(any(r["identity_verified"] for r in evidence["results"]))
        for r in evidence["results"]:
            data = (self.root / "private" / r["archive_path"]).read_bytes()
            self.assertEqual(r["content_sha256"], hashlib.sha256(data).hexdigest())
            self.assertTrue(r["archive_path"].startswith("source_snapshots/"))
        self.assertEqual(len(list((self.root / "private/source_snapshots").iterdir())), 20)

    def test_missing_source_is_not_verified(self):
        def fail(url):
            raise archive.SourceCaptureError("intentionally unreachable")
        stats = archive.collect_twenty(self.root / "private", fetcher=fail)
        self.assertEqual(stats["captured"], 0)
        self.assertEqual(stats["unavailable"], 20)
        evidence = json.loads((self.root / "private/source_snapshot_manifest.json").read_text())
        self.assertTrue(all(x["state"] == "source_unavailable" for x in evidence["results"]))

    def test_cms_aes_gcm_roundtrip_and_no_plaintext(self):
        archive.collect_twenty(self.root / "private", fetcher=fake_fetch)
        report = archive.encrypted_export(self.root / "private", self.root / "envelopes",
                                          cert_path=self.cert)
        self.assertEqual(report["envelope"], "CMS-AES-256-GCM")
        self.assertFalse(report["durable_remote_copy_verified"])
        self.assertEqual(report["company_writes"], 0)
        enc = list((self.root / "envelopes").glob("*.cms.der"))
        self.assertEqual(len(enc), 1)
        cipher = enc[0].read_bytes()
        self.assertEqual(hashlib.sha256(cipher).hexdigest(), report["cipher_sha256"])
        self.assertNotIn(b"Unverified original primary page", cipher)
        self.assertNotIn(b"source_snapshots", cipher)
        manifest = list((self.root / "envelopes").glob("manifest-*.json"))
        self.assertEqual(len(manifest), 1)
        self.assertNotIn("Unverified original primary page", manifest[0].read_text())
        decrypted = subprocess.run(
            ["openssl", "cms", "-decrypt", "-inform", "DER",
             "-in", str(enc[0]), "-inkey", str(self.key), "-recip", str(self.cert)],
            check=True, capture_output=True).stdout
        self.assertIn(b"source_snapshots", decrypted)
        self.assertIn(b"Unverified original primary page", decrypted)

    def test_gcm_detects_tampering(self):
        archive.collect_twenty(self.root / "private", fetcher=fake_fetch)
        archive.encrypted_export(self.root / "private", self.root / "envelopes",
                                 cert_path=self.cert)
        enc = list((self.root / "envelopes").glob("*.cms.der"))[0]
        raw = bytearray(enc.read_bytes())
        # Modifying final content/authentication bytes must invalidate integrity.
        raw[-5] ^= 0x04
        tampered = self.root / "tampered.cms"
        tampered.write_bytes(raw)
        result = subprocess.run(
            ["openssl", "cms", "-decrypt", "-inform", "DER", "-in", str(tampered),
             "-inkey", str(self.key), "-recip", str(self.cert)],
            capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_certificate_fails_closed(self):
        archive.collect_twenty(self.root / "private", fetcher=fake_fetch)
        cert = self.root / "invalid.pem"
        cert.write_text("not a certificate")
        with self.assertRaises(ValueError):
            archive.encrypted_export(self.root / "private", self.root / "vault", cert_path=cert)

    def test_strict_source_manifest_and_redirects(self):
        with self.assertRaises(ValueError):
            archive.collect_twenty(self.root / "private", hints=load_hints()[:-1],
                                   fetcher=fake_fetch)
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "text/html"}
            def geturl(self):
                return "http://localhost/private"
            def read(self, length):
                return b"secret"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
        class FakeOpener:
            def open(self, request, timeout=None):
                return FakeResponse()
        with self.assertRaises(ValueError):
            archive.fetch_https_exact("https://www.amz.pl/kontakt.html",
                                      opener_factory=lambda host: FakeOpener())

    def test_source_unexpected_type_not_archived(self):
        def pdf_fake(url):
            return b"not an html page", "application/octet-stream", url
        stats = archive.collect_twenty(self.root / "private", fetcher=pdf_fake)
        self.assertEqual(stats["captured"], 0)


if __name__ == "__main__":
    unittest.main()
