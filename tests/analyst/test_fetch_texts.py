"""Offline tests for EXP-013 stage A: CMS encryption round-trip and no plaintext in outputs."""
import importlib.util
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("fetch", ROOT / "scripts" / "exp013-fetch-texts.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)

SECRET_TEXT = "Confidential paragraph about Rheinmetall 155mm capacity in Unterluess."
ITEMS = [{"id": "tag:1", "title": "Headline A", "canonical": [{"href": "https://www.defensenews.com/x"}],
          "origin": {"title": "Defense News"}, "published": 1,
          "summary": {"content": "<p>" + SECRET_TEXT * 40 + "</p>"}},
         {"id": "tag:2", "title": "Headline B", "canonical": [{"href": "https://www.ukrinform.net/y"}],
          "summary": {"content": "<p>Teaser…</p>"}}]


@unittest.skipUnless(shutil.which("openssl"), "openssl required")
class TestStageA(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout",
                        str(self.tmp / "k.pem"), "-out", str(self.tmp / "c.pem"), "-days", "1",
                        "-subj", "/CN=test"], check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_round_trip_and_no_plaintext(self):
        out = self.tmp / "out"
        manifest = fetch.build(ITEMS, self.tmp / "c.pem", out)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["manifest.json", "texts.cms.der"])
        cipher = (out / "texts.cms.der").read_bytes()
        for leak in (SECRET_TEXT, "Headline A", "Rheinmetall"):
            self.assertNotIn(leak.encode(), cipher)
            self.assertNotIn(leak, (out / "manifest.json").read_text())
            self.assertNotIn(leak, json.dumps(manifest))
        self.assertEqual((manifest["records"], manifest["classes"]), (2, {"FULL": 1, "PARTIAL": 1}))
        plain = subprocess.run(["openssl", "cms", "-decrypt", "-inform", "DER", "-in", str(out / "texts.cms.der"),
                                "-inkey", str(self.tmp / "k.pem"), "-recip", str(self.tmp / "c.pem")],
                               check=True, capture_output=True).stdout.decode()
        rows = [json.loads(l) for l in plain.splitlines()]
        self.assertEqual([r["id"] for r in rows], ["tag:1", "tag:2"])
        self.assertIn(SECRET_TEXT, rows[0]["content_html"])
        self.assertEqual(rows[1]["domain"], "ukrinform.net")

    def test_repo_certificate_is_public_only(self):
        text = (ROOT / "scripts" / "exp013_cms.crt").read_text()
        self.assertIn("BEGIN CERTIFICATE", text)
        self.assertNotIn("PRIVATE KEY", text)


if __name__ == "__main__":
    unittest.main()
