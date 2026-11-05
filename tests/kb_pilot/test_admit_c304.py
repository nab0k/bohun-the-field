"""C3-04: offline fail-closed regressions for kb_pilot admission.

No DB, HTTP, model API, secrets, workflow or side effects.
"""
import unittest
from unittest.mock import patch

from collectors.kb_pilot import admit


def article():
    return {
        "headline": "ACME delivered equipment to the depot.",
        "what_happened": "ACME delivered equipment to the depot.",
        "bigger_picture": "This extends its manufacturing reach.",
        "why_it_matters": "The delivery increases regional sustainment.",
        "context": "A verified prior contract was signed.",
        "policy_direction": None,
        "supply_chain": None,
        "sources": [{"url": "https://official.example/release"}],
    }


class C304AdmissionGuard(unittest.TestCase):
    def decision(self, original, texts):
        items = admit.review_items(original)
        final = admit.rebuild(original, items, texts)
        return admit.decide(final, items, texts, False, 0)

    def test_complete_verified_payload_can_publish(self):
        original = article()
        items = admit.review_items(original)
        kept = {n: text for n, _section, text in items}
        self.assertEqual(self.decision(original, kept), ("PUBLISH", []))

    def test_removed_what_happened_blocks_even_if_original_present(self):
        original = article()
        items = admit.review_items(original)
        kept = {n: text for n, section, text in items if section != "what_happened"}
        decision, reasons = self.decision(original, kept)
        self.assertEqual(decision, "HOLD")
        self.assertIn("what_happened_empty", reasons)

    def test_removed_why_it_matters_blocks_even_if_original_present(self):
        original = article()
        items = admit.review_items(original)
        kept = {n: text for n, section, text in items if section != "why_it_matters"}
        decision, reasons = self.decision(original, kept)
        self.assertEqual(decision, "HOLD")
        self.assertIn("why_it_matters_empty", reasons)

    def test_removed_headline_blocks_final_payload(self):
        original = article()
        items = admit.review_items(original)
        kept = {n: text for n, section, text in items if n != 0}
        decision, reasons = self.decision(original, kept)
        self.assertEqual(decision, "HOLD")
        self.assertIn("headline_unsupported", reasons)

    def test_rebuilt_version_discards_unverified_context_sentence(self):
        original = article()
        original["what_happened"] = "ACME delivered equipment. Unknown sources claim new production."
        items = admit.review_items(original)
        kept = {n: text for n, sec, text in items if "Unknown sources" not in text}
        final = admit.rebuild(original, items, kept)
        self.assertNotIn("Unknown sources", final["what_happened"])
        self.assertEqual(original["what_happened"],
                         "ACME delivered equipment. Unknown sources claim new production.")

    def test_fake_caseevak_foreign_context_is_not_reintroduced(self):
        original = article()
        original["what_happened"] = ("CaseEvak vehicles were delivered. "
                                    "An unrelated maritime missile sale was also announced.")
        items = admit.review_items(original)
        kept = {n: text for n, _, text in items if "unrelated maritime" not in text}
        final = admit.rebuild(original, items, kept)
        self.assertNotIn("maritime missile", final["what_happened"])
        self.assertIn("CaseEvak", final["what_happened"])

    def test_no_real_source_is_hold(self):
        original = article()
        original["sources"] = []
        items = admit.review_items(original)
        kept = {n: text for n, sec, text in items}
        decision, reasons = self.decision(original, kept)
        self.assertEqual(decision, "HOLD")
        self.assertIn("no_real_source", reasons)

    def test_run_limit_is_hold(self):
        original = article()
        items = admit.review_items(original)
        kept = {n: text for n, sec, text in items}
        result = admit.decide(original, items, kept, False, admit.MAX_PUBLISH)
        self.assertEqual(result, ("HOLD", ["run_limit"]))

    def test_review_numbers_do_not_use_unseen_tail(self):
        original = article()
        original["headline"] = "ACME delivered 999 units of equipment."
        items = admit.review_items(original)

        class Provider:
            name = "strong-test-model"

        class Fake:
            providers = [Provider()]
            off = set()
            def request(self, *args, **kwargs):
                return {}
            def free_many(self, requests, exclude=None):
                return [("ok", {
                    "defence_relevant": True,
                    "verdicts": [{"n": n, "ok": True} for n, _, _ in items]
                }, {"model": "strong-test-model"})]

        evidence = "Source confirmed equipment delivery. " + ("x" * admit.SOURCE_CHARS) + " 999 units"
        reviewed = admit.review(Fake(), evidence, original, items)
        self.assertIsNotNone(reviewed)
        accepted, _, _ = reviewed
        self.assertNotIn(0, accepted)

    def test_weak_second_reviewer_cannot_approve_replacement(self):
        original = article()
        items = admit.review_items(original)

        class Provider:
            name = "strong-test-model"

        class Fake:
            providers = [Provider()]
            off = set()
            calls = 0
            def request(self, *args, **kwargs):
                return {}
            def free_many(self, requests, exclude=None):
                self.calls += 1
                if self.calls == 1:
                    return [("ok", {
                        "defence_relevant": True,
                        "verdicts": [{"n": n, "ok": False,
                          "fix": "The depot received equipment confirmed by its operator."}
                          for n, _, _ in items]
                    }, {"model": "strong-test-model"})]
                return [("ok", {"verdicts": [{"n": n, "ok": True} for n, _, _ in items]},
                         {"model": "cloudflare"})]

        accepted, _, stats = admit.review(Fake(), "Source confirms equipment received.", original, items)
        self.assertEqual(accepted, {})
        self.assertEqual(stats["fixed"], 0)


if __name__ == "__main__":
    unittest.main()


class ContextSourcesCarry(unittest.TestCase):
    """C3-11 v21: admission writes a new version; the line links must come with it."""

    def test_held_reads_and_write_keeps_context_sources(self):
        import json
        links = [{"line": "2024: A.", "url": "https://b.example/a"}]

        class Cur:
            def __init__(self):
                self.calls = []

            def execute(self, sql, args=()):
                self.calls.append((" ".join(sql.split()), args))

            def fetchall(self):
                return [(5, 9, 1, "H", "W", "2024: A.", None, None, None, "Y", [{"url": "https://a.example"}], links)]

        cur = Cur()
        row = admit.held(cur, 1)[0]
        self.assertIn("v.context_sources", cur.calls[0][0])
        self.assertEqual(row["v"]["context_sources"], links)
        new = admit.rebuild(row["v"], [], {0: "New headline"})
        admit.write(cur, row, new, "PUBLISH", [], "p", "r")
        sql, args = [c for c in cur.calls if c[0].startswith("INSERT INTO knowledge.synthesis_version")][0]
        self.assertIn("context_sources", sql)
        self.assertEqual(json.loads(args[10]), links)

