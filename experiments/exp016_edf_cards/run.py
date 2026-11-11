"""One cloud pass: home pages of EDF candidates, one free model call each, sealed report.

Public logs carry counters only. Nothing is written to Supabase, Notion or the site.
"""
import base64
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.exp016_edf_cards import cards as cd  # noqa: E402
from experiments.exp016_edf_cards.fetch import Budget, fetch_home  # noqa: E402
from experiments.exp016_live_pilot import live_extract as lx, live_sources  # noqa: E402
from experiments.exp016_live_pilot.live_seal import seal, sha  # noqa: E402

CONFIRMATION = 'EDF_CARDS_ONE_RUN'
REPOSITORY = 'nab0k/bohun-the-field'
DENIED = ('SUPABASE_', 'NOTION_', 'CLOUDFLARE_', 'OPENAI_', 'GROQ_', 'MISTRAL_', 'INOREADER_')
CERT = ROOT / 'experiments/exp016_live_pilot/owner-cert.pem'


def guard(env=None):
    env = os.environ if env is None else env
    ok = [sys.platform == 'linux', env.get('GITHUB_ACTIONS') == 'true', env.get('GITHUB_REPOSITORY') == REPOSITORY,
          env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', env.get('GITHUB_REF') == 'refs/heads/main',
          env.get('CARDS_CONFIRMATION') == CONFIRMATION, re.fullmatch('[0-9a-f]{40}', env.get('EXPECTED_SHA', '')) is not None,
          env.get('EXPECTED_SHA') == env.get('GITHUB_SHA') == env.get('GITHUB_WORKFLOW_SHA')]
    if not all(ok):
        raise RuntimeError('CLOUD_MANUAL_EXACT_SHA_ONLY')
    if env.get('GEMINI_API_KEY') or any(k.startswith(DENIED) for k in env):
        raise RuntimeError('WORKING_CREDENTIAL_DENIED')


def load_packet(env, pin):
    text = ''.join(env.get('EXP016_CARDS_PACKET_' + str(n), '') for n in range(1, 5))
    if not text.strip():
        raise RuntimeError('PACKET_MISSING')
    raw = gzip.decompress(base64.b64decode(text, validate=True))
    if hashlib.sha256(raw).hexdigest() != pin:
        raise RuntimeError('PACKET_PIN')
    packet = json.loads(raw)
    if packet.get('format') != 'exp016.edf_cards.v1' or not isinstance(packet.get('companies'), list):
        raise RuntimeError('PACKET_FORMAT')
    return packet


RETRY_CODES = {'MODEL_HTTP_503', 'MODEL_HTTP_500', 'MODEL_HTTP_502', 'MODEL_HTTP_504'}
RETRY_DELAY = 20.0


def ask(transport, prompt, budget):
    """One delayed second attempt only after an overloaded-server answer. Quota and key refusals stop at once."""
    try:
        return transport(prompt)
    except lx.ModelFailed as exc:
        if str(exc) not in RETRY_CODES:
            raise
        budget.sleep(RETRY_DELAY)
        return transport(prompt)


def run(packet, *, transport, budget, fetch=None):
    fetch = fetch or (lambda url: fetch_home(url, budget))
    out, stopped = [], None
    for company in packet['companies']:
        if stopped:
            out.append(cd.build(company, {'status': 'NOT_RUN_' + stopped}, model_status='NOT_RUN'))
            continue
        try:
            page = fetch(company['website'])
        except lx.LimitReached as exc:
            stopped = str(exc)
            out.append(cd.build(company, {'status': 'NOT_RUN_' + stopped}, model_status='NOT_RUN'))
            continue
        if page['status'] != 'CAPTURED':
            out.append(cd.build(company, page, model_status='NOT_RUN'))
            continue
        text = page['text'][:cd.MAX_TEXT_CHARS]
        try:
            data, dropped = cd.check(cd.parse(ask(transport, cd.prompt_for(company['name'], text), budget)), text)
            out.append(cd.build(company, page, data, dropped))
        except (lx.ModelStopped, lx.LimitReached) as exc:
            stopped = str(exc)
            out.append(cd.build(company, page, model_status=stopped))
        except lx.ModelFailed as exc:
            out.append(cd.build(company, page, model_status=str(exc)))
        except (ValueError, json.JSONDecodeError):
            out.append(cd.build(company, page, model_status='ANSWER_INVALID'))
    return out, stopped


