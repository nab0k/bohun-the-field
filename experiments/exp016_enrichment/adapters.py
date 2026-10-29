"""Thin calls to original upstream APIs. Import failure is never an algorithm PASS."""
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
import json
import socket
from unittest.mock import patch

PINS = {'followthemoney': '4.11.2', 'nomenklatura': '4.17.3',
        'trafilatura': '2.3.1', 'langextract': '1.7.1'}


class NativeUnavailable(RuntimeError):
    pass


def native_status():
    result = {}
    for name, pin in PINS.items():
        try:
            actual = version(name)
        except PackageNotFoundError:
            actual = None
        result[name] = {'expected': pin, 'installed': actual,
                        'status': 'READY_FOR_TEST' if actual == pin else 'NATIVE_NOT_RUN'}
    return result


def require(name):
    actual = native_status()[name]
    if actual['status'] != 'READY_FOR_TEST':
        raise NativeUnavailable(f'{name}: required {PINS[name]}, installed {actual["installed"]}')


@contextmanager
def no_network():
    def deny(*args, **kwargs):
        raise RuntimeError('NETWORK_FORBIDDEN_IN_FIXTURE')
    class BlockedSocket(socket.socket):
        def __new__(cls, *args, **kwargs):
            return deny()
    with patch.object(socket, 'socket', BlockedSocket), patch.object(socket, 'create_connection', deny), \
            patch.object(socket, 'getaddrinfo', deny):
        yield


def ftm_fixture():
    require('followthemoney')
    from followthemoney import Dataset, Statement, StatementEntity
    dataset = Dataset({'name': 'bohun_synthetic', 'title': 'Synthetic fixtures only'})
    statement = Statement(entity_id='syn-alpha', prop='name', schema='Company',
                          value='Synthetic Alpha', original_value='SYNTHETIC ALPHA',
                          dataset=dataset.name, origin='https://alpha.example/legal',
                          first_seen='2026-10-29T00:00:00', last_seen='2026-10-29T01:00:00')
    restored = Statement.from_dict(statement.to_dict())
    entity = StatementEntity(dataset, {'id': 'syn-alpha', 'schema': 'Company'})
    entity.add_statement(restored)
    # Original statement serializer preserves origin/time/original_value; to_dict loses them.
    again = StatementEntity(dataset, entity.to_statement_dict())
    if restored.to_dict() != statement.to_dict() or again.get('name') != ['Synthetic Alpha']:
        raise AssertionError('FTM_ROUNDTRIP')
    names = [s for s in again.statements if s.prop == 'name']
    if len(names) != 1 or names[0].to_dict() != statement.to_dict():
        raise AssertionError('FTM_ENTITY_PROVENANCE_ROUNDTRIP')
    return {'statement': restored.to_dict(), 'entity': again.to_statement_dict(),
            'verified_operations': ['statement_roundtrip', 'entity_provenance_roundtrip']}


