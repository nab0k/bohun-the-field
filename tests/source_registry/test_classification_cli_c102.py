"""C1-02 offline runner contract tests, no DB/network/model service."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from collectors.source_registry import classification_cli as cli


def item(i, *, confirmed=False):
    return {
        "source_id": f"feed-{i}",
        "title": "Demo official notice",
        "description": "Tender notices and invitations to bid",
        "url": f"https://example{i}.org/feed",
        "sample_titles": [],
        "delivery_evidence": (
            [{"method": "rss", "status": "confirmed", "ref": f"probe-{i}"}]
            if confirmed else []),
    }


class RunnerTests(unittest.TestCase):
    def test_counts_and_provenance_with_unknown_transport(self):
        result = cli.build_report({"sources": [item(1), item(2)]})
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(result["class_counts"]["tenders"], 2)
        self.assertEqual(result["delivery_counts"]["unknown"], 2)
        self.assertEqual(result["delivery_unverified_or_conflicting"], 2)
        self.assertIs(result["owner_approved"], False)
        self.assertIs(result["network_or_live_probes_run"], False)
        self.assertIsNone(result["human_gold_assessment"])

    def test_138_requires_all_explicit_transport_evidence(self):
        rows = [item(i, confirmed=True) for i in range(138)]
        result = cli.build_report({"sources": rows}, require_138=True)
        self.assertEqual(result["source_count"], 138)
        self.assertEqual(result["delivery_counts"]["rss_atom"], 138)
        rows[0]["delivery_evidence"] = []
        with self.assertRaisesRegex(cli.ClassificationError,
                                    "138_VERIFIED_DELIVERY_NOT_ESTABLISHED"):
            cli.build_report({"sources": rows}, require_138=True)

    def test_sample_of_50_reproducible_independent_of_order(self):
        rows = [item(i, confirmed=True) for i in range(138)]
        first = cli.build_report({"sources": rows}, sample_seed="owner-seed-2026")
        second = cli.build_report({"sources": list(reversed(rows))},
                                  sample_seed="owner-seed-2026")
        ids = first["review_sample_source_ids"]
        self.assertEqual(len(ids), 50)
        self.assertEqual(len(set(ids)), 50)
        self.assertEqual(ids, second["review_sample_source_ids"])
        changed = cli.build_report({"sources": rows}, sample_seed="another-seed")
        self.assertNotEqual(ids, changed["review_sample_source_ids"])

    def test_gold_must_match_preselected_source_ids(self):
        rows = [item(i, confirmed=True) for i in range(138)]
        report = cli.build_report({"sources": rows})
        sample_ids = report["review_sample_source_ids"]
        correct = {sid: "tenders" for sid in sample_ids}
        self.assertEqual(cli.build_report(
            {"sources": rows}, {"labels": correct})["human_gold_assessment"]["correct"], 50)
        wrong = dict(correct)
        wrong.pop(next(iter(wrong)))
        outside = next(f"feed-{i}" for i in range(138) if f"feed-{i}" not in sample_ids)
        wrong[outside] = "tenders"
        with self.assertRaisesRegex(cli.ClassificationError, "GOLD_SAMPLE_IDS_MISMATCH"):
            cli.build_report({"sources": rows}, {"labels": wrong})

    def test_precomputed_proposal_accepted_only_with_exact_quote(self):
        row = item(3)
        row["description"] = "A specialised publisher"
        row["sample_titles"] = ["Technology journal: research papers on radar"]
        proposal = {"source_id": "feed-3", "source_class": "technology",
                    "evidence_quote": "research papers"}
        report = cli.build_report({"sources": [row]},
                                  proposal_payload={"proposals": [proposal]})
        found = report["classifications"][0]
        self.assertEqual(found["source_class"], "technology")
        self.assertEqual(found["class_origin"], "external_proposal")
        self.assertEqual(found["delivery_method"], "unknown")
        self.assertEqual(report["external_proposals_accepted"], 1)

    def test_fabricated_proposal_is_not_evidence(self):
        row = item(3)
        row["description"] = "A specialised publisher"
        proposal = {"source_id": "feed-3", "source_class": "grants",
                    "evidence_quote": "a grant that the source never mentioned"}
        report = cli.build_report({"sources": [row]},
                                  proposal_payload={"proposals": [proposal]})
        found = report["classifications"][0]
        self.assertEqual(found["class_status"], "needs_review")
        self.assertEqual(found["source_class"], "other")
        self.assertEqual(report["external_proposals_accepted"], 0)

    def test_precomputed_proposals_cannot_invent_source_ids(self):
        fake = {"source_id": "feed-999", "source_class": "media",
                "evidence_quote": "Defence news"}
        with self.assertRaisesRegex(cli.ClassificationError, "PROPOSAL_UNKNOWN_SOURCE"):
            cli.build_report({"sources": [item(3)]},
                             proposal_payload={"proposals": [fake]})

    def test_precomputed_proposals_cannot_repeat_source_ids(self):
        proposal = {"source_id": "feed-3", "source_class": "media",
                    "evidence_quote": "Defence news"}
        with self.assertRaisesRegex(cli.ClassificationError, "PROPOSAL_DUPLICATE_SOURCE"):
            cli.build_report({"sources": [item(3)]},
                             proposal_payload={"proposals": [proposal, proposal]})

    def test_precomputed_proposals_reject_unexpected_fields(self):
        proposal = {"source_id": "feed-3", "source_class": "media",
                    "evidence_quote": "Defence news", "delivery_method": "rss"}
        with self.assertRaisesRegex(cli.ClassificationError, "PROPOSAL_FIELDS"):
            cli.build_report({"sources": [item(3)]},
                             proposal_payload={"proposals": [proposal]})

    def test_precomputed_proposal_failure_for_unstructured_source(self):
        with self.assertRaises(cli.ClassificationError):
            cli.build_report({"sources": [{"source_id": "feed-3"}]},
                             proposal_payload={"proposals": []})

    def test_proposal_cli_reads_local_fixture_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            source_path = Path(folder, "sources.json")
            proposal_path = Path(folder, "proposals.json")
            row = item(3)
            row["description"] = "Specialist reporting on radar"
            row["sample_titles"] = ["Research papers on radar imaging"]
            source_path.write_text(json.dumps({"sources": [row]}), encoding="utf-8")
            proposal_path.write_text(json.dumps({"proposals": [
                {"source_id": "feed-3", "source_class": "technology",
                 "evidence_quote": "Research papers"}]}), encoding="utf-8")
            before = set(Path(folder).iterdir())
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli.main(["--input", str(source_path),
                                   "--proposals", str(proposal_path)])
            self.assertEqual(status, 0)
            self.assertEqual(set(Path(folder).iterdir()), before)
            self.assertEqual(json.loads(output.getvalue())["external_proposals_accepted"], 1)

    def test_count_137_is_not_138(self):
        rows = [item(i, confirmed=True) for i in range(137)]
        with self.assertRaises(cli.ClassificationError):
            cli.build_report({"sources": rows}, require_138=True)

    def test_human_gold_50_calculation(self):
        rows = [item(i) for i in range(50)]
        labels = {f"feed-{i}": "tenders" for i in range(50)}
        result = cli.build_report({"sources": rows}, {"labels": labels})
        self.assertEqual(result["human_gold_assessment"]["correct"], 50)
        self.assertEqual(result["human_gold_assessment"]["sample_size"], 50)

    def test_reject_envelope_with_unexpected_keys(self):
        with self.assertRaisesRegex(cli.ClassificationError, "SOURCE_ENVELOPE"):
            cli.build_report({"sources": [], "access_token": "secret"})

    def test_reject_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "input.json")
            path.write_text('{"sources": [], "sources": []}', encoding="utf-8")
            with self.assertRaisesRegex(cli.ClassificationError,
                                        "DUPLICATE_JSON_KEY"):
                cli._load(str(path))

    def test_reject_json_nan(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "input.json")
            path.write_text('{"sources": NaN}', encoding="utf-8")
            with self.assertRaisesRegex(cli.ClassificationError, "NONFINITE_JSON"):
                cli._load(str(path))

    def test_error_output_never_contains_input_or_file_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "sensitive-name.json")
            path.write_text('{"sources": [{"title":"private secret"}]}', encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = cli.main(["--input", str(path)])
            self.assertEqual(status, 2)
            self.assertEqual(out.getvalue(), "")
            self.assertNotIn("sensitive-name", err.getvalue())
            self.assertNotIn("private secret", err.getvalue())
            self.assertIn("error_code", err.getvalue())

    def test_cli_stdout_has_no_unrequested_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "sources.json")
            path.write_text(json.dumps({"sources": [item(1)]}), encoding="utf-8")
            before = set(Path(folder).iterdir())
            out = io.StringIO()
            with redirect_stdout(out):
                status = cli.main(["--input", str(path)])
            self.assertEqual(status, 0)
            self.assertEqual(set(Path(folder).iterdir()), before)
            result = json.loads(out.getvalue())
            self.assertEqual(result["source_count"], 1)


if __name__ == "__main__":
    unittest.main()