def counts(cards, budget, stopped):
    return {'companies': len(cards), 'site_status': dict(Counter(c['site_status'] for c in cards)),
            'redirected': sum(c['redirected'] for c in cards), 'model_status': dict(Counter(c['model_status'] for c in cards)),
            'verdict': dict(Counter(c['verdict'] for c in cards)), 'class': dict(Counter(c['class'] for c in cards)),
            'with_summary': sum(bool(c['summary']) for c in cards), 'dropped_unquoted': sum(c['dropped_unquoted'] for c in cards),
            'documents': budget.documents, 'model_posts': budget.posts, 'stopped': stopped}


def report_html(cards, totals):
    e = lambda v: html.escape(str(v if v is not None else '—'))  # noqa: E731
    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
             'content="default-src \'none\'; style-src \'unsafe-inline\'"><title>Карточки EDF</title><style>body{font:16px system-ui;'
             'margin:2rem;max-width:62rem}.m{color:#555;font-size:14px}q{color:#333}</style><h1>Карточки кандидатов EDF</h1>'
             '<p>Сведения с сайта это заявления компании. Каждое утверждение с цитатой, утверждения без найденной цитаты отброшены.</p><pre>',
             e(json.dumps(totals, ensure_ascii=False, indent=1)), '</pre>']
    for c in cards:
        parts += ['<h2>', e(c['name']), '</h2><p class="m">', e(c['country']), ' · ', e(c['site_url'] or c['website_listed']),
                  ' · сайт: ', e(c['site_status']), ' · класс: ', e(c['class']), ' · предварительно: ', e(c['verdict']), '</p>']
        for field, title in (('summary', 'Коротко'), ('products', 'Продукты'), ('markets', 'Рынки'), ('claims', 'Заявляет')):
            if c[field]:
                parts.append('<p><b>' + title + ':</b></p><ul>' + ''.join('<li>' + e(i['text']) + ' <q class="m">' + e(i['quote']) + '</q></li>' for i in c[field]) + '</ul>')
        parts.append('<p><b>EDF:</b></p><ul>' + ''.join('<li class="m">' + e(p['acronym']) + ', ' + e(p['call']) + ', ' + e(p['role']) + ', ' + e(p['eur']) + ' евро, ' + e(p['signed']) + ', ' + e(p['status']) + '</li>' for p in c['edf']) + '</ul>')
    return ''.join(parts) + '</html>'


def summary(values):
    text = '## EXP-016 EDF cards, counters only\n```json\n' + json.dumps(values, indent=1, sort_keys=True) + '\n```\n'
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(text)


def main(argv=None):
    try:
        guard()
        plan = json.loads((HERE / 'plan.json').read_text())
        packet = load_packet(os.environ, plan['packet_sha256'])
        live_sources.MAX_SECONDS = plan['limits']['seconds']
        budget = Budget(max_documents=plan['limits']['documents'], max_posts=plan['limits']['model_posts'],
                        interval=plan['limits']['post_interval_seconds'])
        transport = lx.GeminiTransport(lx.free_key(), budget)
        cards, stopped = run(packet, transport=transport, budget=budget)
        totals = counts(cards, budget, stopped)
        raw = json.dumps({'finished_at': datetime.now(timezone.utc).isoformat(), 'model': lx.MODEL, 'counts': totals, 'cards': cards,
                          'report_html': report_html(cards, totals)}, ensure_ascii=False, indent=1).encode()
        out = Path(os.environ['RUNNER_TEMP']) / 'exp016-edf-cards'
        out.mkdir(mode=0o700)
        cipher = seal(raw, CERT, plan['certificate_sha256'])
        fd = os.open(out / 'result.cms', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(cipher)
        summary({**totals, 'result_sha256': sha(raw), 'cipher_sha256': sha(cipher)})
        return 0
    except Exception as exc:  # Bounded code only: page or driver text must not reach a public log.
        code = str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{0,79}', str(exc)) else type(exc).__name__
        print('CARDS_FAILED:' + code)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