def nomenklatura_fixture():
    require('nomenklatura')
    require('followthemoney')
    from followthemoney import Dataset, StatementEntity
    from nomenklatura.db import Session
    from nomenklatura.enrich import Enricher, match, enrich
    from nomenklatura.judgement import Judgement
    from nomenklatura.resolver import Resolver
    from sqlalchemy import create_engine
    dataset = Dataset({'name': 'bohun_synthetic'})
    query = StatementEntity(dataset, {'id': 'query-alpha', 'schema': 'Company',
                                     'properties': {'name': ['Synthetic Alpha'], 'country': ['de']}})
    candidate = StatementEntity(dataset, {'id': 'external-alpha', 'schema': 'Company',
                                         'properties': {'name': ['Synthetic Alpha'], 'country': ['de']}})
    calls = {'match': 0, 'expand': 0}

    class FixtureEnricher(Enricher):
        def match(self, entity):
            calls['match'] += 1
            yield candidate

        def expand(self, entity, matched):
            calls['expand'] += 1
            yield matched

    # Cache is unused by the synthetic provider. Original wrapped API and scorer run.
    enricher = FixtureEnricher(dataset, None, {})
    engine = create_engine('sqlite:///:memory:')
    try:
        with Session(engine) as session:
            resolver = Resolver(session, create=True)
            matched = list(match(enricher, resolver, [query]))
            suggestions = list(resolver.get_candidates())
            before = list(enrich(enricher, resolver, [query]))
            if before or not suggestions or resolver.get_judgement(query.id, candidate.id) == Judgement.POSITIVE:
                raise AssertionError('NO_AUTO_MERGE_REQUIRED')
            # Explicit synthetic judgement, not production name-based admission.
            resolver.decide(query.id, candidate.id, Judgement.POSITIVE, user='synthetic-fixture-review')
            after = list(enrich(enricher, resolver, [query]))
            if not after or calls['expand'] != 1 or len(matched) != 2:
                raise AssertionError('CONFIRMED_EXPAND_REQUIRED')
            return {'suggestions': len(suggestions), 'expanded_before_judgement': len(before),
                    'expanded_after_synthetic_judgement': len(after), 'calls': calls}
    finally:
        engine.dispose()


def trafilatura_fixture(html, project_source_manifest):
    require('trafilatura')
    from sources import reuse_module
    module = reuse_module('collectors.web_monitor.content', project_source_manifest)
    text = module.extract_html(html)  # existing adapter -> original trafilatura.extract
    if not text or 'Synthetic Alpha' not in text:
        raise AssertionError('MAIN_TEXT_MISSING')
    # Legal identity must remain in separately retained raw HTML.
    if 'SYN-123456' not in html:
        raise AssertionError('RAW_LEGAL_EVIDENCE_MISSING')
    return {'text': text, 'raw_legal_id_retained': True,
            'legal_id_in_maintext': 'SYN-123456' in text, 'native_api_calls': 1}


def langextract_fixture(text, outputs):
    require('langextract')
    import langextract as lx
    from langextract.core.base_model import BaseLanguageModel
    from langextract.core.types import ScoredOutput

    class FixtureModel(BaseLanguageModel):
        def __init__(self):
            super().__init__()
            self.fixture_prompt_count = 0

        def infer(self, batch_prompts, **kwargs):
            for prompt in batch_prompts:
                self.fixture_prompt_count += 1
                yield [ScoredOutput(score=1.0, output=json.dumps({'extractions': outputs}))]

    provider = FixtureModel()
    example = lx.data.ExampleData(text='Demo makes optics.', extractions=[
        lx.data.Extraction(extraction_class='claim', extraction_text='Demo makes optics.',
                           attributes={'subject': 'Demo', 'polarity': 'positive'})])
    result = lx.extract(text_or_documents=text, prompt_description='Extract literal attributed company claims.',
                        examples=[example], model=provider, fence_output=False,
                        use_schema_constraints=False, extraction_passes=1, max_workers=1,
                        batch_length=1, max_char_buffer=10000, fetch_urls=False, show_progress=False,
                        resolver_params={'enable_fuzzy_alignment': False, 'accept_match_lesser': False,
                                         'suppress_parse_errors': False})
    assertions = []
    for extraction in result.extractions or []:
        interval = extraction.char_interval
        if interval is None:
            assertions.append({'text': extraction.extraction_text, 'status': 'UNALIGNED'})
            continue
        a, b = interval.start_pos, interval.end_pos
        literal = text[a:b] == extraction.extraction_text
        assertions.append({'text': extraction.extraction_text, 'start': a, 'end': b,
                           'attributes': extraction.attributes, 'literal': literal,
                           'status': 'SEMANTIC_REVIEW_REQUIRED'})
    return {'assertions': assertions, 'fixture_provider_prompts': provider.fixture_prompt_count,
            'model_transport_attempts': 0, 'native_extract_calls': 1}
