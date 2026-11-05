"""One private pilot run: 30 existing cards + 5 news candidates. HOLD proposals only.

Nothing here writes to Supabase, Notion or the site. Public logs carry counters only;
the report leaves the runner only as CMS ciphertext for the owner's certificate.
"""
import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import gzip
import html
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(ROOT / 'experiments/exp016_enrichment')):
    if path not in sys.path:
        sys.path.insert(0, path)

from contracts import EvidenceObservation, SourceReceipt, digest  # noqa: E402
from evidence import proposal, validate  # noqa: E402
from experiments.exp016_live_pilot import live_extract as lx  # noqa: E402
from experiments.exp016_live_pilot.live_seal import seal, sha  # noqa: E402
from experiments.exp016_live_pilot.live_sources import Budget, LimitReached, fetch_source  # noqa: E402

CONFIRMATION = 'PRIVATE_PILOT_30_PLUS_5'
REPOSITORY = 'nab0k/bohun-the-field'
DENIED_PREFIXES = ('SUPABASE_', 'NOTION_', 'CLOUDFLARE_', 'OPENAI_', 'GROQ_', 'MISTRAL_', 'INOREADER_')
TARGET_EXISTING = 20
# These mean the claim could not be tied to this text or this company, so it is not useful evidence.
NOT_USEFUL = {'NON_LITERAL_SPAN', 'FIELD_NOT_IN_CANVAS', 'SUBJECT_MISMATCH', 'INVALID_LOCATOR', 'EMPTY_DOES_NOT_CLEAR'}
LEGAL_FORMS = {'gmbh', 'ltd', 'limited', 'ag', 'ab', 'oy', 'sa', 'srl', 'nv', 'bv', 'sro', 'inc', 'llc', 'plc',
               'kg', 'co', 'the', 'and', 'group', 'as', 'spa', 'sp', 'zoo'}


def guard(env=None):
    env = os.environ if env is None else env
    checks = [sys.platform == 'linux', env.get('GITHUB_ACTIONS') == 'true', env.get('RUNNER_OS') == 'Linux',
              env.get('GITHUB_REPOSITORY') == REPOSITORY, env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch',
              env.get('GITHUB_REF') == 'refs/heads/main', env.get('PILOT_CONFIRMATION') == CONFIRMATION,
              re.fullmatch('[0-9a-f]{40}', env.get('EXPECTED_SHA', '')) is not None,
              env.get('EXPECTED_SHA') == env.get('GITHUB_SHA') == env.get('GITHUB_WORKFLOW_SHA')]
    if not all(checks):
        raise RuntimeError('CLOUD_MANUAL_EXACT_SHA_ONLY')
    if env.get('GEMINI_API_KEY') or any(k.startswith(DENIED_PREFIXES) for k in env):
        raise RuntimeError('WORKING_CREDENTIAL_DENIED')


def load_plan(path=None):
    return json.loads(Path(path or HERE / 'pilot_plan.json').read_text())


def load_packet(encoded, expected_sha256):
    if not isinstance(encoded, str) or not encoded.strip():
        raise RuntimeError('PACKET_MISSING')
    raw = gzip.decompress(base64.b64decode(encoded, validate=True))
    if sha(raw) != expected_sha256:
        raise RuntimeError('PACKET_PIN')
    packet = json.loads(raw)
    if packet.get('format') != 'exp016.live_pilot.v1' or not isinstance(packet.get('cases'), list):
        raise RuntimeError('PACKET_FORMAT')
    return packet


def tokens(name):
    words = re.findall(r'[^\W\d_]{3,}|[^\W_]*\d[^\W_]*', str(name).lower())
    return {w for w in words if w not in LEGAL_FORMS}


def subject_matches(company, subject):
    wanted, seen = tokens(company), tokens(subject)
    return bool(wanted and seen and wanted & seen)


def row_for(case):
    return {'id': case['id'], 'entity_kind': case.get('entity_kind', 'company'), 'qa_status': case.get('qa_status'),
            'public_release': False, 'excluded': False, 'identifiers': [], 'fields': case.get('fields', {}),
            'blocked_fields': case.get('blocked_fields', [])}


