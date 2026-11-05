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
