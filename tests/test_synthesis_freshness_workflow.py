"""C3-02 workflow wiring guardrails; no network calls or GitHub Actions execution."""
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "kb-daily.yml"


class FreshnessWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.step = cls.text.split("      - name: C3-02 Public synthesis item-date freshness (advisory)", 1)[1].split("      - name: Summary", 1)[0]

    def test_runs_after_site_feed_refresh(self):
        text = self.text
        self.assertLess(text.index("- name: Refresh the site feed now"),
                        text.index("- name: C3-02 Public synthesis item-date freshness"))

    def test_always_advisory_not_publish_blocker(self):
        self.assertIn("continue-on-error: true", self.step)
        self.assertIn('if: "!cancelled()"', self.step)
        self.assertNotIn("exit 1", self.step)

    def test_checks_real_public_endpoint_via_preapproved_script(self):
        self.assertIn("python -B scripts/check_synthesis_freshness.py --max-age-days 1", self.step)

    def test_retains_machine_readable_evidence(self):
        self.assertIn('tee "$RUNNER_TEMP/kb-daily/freshness.json"', self.step)
        self.assertIn("set -o pipefail", self.step)
        self.assertIn("id: synthesis_freshness", self.step)

    def test_summary_can_report_unknown_when_step_absent(self):
        self.assertIn("steps.synthesis_freshness.outputs.freshness_status", self.text)
        self.assertIn("steps.synthesis_freshness.outputs.latest_item_date", self.text)
        self.assertIn("steps.synthesis_freshness.outputs.age_days", self.text)
        self.assertIn("steps.synthesis_freshness.outputs.published_count", self.text)
        self.assertIn("${FRESHNESS_STATUS:-not_checked}", self.text)
        self.assertIn("news/events, not necessarily site publication timestamps", self.text)

    def test_result_kept_as_existing_job_artifact(self):
        self.assertIn("name: kb-daily-result", self.text)
        self.assertIn("path: ${{ runner.temp }}/kb-daily", self.text)

    def test_no_new_permissions_or_writer_or_secret(self):
        self.assertEqual(len(re.findall(r"\bpermissions:\s*\n", self.text)), 1)
        self.assertNotIn("actions: write", self.step)
        self.assertNotIn("secrets.", self.step)
        self.assertNotIn("gh workflow run", self.step)


if __name__ == "__main__":
    unittest.main()