def assess(case, source, page, text, claim):
    """Reuse the shared admission rules. Identity is never confirmed by this pilot."""
    attrs = claim.get('attributes') or {}
    section, field = str(attrs.get('section')), str(attrs.get('field'))
    modality = attrs.get('modality') or 'asserted'
    rec = SourceReceipt(digest(source['url']), source['url'], source.get('publisher') or '', 'pilot-page',
                        page['received_at'], page['received_at'], page.get('html_sha256') or '',
                        page.get('text_sha256') or '', 'text', 'v1', 'EXP016 C2-04 private pilot')
    locator = {'start': claim.get('start'), 'end': claim.get('end'), 'quote': claim.get('text')}
    obs = EvidenceObservation(case['id'], section, field, claim.get('text'), source['source_type'], rec.key, locator,
                              (), modality=modality)
    shared = {'source_id': rec.source_id, 'format': 'text', 'text': text, 'source_type': source['source_type']}
    row = row_for(case)
    reasons = [r for r in validate(obs, shared, rec, row, {}) if r not in {'WRONG_SUBJECT', 'ISSUER_NOT_BOUND'}]
    reasons.append('IDENTITY_NOT_CONFIRMED')
    if not subject_matches(case['name'], attrs.get('subject')):
        reasons.append('SUBJECT_MISMATCH')
    if source['source_type'] == 'company_self_claim':
        reasons.append('SELF_CLAIM')
    if modality == 'planned':
        reasons.append('PLANNED_NOT_FACT')
    if modality == 'negative':
        reasons.append('NEGATIVE_CLAIM')
    change = proposal(obs, row, row, tuple(reasons))
    if case.get('country') == 'UA' and section == 'geography' and field != 'country':
        shown = {'quote': '[REDACTED_UA_LOCATION]', 'value': '[REDACTED_UA_LOCATION]'}
    else:
        shown = {'quote': claim.get('text'), 'value': claim.get('text')}
    useful = change.operation in {'fill', 'update'} and section != 'sources' and not NOT_USEFUL & set(change.reasons)
    return {'section': section, 'field': field, 'modality': modality, 'subject': attrs.get('subject'),
            'source_url': source['url'], 'source_type': source['source_type'], 'received_at': page['received_at'],
            'start': claim.get('start'), 'end': claim.get('end'), **shown, 'operation': change.operation,
            'status': change.status, 'reasons': list(change.reasons), 'useful': useful,
            'observation_key': obs.key, 'change_key': change.key}


def process(packet, *, fetch, extract):
    """Fetch and extract are injected, so a replay runs the same code with no network or model."""
    cases, stopped = [], None
    for case in packet['cases']:
        out = {k: case.get(k) for k in ('id', 'name', 'kind', 'stratum', 'qa_status', 'country', 'fields', 'novelty')}
        out.update(sources=[], proposals=[])
        for source in case.get('sources', []):
            if stopped:
                out['sources'].append({'url': source['url'], 'status': 'NOT_RUN_' + stopped})
                continue
            try:
                page = fetch(source['url'])
            except LimitReached as exc:
                stopped = str(exc)
                out['sources'].append({'url': source['url'], 'status': 'NOT_RUN_' + stopped})
                continue
            entry = {'url': source['url'], 'status': page['status'], 'source_type': source['source_type']}
            if page['status'] != 'CAPTURED':
                out['sources'].append(entry)
                continue
            text = page['text'][:lx.MAX_TEXT_CHARS]
            entry.update(text_sha256=page.get('text_sha256'), html_sha256=page.get('html_sha256'),
                         received_at=page['received_at'], chars=len(page['text']),
                         truncated=len(page['text']) > lx.MAX_TEXT_CHARS)
            claims, code, halt = extract(text, case['name'])
            entry['model'] = code or 'OK'
            if halt:
                stopped = code
            for claim in claims:
                out['proposals'].append(assess(case, source, page, text, claim))
            out['sources'].append(entry)
        cases.append(out)
    return {'cases': cases, 'stopped': stopped}


def keys_of(result):
    return sorted(p['observation_key'] for c in result['cases'] for p in c['proposals']), \
        sorted(p['change_key'] for c in result['cases'] for p in c['proposals'])


