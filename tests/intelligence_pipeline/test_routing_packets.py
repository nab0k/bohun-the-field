"""Synthetic tests: routing/evidence integrity, never claims of semantic accuracy."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
import random
import unittest
from unittest.mock import patch

from collectors.intelligence_pipeline.research_packet import (
    EntityCandidate, EvidenceSpan, ResearchPacket, SourceRecord, build_packet,
)
from collectors.intelligence_pipeline.routing import DEFAULT_RULES, Rule, RuleSet, route_batch


def source(index=0, title="Factory expansion", text="A factory is planned. £10 million; not yet delivered.", **changes):
    values = dict(source_id="synthetic-publisher", source_version="v1", item_id=f"item-{index}",
                  url=f"https://example.org/news/{index}", title=title, text=text,
                  text_hash=sha256(text.encode()).hexdigest() if text is not None else None,
                  published_at="2026-10-18T08:00:00Z", received_at="2026-10-18T09:00:00Z",
                  completeness="COMPLETE" if text is not None else "UNKNOWN",
                  rights="PERMITTED", sensitive=False, source_stage="planned")
    values.update(changes)
    return SourceRecord(**values)


def packet(lead=None, **changes):
    return build_packet(lead or source(), ("company_capacity",), DEFAULT_RULES.version,
                        DEFAULT_RULES.identity, ("What is established?",), **changes)


class RoutingTests(unittest.TestCase):
    def test_every_category_is_candidate_only(self):
        for i, rule in enumerate(DEFAULT_RULES.rules):
            with self.subTest(rule.category):
                decision = route_batch([source(i, title=rule.keywords[0], text="A supplied statement.")]).decisions[0]
                self.assertIn(rule.category, decision.categories)
                self.assertEqual(decision.disposition, "RESEARCH_CANDIDATE")
                self.assertIn("NOT_SEMANTIC_VERIFICATION", " ".join(decision.reasons))

    def test_multicategory_unknown_and_word_boundaries(self):
        multi, unknown = source(title="Startup investment", text="A grant for a prototype."), source(1, "Nothing classified", "Unrelated prose.")
        decisions = {d.source.identity: d for d in route_batch([multi, unknown]).decisions}
        self.assertEqual(set(decisions[multi.identity].categories), {"startup", "investment", "grant_rd", "product_technology"})
        self.assertEqual(decisions[unknown.identity].relevance, "UNKNOWN")
        self.assertEqual(decisions[unknown.identity].disposition, "SOURCE_UPDATE")
        self.assertFalse(route_batch([source(title="Contractor", text="Unrelated prose.")]).selected_keys)

    def test_two_axes_and_fail_closed_flags(self):
        variants = [source(text=None), source(completeness="UNKNOWN"), source(completeness="PARTIAL"),
                    source(rights="UNKNOWN"), source(rights="DENIED"), source(sensitive=True),
                    source(entities=(EntityCandidate("Atlas", ("company-A", "company-B")),))]
        for item in variants:
            with self.subTest(item.blockers):
                decision = route_batch([item]).decisions[0]
                self.assertEqual(decision.relevance, "CANDIDATE")
                self.assertEqual(decision.sufficiency, "BLOCKED")
                self.assertIn(decision.disposition, {"HOLD", "VERIFY_SOURCE"})
                self.assertTrue(set(item.blockers) <= set(decision.reasons))
                self.assertFalse(route_batch([item]).packets())
                with self.assertRaises(ValueError):
                    packet(item)

    def test_no_body_classification_without_rights(self):
        item = source(title="A notice", text="Secret contract", rights="UNKNOWN")
        self.assertEqual(route_batch([item]).decisions[0].categories, ())

    def test_duplicate_receipts_preserve_identity(self):
        original = source()
        alias = replace(original, item_id="other-feed-alias", url=original.url + "?utm_source=feed#x",
                        received_at="2026-10-18T10:00:00+00:00")
        one, repeated = route_batch([original]), route_batch([alias, original, original])
        self.assertEqual(one.selected_keys, repeated.selected_keys)
        self.assertEqual(repeated.to_dict()["duplicate_count"], 2)
        self.assertEqual(one.packets()[0].identity, repeated.packets()[0].identity)
        self.assertEqual(len(repeated.packets()[0].to_dict()["receipts"]), 2)

    def test_timezone_representation_does_not_create_version(self):
        self.assertEqual(source().identity, source(published_at="2026-10-18T10:00:00+02:00").identity)

    def test_source_version_conflict_is_held_not_arbitrarily_chosen(self):
        original = source()
        changed = source(text="Different factory claim.")
        result = route_batch([original, changed])
        self.assertFalse(result.packets())
        for decision in result.decisions:
            self.assertEqual(decision.disposition, "HOLD")
            self.assertIn("SOURCE_VERSION_CONFLICT", decision.reasons)

    def test_nearby_titles_companies_and_generations_do_not_merge(self):
        records = [source(i, title="Contract for prototype", text=f"Company {i} generation {i} contract.",
                          entities=(EntityCandidate("Atlas", (f"company-{i}",)),)) for i in range(3)]
        result = route_batch(records)
        self.assertEqual(len(result.packets()), 3)
        self.assertEqual(len(set(p.identity for p in result.packets())), 3)

    def test_permutation_selection_and_diversity(self):
        records = [source(i, title=DEFAULT_RULES.rules[i % 9].keywords[0], text="Supplied statement.") for i in range(45)]
        original = route_batch(records)
        random.Random(917).shuffle(records)
        shuffled = route_batch(records)
        self.assertEqual(original.to_dict(), shuffled.to_dict())
        self.assertEqual([p.to_json() for p in original.packets()], [p.to_json() for p in shuffled.packets()])
        self.assertEqual(len(original.packets()), 5)
        self.assertEqual(len({p.categories[0] for p in original.packets()}), 5)
        self.assertEqual(sum("DEFERRED_PACKET_LIMIT" in d.reasons for d in original.decisions), 40)

    def test_bounds_no_padding_and_no_calls(self):
        with patch("socket.socket", side_effect=AssertionError("NETWORK_FORBIDDEN")), \
                patch("subprocess.Popen", side_effect=AssertionError("PROCESS_FORBIDDEN")):
            self.assertEqual(len(route_batch([source()]).packets()), 1)
            self.assertEqual(route_batch([]).to_dict()["shortfall"], 5)
            self.assertFalse(route_batch([source()], packet_limit=0).packets())
        for limit in (-1, 6, True, 1.2):
            with self.subTest(limit), self.assertRaises(ValueError):
                route_batch([source()], packet_limit=limit)
        with self.assertRaises(ValueError):
            route_batch([source()] * 51)
        with self.assertRaises(ValueError):
            route_batch(iter([source()]))

    def test_rule_content_and_priority_are_versioned(self):
        new = RuleSet(DEFAULT_RULES.version, (Rule("company_capacity", ("factory",)),))
        reordered = RuleSet(DEFAULT_RULES.version, DEFAULT_RULES.rules[::-1])
        original = route_batch([source()]).packets()[0]
        self.assertNotEqual(original.identity, route_batch([source()], rules=new).packets()[0].identity)
        self.assertNotEqual(DEFAULT_RULES.identity, reordered.identity)


class PacketTests(unittest.TestCase):
    def test_no_history_not_retrieved_and_no_release(self):
        value = packet().to_dict()["core"]
        self.assertEqual(value["history_status"], "NOT_RETRIEVED")
        self.assertEqual(value["context_status"], "NOT_RETRIEVED")
        self.assertIn("PRIOR_STATE_NOT_RETRIEVED; CHANGE_NOT_PROVEN", value["unknowns"])
        self.assertEqual(value["qa"], {"decision": "HOLD", "public_release": False})
        self.assertEqual(value["execution"], "DISABLED")
        self.assertEqual(value["future_mode"], "AUTOMATIC_WHEN_ENABLED")
        self.assertEqual(value["limits"], {"external_pages": 0, "model_calls": 0})

    def test_context_and_previous_state_are_supplied_only(self):
        old = source(1, text="Previously announced capacity.")
        span = EvidenceSpan(old.identity, old.text_hash, 0, len(old.text), old.text)
        result = packet(context=(old,), previous_state=(span,))
        self.assertEqual(result.core()["history_status"], "SUPPLIED_ONLY")
        self.assertEqual(result.core()["context_status"], "SUPPLIED_ONLY")
        self.assertNotEqual(result.identity, packet().identity)
        self.assertNotEqual(result.identity, packet(context=(old,)).identity)

    def test_entities_never_promoted_to_verified(self):
        item = source(entities=(EntityCandidate("Atlas", ("company-A",)), EntityCandidate("Unknown")))
        self.assertEqual(item.to_document().verified_entity_ids, ())
        statuses = {e["status"] for e in packet(item).core()["sources"][0]["entities"]}
        self.assertEqual(statuses, {"candidate", "unresolved"})
        self.assertEqual(item.to_document().source_stage, "planned")

    def test_span_tampering_rejected_including_boolean_offsets(self):
        item = source(text="Фабрика 🏭 — лишь план.")
        valid = EvidenceSpan(item.identity, item.text_hash, 8, 9, "🏭")
        self.assertEqual(packet(item, evidence=(valid,)).evidence[0].quote, "🏭")
        for changes in ({"quote": "доставлено"}, {"text_hash": "0" * 64}, {"start": True},
                        {"start": -1}, {"end": 200}, {"source_key": "0" * 64}, {"start": 9}):
            with self.subTest(changes), self.assertRaises(ValueError):
                packet(item, evidence=(replace(valid, **changes),))
        with self.assertRaises(ValueError):
            packet(item, evidence=())

    def test_boundaries_no_silent_truncation(self):
        exact = source(text="x" * (128 * 1024))
        self.assertEqual(len(packet(exact).sources[0].text), 128 * 1024)
        with self.assertRaises(ValueError):
            source(text="я" * (64 * 1024 + 1))
        with self.assertRaises(ValueError):
            packet(exact, context=(source(1),))
        with self.assertRaises(ValueError):
            packet(context=tuple(source(i) for i in range(1, 6)))
        with self.assertRaises(ValueError):
            packet(context=(source(),))
        large = source(text="x" * (100 * 1024))
        span = EvidenceSpan(large.identity, large.text_hash, 0, len(large.text), large.text)
        with self.assertRaisesRegex(ValueError, "SERIALIZED_PACKET_TOO_LARGE"):
            packet(large, evidence=(span,) * 5)

    def test_changes_to_evidence_and_task_change_identity(self):
        original = packet()
        changes = [packet(source(text="Changed factory statement.")),
                   packet(source(source_version="v2")), packet(source(title="Updated title")),
                   packet(unknowns=("Extra unknown",)),
                   replace(original, questions=("Different question?",)),
                   replace(original, external_page_limit=1)]
        self.assertEqual(len({original.identity, *(p.identity for p in changes)}), len(changes) + 1)
        for item in changes:
            self.assertEqual(item.core()["execution"], "DISABLED")

    def test_direct_constructor_cannot_bypass_source_gates(self):
        original = packet()
        for bad in (source(sensitive=True), source(rights="UNKNOWN"), source(completeness="PARTIAL")):
            with self.subTest(bad.blockers), self.assertRaises(ValueError):
                replace(original, sources=(bad,), lead_key=bad.identity)
        with self.assertRaises(TypeError):
            ResearchPacket(**{**original.__dict__, "public_release": True})
        with self.assertRaises(ValueError):
            replace(original, receipt_sources=(source(99),))

    def test_mutation_and_serialization_cannot_change_live_packet(self):
        original = packet()
        identity = original.identity
        with self.assertRaises(FrozenInstanceError):
            original.lead_key = "changed"
        copy = json.loads(original.to_json())
        copy["core"]["qa"]["public_release"] = True
        copy["core"]["sources"][0]["text"] = "tampered"
        self.assertEqual(original.identity, identity)
        self.assertFalse(original.to_dict()["core"]["qa"]["public_release"])

    def test_bad_input_hash_timestamp_and_mutable_containers(self):
        for changes in ({"text_hash": "0" * 64}, {"received_at": "2026-10-18T09:00:00"},
                        {"sensitive": 0}, {"entities": []}, {"rights": "yes"},
                        {"text": None, "text_hash": None, "completeness": "COMPLETE"}):
            with self.subTest(changes), self.assertRaises(ValueError):
                source(**changes)
        with self.assertRaises(ValueError):
            EntityCandidate("Alias", ["company-A"])
        with self.assertRaises(ValueError):
            packet(context=[])
        for changes in ({"model_call_limit": True}, {"rule_hash": "z" * 64}, {"questions": ["Mutable"]}):
            with self.subTest(changes), self.assertRaises(ValueError):
                replace(packet(), **changes)


if __name__ == "__main__":
    unittest.main()
