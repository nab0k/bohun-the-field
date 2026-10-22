"""DISCOVERY-ENRICH-02: no network, no database, no legal confirmation."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from collectors.companies import evidence_review as ev
from collectors.companies import enrich_pilot as pilot
from tests.companies.test_enrich_pilot import synthetic_rows


def research():
    return [pilot.research_record(row) for row in synthetic_rows()]


def example_capture(case):
    quote = "Registry number 123456789 and company status reported."
    raw = ("<html>" + quote + "</html>").encode("utf-8")
    return ev.capture_receipt(
        case, case["source_links"][0]["url"], raw, quote,
        kind="legal_entity", lifecycle="liquidation", source_as_of="2026-06-03")


class TestPrivateEvidenceReview(unittest.TestCase):
    def test_20_source_hints_are_unverified(self):
        packets = ev.review_packets(research())
        self.assertEqual(len(packets), 20)
        self.assertEqual(len({p["candidate_id"] for p in packets}), 20)
        for packet in packets:
            self.assertFalse(packet["identity_confirmed"])
            self.assertFalse(packet["publishable"])
            self.assertFalse(packet["company_write_allowed"])
            self.assertEqual(packet["identity_kind"], "unknown")
            self.assertEqual(packet["lifecycle_status"], "unknown")
            self.assertEqual(packet["review_status"], "unreviewed")
            self.assertTrue(all(not link["quote_verified"] for link in packet["source_links"]))

    def test_brand_group_and_lifecycle_cases_remain_open(self):
        p = {row["candidate_id"]: row for row in ev.review_packets(research())}
        self.assertEqual(p[531]["research_focus"], "brand_vs_legal_entity")  # Direct Action
        self.assertEqual(p[412]["research_focus"], "subsidiary_vs_group")  # Combitech
        self.assertEqual(p[505]["research_focus"], "lifecycle_review")  # Delfox
        self.assertEqual(p[229]["research_focus"], "subsidiary_vs_group")  # BAE
        self.assertEqual(p[505]["lifecycle_status"], "unknown")
        self.assertEqual(p[531]["identity_kind"], "unknown")
        self.assertEqual(p[412]["source_country_source_only"], "FINLAND")
        self.assertNotIn("legal_country", p[412])

    def test_only_known_candidate_ids_allowed(self):
        hints = ev.load_hints()
        hints[0]["candidate_id"] = 999999
        with self.assertRaises(ValueError):
            ev.review_packets(research(), hints)

    def test_hint_link_is_not_quote_evidence(self):
        packet = ev.review_packets(research())[0]
        self.assertEqual(packet["source_links"][0]["state"], "unfetched_hint")
        with self.assertRaisesRegex(ValueError, "not present"):
            ev.capture_receipt(packet, packet["source_links"][0]["url"],
                               b"<html>There is no claim</html>", "not the actual claim")

    def test_capture_is_hypothesis_not_verified_legal_status(self):
        packet = next(c for c in ev.review_packets(research()) if c["candidate_id"] == 505)
        receipt = example_capture(packet)
        self.assertEqual(receipt["claimed_lifecycle"], "liquidation")
        self.assertEqual(receipt["claimed_identity_kind"], "legal_entity")
        self.assertTrue(receipt["quote_present_in_capture"])
        self.assertFalse(receipt["exhibitor_identity_verified"])
        self.assertFalse(receipt["registry_identifier_correlated"])
        self.assertEqual(receipt["review_status"], "unreviewed")
        self.assertFalse(receipt["publication_allowed"])
        self.assertNotIn("source_body", receipt)

    def test_wrong_url_or_unsupported_classification_fails(self):
        packet = ev.review_packets(research())[0]
        with self.assertRaises(ValueError):
            ev.capture_receipt(packet, "https://example.org/other", b"1234567890", "1234567890")
        with self.assertRaises(ValueError):
            ev.capture_receipt(packet, packet["source_links"][0]["url"], b"1234567890",
                               "1234567890", kind="automatic_legal_verification")

    def test_persistent_private_chain_idempotence_and_tamper_detection(self):
        packet = ev.review_packets(research())[0]
        receipt = example_capture(packet)
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "private-ledger"
            self.assertEqual(ev.append_private_receipt(root, receipt)["status"], "appended")
            self.assertEqual(ev.append_private_receipt(root, receipt)["status"], "already_present")
            chain = root / "evidence_chain.jsonl"
            entries, _ = ev.verify_ledger(chain)
            self.assertEqual(len(entries), 1)
            self.assertEqual(chain.stat().st_mode & 0o777, 0o600)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            with chain.open("a", encoding="utf-8") as f:
                f.write('{"seq":2,"previous_sha256":"incorrect"}\n')
            with self.assertRaises(ValueError):
                ev.verify_ledger(chain)

    def test_forged_verification_receipt_never_persists(self):
        packet = ev.review_packets(research())[0]
        receipt = example_capture(packet)
        receipt["exhibitor_identity_verified"] = True
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "forbidden"):
                ev.append_private_receipt(pathlib.Path(temp) / "secure", receipt)
        receipt = example_capture(packet)
        receipt["quote"] = "altered quote"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "invalid"):
                ev.append_private_receipt(pathlib.Path(temp) / "secure", receipt)

    def test_review_packets_refuse_silent_overwrite(self):
        packets = ev.review_packets(research())
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "private"
            self.assertEqual(ev.persist_review_packets(root, packets), 20)
            self.assertEqual(ev.persist_review_packets(root, packets), 20)
            with self.assertRaises(ValueError):
                ev.persist_review_packets(root, packets[:-1])

    def test_symlink_private_file_rejected(self):
        packets = ev.review_packets(research())
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "private"
            root.mkdir()
            elsewhere = pathlib.Path(temp) / "target"
            elsewhere.write_text("should not overwrite", encoding="utf-8")
            (root / "review20_unverified.jsonl").symlink_to(elsewhere)
            with self.assertRaises(ValueError):
                ev.persist_review_packets(root, packets)
            self.assertEqual(elsewhere.read_text(encoding="utf-8"), "should not overwrite")

    def test_https_only_and_no_credentials(self):
        for value in ("http://example.com/path", "https://user:pass@example.com/",
                      "https://localhost/test", "https://127.0.0.1/test",
                      "https://example.com/path#anchor"):
            with self.assertRaises(ValueError):
                ev._public_https(value)

    def test_existing_enrichment_cli_handles_review20_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            tmp = pathlib.Path(temp)
            input_path = tmp / "input.json"
            input_path.write_text(json.dumps(synthetic_rows()), encoding="utf-8")
            out = tmp / "private"
            cmd = [sys.executable, "-m", "collectors.companies.enrich_pilot",
                   "--input-json", str(input_path), "--review20",
                   "--private-output-dir", str(out)]
            result = subprocess.run(cmd, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["review20_unverified_packets"], 20)
            self.assertEqual(report["verified_review20_identifications"], 0)
            self.assertTrue((out / "review20_unverified.jsonl").exists())
            self.assertNotIn("Fictional Supplier", result.stdout)
            self.assertNotIn("http", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