def counts(result, budget):
    cases = result['cases']
    existing = [c for c in cases if c['kind'] == 'existing']
    proposals = [p for c in cases for p in c['proposals']]
    sources = [s for c in cases for s in c['sources']]
    return {'cases': len(cases), 'existing': len(existing), 'controls': sum(c['kind'] == 'control' for c in cases),
            'new_candidates': sum(c['kind'] == 'new' for c in cases),
            'existing_with_useful_proposal': sum(any(p['useful'] for p in c['proposals']) for c in existing),
            'target_existing': TARGET_EXISTING,
            'new_with_useful_proposal': sum(any(p['useful'] for p in c['proposals']) for c in cases if c['kind'] == 'new'),
            'controls_with_useful_proposal': sum(any(p['useful'] for p in c['proposals'])
                                                 for c in cases if c['kind'] == 'control'),
            'proposals': len(proposals), 'useful': sum(p['useful'] for p in proposals),
            'accepted': sum(p['status'] == 'ACCEPTED_PRIVATE' for p in proposals),
            'source_status': dict(Counter(s['status'] for s in sources)),
            'model_status': dict(Counter(s.get('model') for s in sources if s.get('model'))),
            'documents': budget.documents, 'model_posts': budget.posts, 'stopped': result['stopped']}


def extractor(transport):
    def extract(text, company):
        if getattr(transport, 'stopped', None):
            return [], transport.stopped, True
        before = getattr(transport, 'last_error', None)
        try:
            return lx.run_langextract(text, company, transport), None, False
        except Exception:
            stopped = getattr(transport, 'stopped', None)
            last = getattr(transport, 'last_error', None)
            return [], stopped or (last if last != before else None) or 'EXTRACT_PARSE_FAILED', bool(stopped)
    return extract


def report_html(result):
    def e(value):
        return html.escape(str(value if value is not None else '—'))
    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8">'
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
             '<title>Пилот компаний</title><style>body{font:16px system-ui;margin:2rem;max-width:70rem}'
             'td,th{border:1px solid #bbb;padding:.5rem;vertical-align:top}table{border-collapse:collapse;width:100%}'
             '.m{color:#555;font-size:14px}</style><h1>Пилот компаний: приватный отчёт</h1>'
             '<p>Все предложения в статусе HOLD. Цитата подтверждает только то, что текст это говорит: '
             'смысл, юрлицо и самозаявления не проверены.</p><pre>', e(json.dumps(result['counts'], ensure_ascii=False, indent=1)), '</pre>']
    for case in result['cases']:
        parts += ['<h2>', e(case['name']), '</h2><p class="m">', e(case['kind']), ' · ', e(case['stratum']), ' · страна: ',
                  e(case['country']), '</p><ul>']
        parts += ['<li class="m">' + e(s['url']) + ': ' + e(s['status']) + ' / ' + e(s.get('model')) + '</li>' for s in case['sources']]
        parts.append('</ul>')
        if case['proposals']:
            parts.append('<table><tr><th>Раздел</th><th>Цитата</th><th>Причины удержания</th></tr>')
            for p in case['proposals']:
                parts += ['<tr><td>', e(p['section']), '.', e(p['field']), '</td><td>', e(p['quote']), '<br><span class="m">',
                          e(p['source_url']), '</span></td><td class="m">', e(', '.join(p['reasons'])), '</td></tr>']
            parts.append('</table>')
    return ''.join(parts) + '</html>'


