"""Original pinned API checks. Missing packages are SKIP, never native PASS."""
import unittest
from adapters import (NativeUnavailable, ftm_fixture, langextract_fixture, no_network,
                      nomenklatura_fixture, trafilatura_fixture)
from sources import project_manifest
from test_core import fixture


class Native(unittest.TestCase):
    def call(self, fn, *args):
        try:
            with no_network():
                return fn(*args)
        except NativeUnavailable as exc:
            self.skipTest('NATIVE_NOT_RUN: ' + str(exc))

    def test_followthemoney_original_roundtrip(self):
        result = self.call(ftm_fixture)
        self.assertEqual(result['statement']['origin'], 'https://alpha.example/legal')
        self.assertEqual(result['statement']['original_value'], 'SYNTHETIC ALPHA')

    def test_nomenklatura_suggestion_then_explicit_judgement(self):
        result = self.call(nomenklatura_fixture)
        self.assertEqual(result['expanded_before_judgement'], 0)
        self.assertGreater(result['expanded_after_synthetic_judgement'], 0)

    def test_trafilatura_existing_adapter_original_extract(self):
        result = self.call(trafilatura_fixture, fixture()['native_html'], project_manifest())
        self.assertTrue(result['raw_legal_id_retained'])
        self.assertIn('Synthetic Alpha', result['text'])

    def test_langextract_fixture_provider_exact_alignment(self):
        data = fixture()
        result = self.call(langextract_fixture, data['native_text'], data['native_outputs'])
        self.assertEqual(result['model_transport_attempts'], 0)
        literal = [r for r in result['assertions'] if r.get('literal')]
        self.assertEqual(len(literal), 2)
        self.assertEqual(literal[0]['attributes']['polarity'], 'negative')
        self.assertTrue(all(r['status'] == 'SEMANTIC_REVIEW_REQUIRED' for r in literal))
        self.assertFalse(any(r.get('literal') and 'tanks' in r['text'] for r in result['assertions']))


if __name__ == '__main__': unittest.main()
