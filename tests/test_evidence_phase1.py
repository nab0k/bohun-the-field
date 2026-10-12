"""Offline tests for intelligence/evidence_phase1 (no network, no secrets, no DB).

Run: python -m unittest tests.test_evidence_phase1 -v
LangExtract tests run the real library resolver with an injected fake model and
are skipped if langextract is not installed.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intelligence.evidence_phase1 import documents as D  # noqa: E402
from intelligence.evidence_phase1.entailment import MiniCheckScorer, apply_entailment  # noqa: E402
from intelligence.evidence_phase1.extractors import GeminiDirectExtractor  # noqa: E402
from intelligence.evidence_phase1.guards import apply_guards  # noqa: E402
from intelligence.evidence_phase1.metrics import project_costs  # noqa: E402
from intelligence.evidence_phase1.pipeline import ReplayExtractor, run  # noqa: E402
from intelligence.evidence_phase1.record import build_record, sha256_text, validate_record  # noqa: E402

FIX = ROOT / "intelligence/fixtures/evidence_phase1"
DOCS = {d["document_id"]: d for d in D.load_fixtures()}
NEG = DOCS["fixture:syn-contract-negation"]
CAN = DOCS["fixture:syn-cancelled-dates"]


def rec(doc, **cand):
    r = build_record(document=doc, candidate=cand, extractor_name="test")
    return apply_guards(r, doc["text"])


class Offsets(unittest.TestCase):
    def test_offsets_slice_exact_evidence_from_stored_text(self):
        span = "it awarded Kestrel Dynamics a contract worth EUR 48 million"
        r = rec(NEG, claim_text="x.", evidence_text=span, modality="announced",
                asserted_by="Valdorian Ministry of Defence")
        self.assertEqual(NEG["text"][r["char_start"]:r["char_end"]], span)
        self.assertEqual(r["document_hash"], sha256_text(NEG["text"]))
        self.assertEqual(validate_record(r, NEG["text"]), [])

    def test_wrong_extractor_offsets_are_not_trusted(self):
        span = "The radar was first tested in June 2025"
        r = rec(NEG, claim_text="x.", evidence_text=span, modality="occurred", char_start=0, char_end=len(span))
        self.assertIn("EXTRACTOR_OFFSETS_DO_NOT_MATCH_SPAN", r["hold_reasons"])
        self.assertEqual(NEG["text"][r["char_start"]:r["char_end"]], span)

    def test_tampered_record_fails_validation(self):
        r = rec(NEG, claim_text="x.", evidence_text="Kestrel Dynamics will deliver", modality="planned")
        r["evidence_text"] = "Kestrel Dynamics delivered"
        self.assertIn("EVIDENCE_TEXT_NOT_AT_OFFSETS", validate_record(r, NEG["text"]))
        self.assertIn("DOCUMENT_HASH_MISMATCH", validate_record(r, NEG["text"] + " "))

    def test_ambiguous_span_is_held(self):
        r = rec(NEG, claim_text="x.", evidence_text="Kestrel Dynamics", modality="unknown")
        self.assertEqual(r["alignment_status"], "EXACT_AMBIGUOUS")
        self.assertIn("SPAN_OCCURS_MULTIPLE_TIMES", r["hold_reasons"])

    def test_whitespace_or_case_variant_is_not_evidence(self):
        r = rec(NEG, claim_text="x.", evidence_text="it awarded kestrel dynamics", modality="announced")
        self.assertIn("SPAN_NOT_IN_STORED_TEXT", r["hold_reasons"])
        self.assertIsNone(r["char_start"])


class ModalityAndAttribution(unittest.TestCase):
    def test_negation_labelled_as_occurred_is_held(self):
        r = rec(NEG, claim_text="Kestrel received a second order.", modality="occurred", asserted_by="Kestrel Dynamics",
                evidence_text="denied that the company had received a second order from the Valdorian Navy")
        self.assertIn("NEGATION_CUE_IN_SPAN_CONFLICTS_WITH_MODALITY", r["hold_reasons"])

    def test_plan_labelled_as_occurred_is_held(self):
        r = rec(NEG, claim_text="Kestrel delivered the units.", modality="occurred",
                evidence_text="Kestrel Dynamics will deliver the first units in early 2028")
        self.assertIn("PLAN_CUE_IN_SPAN_CONFLICTS_WITH_MODALITY", r["hold_reasons"])

    def test_correct_plan_label_raises_no_plan_conflict(self):
        r = rec(CAN, claim_text="Brightwater plans a plant in 2028.", modality="planned", asserted_by="Brightwater Munitions",
                evidence_text="Brightwater Munitions plans to build a new filling plant in 2028")
        self.assertFalse([h for h in r["hold_reasons"] if "PLAN" in h])

    def test_cancelled_decision_reported_as_occurred_is_held(self):
        r = rec(CAN, claim_text="Ostmark has an agreement with Brightwater.", modality="occurred",
                evidence_text="the Republic of Ostmark signed a framework agreement with Brightwater Munitions for 155mm artillery shells")
        # the cancellation is in a different sentence: Phase 1 guards only see the local window
        self.assertNotIn("CANCELLATION_CUE_IN_CONTEXT", r["hold_reasons"])
        r2 = rec(CAN, claim_text="Ostmark has an agreement.", modality="occurred",
                 evidence_text="the Ostmark procurement agency cancelled the agreement")
        self.assertIn("CANCELLATION_CUE_IN_CONTEXT", r2["hold_reasons"])

    def test_third_party_claim_without_attribution_is_held(self):
        r = rec(NEG, claim_text="Kestrel may open a line in Ostrava.", modality="speculative", asserted_by=None,
                evidence_text="Kestrel Dynamics may open a second assembly line in Ostrava")
        self.assertIn("ATTRIBUTION_CUE_BUT_ASSERTED_BY_UNKNOWN", r["hold_reasons"])
        self.assertIsNone(r["asserted_by"])

    def test_unknown_values_are_kept_unknown(self):
        r = rec(NEG, claim_text="x.", evidence_text="The radar was first tested", modality=None, asserted_by="unknown")
        self.assertEqual(r["modality"], "unknown")
        self.assertIsNone(r["asserted_by"])

    def test_out_of_vocabulary_modality_becomes_unknown_and_held(self):
        r = rec(NEG, claim_text="x.", evidence_text="The radar was first tested", modality="confirmed")
        self.assertEqual(r["modality"], "unknown")
        self.assertTrue(any(h.startswith("MODALITY_NOT_IN_VOCABULARY") for h in r["hold_reasons"]))


class Dates(unittest.TestCase):
    def test_event_date_grounded_and_differs_from_publication_date(self):
        r = rec(CAN, claim_text="Signed.", modality="occurred", event_date="2026-09-14",
                evidence_text="the Republic of Ostmark signed a framework agreement with Brightwater Munitions")
        self.assertEqual(r["event_date"], "2026-09-14")
        self.assertEqual(r["publication_date"], "2026-10-12")

    def test_publication_date_is_never_used_as_event_date(self):
        r = rec(CAN, claim_text="Cancelled.", modality="cancelled", asserted_by="Ostmark procurement agency",
                event_date="2026-10-12", evidence_text="the Ostmark procurement agency cancelled the agreement")
        self.assertIsNone(r["event_date"])
        self.assertEqual(r["event_date_candidate"], "2026-10-12")
        self.assertIn("EVENT_DATE_EQUALS_PUBLICATION_DATE_NOT_IN_EVIDENCE", r["hold_reasons"])

    def test_missing_event_date_stays_null(self):
        r = rec(NEG, claim_text="x.", evidence_text="The radar was first tested", modality="occurred")
        self.assertIsNone(r["event_date"])


class UnsupportedAndEntailment(unittest.TestCase):
    def test_invented_span_is_held(self):
        r = rec(NEG, claim_text="Kestrel opened a factory.", modality="occurred",
                evidence_text="Kestrel Dynamics opened its Ostrava factory in 2026")
        self.assertIn("SPAN_NOT_IN_STORED_TEXT", r["hold_reasons"])
        self.assertEqual(validate_record(r, NEG["text"]), [])

    def test_entailment_records_score_but_never_releases(self):
        class Fake:
            def score(self, docs, claims):
                labels = [0 if "delivered" in c else 1 for c in claims]
                return labels, [0.02 if l == 0 else 0.97 for l in labels], None, None
        recs = [rec(NEG, claim_text="Kestrel delivered the first units.", modality="planned",
                    evidence_text="Kestrel Dynamics will deliver the first units in early 2028"),
                rec(NEG, claim_text="The ministry awarded Kestrel a radar contract.", modality="announced",
                    asserted_by="Valdorian Ministry of Defence",
                    evidence_text="it awarded Kestrel Dynamics a contract worth EUR 48 million for 30 counter-drone radar units")]
        apply_entailment(recs, {NEG["document_id"]: NEG["text"]}, MiniCheckScorer(backend=Fake()))
        self.assertEqual(recs[0]["entailment_label"], "NOT_SUPPORTED")
        self.assertIn("ENTAILMENT_NOT_SUPPORTED", recs[0]["hold_reasons"])
        self.assertEqual(recs[1]["entailment_label"], "SUPPORTED")
        for r in recs:
            self.assertEqual((r["verification_status"], r["public_release"]), ("HOLD", False))

    def test_scorer_error_fails_closed(self):
        class Boom:
            def score(self, docs, claims):
                raise RuntimeError("quota")
        r = rec(NEG, claim_text="x.", evidence_text="The radar was first tested", modality="occurred")
        apply_entailment([r], {NEG["document_id"]: NEG["text"]}, MiniCheckScorer(backend=Boom()))
        self.assertIn("ENTAILMENT_ERROR:RuntimeError", r["hold_reasons"])
        self.assertIsNone(r["entailment_score"])

    def test_no_scorer_is_reported_not_skipped_silently(self):
        r = rec(NEG, claim_text="x.", evidence_text="The radar was first tested", modality="occurred")
        apply_entailment([r], {NEG["document_id"]: NEG["text"]}, None)
        self.assertIn("ENTAILMENT_NOT_RUN", r["hold_reasons"])


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.replay = ReplayExtractor(json.loads((FIX / "replay/candidates.json").read_text()))

    def test_no_full_text_is_skipped(self):
        res = run([DOCS["fixture:syn-no-full-text"]], self.replay)
        self.assertEqual(res["documents"][0]["outcome"], "SKIPPED_NO_FULL_TEXT")
        self.assertEqual(res["records"], [])

    def test_all_records_hold_and_valid(self):
        res = run(list(DOCS.values()), self.replay, max_docs=10)
        self.assertTrue(res["metrics"]["all_hold"])
        self.assertEqual(res["validation_problems"], [])
        self.assertTrue(all(r["hold_reasons"] for r in res["records"]))

    def test_quality_metrics_not_invented_without_gold(self):
        doc = dict(NEG, gold=None)
        res = run([doc], self.replay.for_document(NEG["document_id"]))
        self.assertEqual(res["metrics"]["precision_span_match"], "NOT MEASURED")
        self.assertEqual(res["metrics"]["recall_essential"], "NOT MEASURED")

    def test_cost_guard(self):
        with self.assertRaises(ValueError):
            run([], self.replay, max_docs=11)

    def test_gemini_failure_fails_closed(self):
        def transport(body):
            raise TimeoutError("timeout")
        res = run([NEG], GeminiDirectExtractor(transport=transport))
        self.assertTrue(res["documents"][0]["outcome"].startswith("EXTRACTION_FAILED:TimeoutError"))
        self.assertEqual(res["records"], [])

    def test_gemini_usage_is_recorded_from_provider(self):
        def transport(body):
            sent = json.loads(json.loads(body)["contents"][0]["parts"][0]["text"])
            self.assertNotIn("publication_date", sent)  # model never sees metadata dates
            out = {"claims": [{"claim_text": "Awarded.", "evidence_text": "it awarded Kestrel Dynamics a contract",
                               "modality": "announced", "asserted_by": "Valdorian Ministry of Defence", "event_date": None}]}
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(out)}]}}],
                    "usageMetadata": {"promptTokenCount": 812, "candidatesTokenCount": 64}}
        res = run([NEG], GeminiDirectExtractor(transport=transport))
        meta = res["documents"][0]["extraction_meta"]
        self.assertEqual((meta["prompt_tokens"], meta["output_tokens"]), (812, 64))
        self.assertEqual(res["records"][0]["char_start"], NEG["text"].index("it awarded Kestrel Dynamics a contract"))


class Safety(unittest.TestCase):
    def test_package_has_no_write_sql(self):
        for path in (ROOT / "intelligence/evidence_phase1").glob("*.py"):
            src = path.read_text()
            self.assertIsNone(re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|GRANT|TRUNCATE)\s", src), path.name)

    def test_workflow_is_manual_only_and_has_no_db_write_secret_in_extract_job(self):
        wf = (ROOT / ".github/workflows/evidence-phase1-experiment.yml").read_text()
        on_block = wf.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch", on_block)
        for trigger in ("pull_request", "schedule:"):
            self.assertNotIn(trigger, on_block)
        # push is allowed only for this experiment branch and only for the explicit run-request file
        push = on_block.split("push:", 1)[1]
        self.assertIn("branches: [experiment/evidence-phase1-20261013]", push)
        self.assertIn("paths: ['intelligence/fixtures/evidence_phase1/run-request.json']", push)
        self.assertIn("contents: read", wf)
        self.assertNotIn("${{ inputs.", wf.split("  experiment:", 1)[1])  # inputs only via validated resolve job

    def test_cost_projection_is_linear_and_explicit(self):
        rows = project_costs(prompt_tokens_per_doc=1000, output_tokens_per_doc=100, price_in_per_m=0.30,
                             price_out_per_m=2.50, entailment_cpu_s_per_doc=1.0)
        self.assertEqual(rows[0]["llm_usd_per_day"], round(1000 * (1000 * 0.30 + 100 * 2.50) / 1e6, 2))
        self.assertEqual(rows[2]["docs_per_day"], 100_000)


try:
    import langextract as lx  # noqa: E402
    from langextract.core import base_model, types  # noqa: E402
except ImportError:  # pragma: no cover
    lx = None


@unittest.skipIf(lx is None, "langextract not installed")
class LangExtractResolver(unittest.TestCase):
    """Runs the real LangExtract alignment code with a scripted fake model."""

    def _fake(self, extraction_text):
        class Fake(base_model.BaseLanguageModel):
            def infer(self, batch_prompts, **kw):
                for _ in batch_prompts:
                    yield [types.ScoredOutput(score=1.0, output=json.dumps({"extractions": [
                        {"claim": extraction_text, "claim_attributes": {"modality": "announced"}}]}))]
        return Fake()

    def _run(self, extraction_text, **resolver):
        kwargs = dict(text_or_documents=NEG["text"], prompt_description="Extract claims.",
                      examples=[lx.data.ExampleData(text="A said B.", extractions=[
                          lx.data.Extraction(extraction_class="claim", extraction_text="B")])],
                      model=self._fake(extraction_text), show_progress=False, fetch_urls=False)
        if resolver:
            kwargs["resolver_params"] = resolver
        return lx.extract(**kwargs).extractions[0]

    def test_exact_extraction_gets_matching_char_interval(self):
        from intelligence.evidence_phase1.extractors import LangExtractExtractor
        span = "it awarded Kestrel Dynamics a contract"
        out = LangExtractExtractor(model=self._fake(span)).extract(NEG["text"])
        c = out["candidates"][0]
        self.assertEqual(c["extractor_alignment"], "match_exact")
        self.assertEqual(NEG["text"][c["char_start"]:c["char_end"]], span)

    def test_library_default_accepts_fuzzy_alignment_but_ours_does_not(self):
        paraphrase = "the ministry awarded Kestrel Dynamics a radar contract"
        default = self._run(paraphrase)
        strict = self._run(paraphrase, enable_fuzzy_alignment=False, accept_match_lesser=False)
        # Documented library behaviour: with defaults a paraphrase may still get an interval.
        if default.char_interval is not None:
            self.assertNotEqual(NEG["text"][default.char_interval.start_pos:default.char_interval.end_pos], paraphrase)
        self.assertIsNone(strict.char_interval)
        r = rec(NEG, claim_text=paraphrase, evidence_text=paraphrase, modality="announced",
                extractor_alignment=strict.alignment_status.value if strict.alignment_status else "unaligned")
        self.assertIn("SPAN_NOT_IN_STORED_TEXT", r["hold_reasons"])


if __name__ == "__main__":
    unittest.main()