def run_pilot(packet, *, key, capture_fn=None, run=None, budget=None):
    """Live pass, then a replay of the saved pages and answers. Returns the private result."""
    budget = budget or Budget()
    transport = lx.GeminiTransport(key, budget)
    if run is not None:
        transport.post = run
    pages = {}

    def fetch(url):
        page = fetch_source(url, budget, **({'capture_fn': capture_fn} if capture_fn else {}))
        pages[url] = page
        return page

    result = process(packet, fetch=fetch, extract=extractor(transport))
    result['counts'] = counts(result, budget)
    posts, documents = budget.posts, budget.documents
    replay_transport = lx.ReplayTransport(transport.answers)
    replay = process(packet, fetch=lambda url: pages.get(url, {'status': 'NOT_RUN'}), extract=extractor(replay_transport))
    new_obs = set(keys_of(replay)[0]) - set(keys_of(result)[0])
    new_changes = set(keys_of(replay)[1]) - set(keys_of(result)[1])
    result['replay'] = {'new_observations': len(new_obs), 'new_changes': len(new_changes),
                        'same_keys': keys_of(replay) == keys_of(result) or bool(result['stopped']),
                        'network_documents': budget.documents - documents, 'network_posts': budget.posts - posts}
    result['pages'] = {u: {k: p.get(k) for k in ('status', 'text_sha256', 'html_sha256')} | {'text': (p.get('text') or '')[:lx.MAX_TEXT_CHARS]}
                       for u, p in pages.items()}
    result['answers'] = transport.answers
    result['usage_tokens'] = [t for t in transport.usage if t is not None]
    result['run'] = {'finished_at': datetime.now(timezone.utc).isoformat(), 'model': lx.MODEL,
                     'limitations': ['One page per company, no search and no redirects.',
                                     'One model pass over at most 9000 characters of each page.',
                                     'Registry snapshot checked by counts on 05.11; novelty unverified.',
                                     'useful is a mechanical count, the owner review decides.']}
    return result


def selfcheck():
    """Real LangExtract with a canned answer, no network and no secrets."""
    text = 'Pilot Check GmbH makes radar sensors in Bonn. Other Corp makes boats.'
    answer = json.dumps({'extractions': [{'claim': 'Pilot Check GmbH makes radar sensors',
                                          'claim_attributes': {'subject': 'Pilot Check GmbH', 'section': 'products',
                                                               'field': 'product', 'modality': 'asserted'}}]})
    rows = lx.run_langextract(text, 'Pilot Check GmbH', lambda prompt: answer)
    if len(rows) != 1 or text[rows[0]['start']:rows[0]['end']] != rows[0]['text']:
        raise RuntimeError('LANGEXTRACT_ALIGNMENT')
    case = {'id': 'check', 'name': 'Pilot Check GmbH', 'kind': 'existing', 'sources': [
        {'url': 'https://check.example.test/', 'source_type': 'company_self_claim'}]}
    page = {'status': 'CAPTURED', 'text': text, 'received_at': '2026-11-05T00:00:00+00:00'}
    result = process({'cases': [case]}, fetch=lambda u: page, extract=lambda t, c: (rows, None, False))
    item = result['cases'][0]['proposals'][0]
    if item['status'] != 'HOLD' or not item['useful'] or 'SELF_CLAIM' not in item['reasons']:
        raise RuntimeError('ADMISSION_CHECK')
    plan = load_plan()
    if sha((HERE / 'owner-cert.pem').read_bytes()) != plan['certificate_sha256']:
        raise RuntimeError('CERTIFICATE_PIN')
    seal(b'selfcheck', HERE / 'owner-cert.pem', plan['certificate_sha256'])
    return {'status': 'PASS', 'claims': 1}


def write_private(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)


def summary(values):
    text = '## EXP-016 private pilot, counters only\n```json\n' + json.dumps(values, indent=1) + '\n```\n'
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(text)


def live():
    guard()
    plan = load_plan()
    packet = load_packet(os.environ.get('EXP016_PILOT_PACKET_B64'), plan['packet_sha256'])
    result = run_pilot(packet, key=lx.free_key())
    result['report_html'] = report_html(result)
    raw = json.dumps(result, ensure_ascii=False, indent=1).encode()
    out = Path(os.environ['RUNNER_TEMP']) / 'exp016-pilot-result'
    out.mkdir(mode=0o700)
    cipher = seal(raw, HERE / 'owner-cert.pem', plan['certificate_sha256'])
    write_private(out / 'result.cms', cipher)
    summary({**result['counts'], 'replay': result['replay'], 'result_sha256': sha(raw), 'cipher_sha256': sha(cipher)})
    if result['replay']['new_observations'] or result['replay']['new_changes'] or not result['replay']['same_keys'] \
            or result['replay']['network_documents'] or result['replay']['network_posts']:
        raise RuntimeError('REPLAY_CHANGED')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('selfcheck', 'live'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'selfcheck':
            print(json.dumps(selfcheck()))
        else:
            live()
    except Exception as exc:  # Only a bounded code: remote or private text must not reach a public log.
        code = str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{0,79}', str(exc)) else type(exc).__name__
        print('PILOT_FAILED:' + code)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
