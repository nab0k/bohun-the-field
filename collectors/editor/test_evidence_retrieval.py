"""Synthetic, no-network checks for bounded two-pass excerpt selection."""
import unittest

from collectors.editor.evidence_retrieval import select_excerpts, fit_packet_to_budget


class EvidenceRetrievalTests(unittest.TestCase):
    def test_two_distinct_passages_for_same_claim(self):
        body = ("The ministry awarded forty unmanned vehicles to Orbis Systems after a competitive process.\n"
                "The Orbis Systems forty-vehicle contract has not reached delivery and remains subject to review.")
        result = select_excerpts(body, [{"text": "The ministry awarded forty vehicles to Orbis Systems."}])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(quote in body for quote in result))
        self.assertTrue(any("not reached delivery" in q for q in result))

    def test_claim_first_priority_before_extra_span(self):
        body = ("Agency awarded twenty drones for the maritime trial in January.\n"
                "Agency twenty drones remain under evaluation in January.\n"
                "Parliament approved the annual technology grant allocation in October.")
        claims = [{"text": "Agency awarded twenty drones."},
                  {"text": "Parliament approved technology grant allocation."}]
        result = select_excerpts(body, claims, max_spans=2)
        self.assertEqual(len(result), 2)
        self.assertTrue(any("Parliament" in q for q in result))

    def test_shared_top_quote_does_not_starve_second_claim(self):
        body = ("Agency awarded forty radios to Orbis under contract, delivery pending approval.\n"
                "Agency awarded forty radios to Orbis for the exercise.\n"
                "The Orbis contract delivery remains pending approval after review.")
        claims = [{"text": "Agency awarded forty radios to Orbis contract."},
                  {"text": "Orbis contract delivery pending approval review."}]
        picked = select_excerpts(body, claims, max_spans=2)
        self.assertEqual(len(picked), 2)
        self.assertTrue(any("after review" in q for q in picked))

    def test_only_literal_bounded_quotes_deterministic(self):
        body = ("The procurement agency confirmed a fresh satellite equipment program with a value of 400 million euros. " * 40)
        claims = [{"text": "Procurement agency confirmed 400 million euros satellite equipment."}] * 5
        first = select_excerpts(body, claims)
        self.assertEqual(first, select_excerpts(body, claims))
        self.assertLessEqual(len(first), 10)
        self.assertEqual(len(first), len(set(first)))
        self.assertTrue(all(len(q) <= 200 and q in body for q in first))

    def test_no_unrelated_quote(self):
        with self.assertRaisesRegex(ValueError, "no_lexically_related_excerpt"):
            select_excerpts("General economics and management of private office staff.",
                            [{"text": "Boreal aircraft launched 938 spacecraft."}])

    def test_packet_budget_discards_optional_tail_only(self):
        snippets = ["Agency awarded 12 vehicles.",
                    "Delivery remains subject to audit.",
                    "The audit may delay the expected delivery."]
        def builder(values):
            return {"claims": ["A" * 80], "spans": values}
        import json
        size_two = len(json.dumps(builder(snippets[:2]), ensure_ascii=False).encode())
        packet, kept = fit_packet_to_budget(snippets, builder,
                                            max_bytes=size_two, min_snippets=2)
        self.assertEqual(kept, snippets[:2])
        self.assertEqual(packet["spans"], snippets[:2])
        self.assertTrue(all(q in snippets for q in kept))

    def test_shared_quote_may_be_only_required_snippet(self):
        snippets = ["One shared official source quote.",
                    "Second contextual quote from article.",
                    "Third contextual quote from article."]
        def builder(values): return {"spans": values, "claims": ["one", "two", "three"]}
        import json
        size_one = len(json.dumps(builder(snippets[:1]), ensure_ascii=False).encode())
        packet, kept = fit_packet_to_budget(snippets, builder,
                                            max_bytes=size_one, min_snippets=1)
        self.assertEqual(kept, snippets[:1])
        self.assertEqual(packet["spans"], snippets[:1])

    def test_packet_budget_never_erases_mandatory_evidence(self):
        snippets = ["Evidence one enough length.", "Evidence two enough length."]
        with self.assertRaisesRegex(ValueError, "checker_packet_oversized"):
            fit_packet_to_budget(snippets, lambda q: {"claims": "X" * 1000, "spans": q},
                                 max_bytes=100, min_snippets=2)

    def test_packet_budget_counts_unicode_bytes_not_codepoints(self):
        snippets = ["Заявлено пять единиц техники.",
                    "Поставка пока не подтверждена."]
        def builder(q): return {"spans": q}
        import json
        one = len(json.dumps(builder(snippets[:1]), ensure_ascii=False).encode("utf-8"))
        _, kept = fit_packet_to_budget(snippets, builder, max_bytes=one, min_snippets=1)
        self.assertEqual(kept, snippets[:1])
        self.assertGreater(one, len(json.dumps(builder(snippets[:1]), ensure_ascii=False)))

    def test_bad_caps_and_inputs_fail_closed(self):
        for bad in (0, 11, True):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "invalid_span_limit"):
                select_excerpts("The procurement agency submitted a new report.",
                                [{"text": "procurement report"}], max_spans=bad)
        for claims in ([], [{"text": "x"}] * 6, [{"bad": "x"}]):
            with self.subTest(claims=claims), self.assertRaisesRegex(ValueError, "invalid_claims"):
                select_excerpts("The procurement agency submitted a new report.", claims)

    def test_duplicate_capture_passage_does_not_waste_spans(self):
        body = ("The authority announced a contract for twenty radios to supplier Caldera.\n"
                "The authority announced a contract for twenty radios to supplier Caldera.")
        result = select_excerpts(body, [{"text": "Authority contract twenty radios Caldera."}])
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
