#!/usr/bin/env python3
"""Hand the newest auto-written company cards and the companies found in the news to the site job (B4CE-04, owner 23.11.2026 «Да, делай оба»).

The site job used to read the cards from Supabase with its own service key. Now the FIELD autofill, which already
has database access, exports them (public.export_company_cards_auto()) and passes them with the dispatch as one
gzip+base64 input, so the site repository needs no database key. Only cards reviewed in the last DAYS days go,
newest first, cut to fit the dispatch limit; the site never replaces a published profile, so a card sent twice
is harmless. Prints counts only.
"""
import base64
import gzip
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.companies.pg_store import connect_params  # noqa: E402

DAYS = 7
LIMIT = 60000  # characters; GitHub allows about 65 000 for all dispatch inputs together


CATALOGUE_SQL = """select name, coalesce(country, '') as country, created_at::date::text as added
from companies.company where flags ? 'from_news' and status = 'candidate' order by created_at desc, id"""


def _encode(obj):
    return base64.b64encode(gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode())).decode()


def pack(cards, catalogue=(), days=DAYS, today=None, limit=LIMIT):
    """(encoded, sent_cards, sent_catalogue, left): {"cards": cards reviewed in the last `days` days, newest first,
    "catalogue": companies found in the news (owner 23.11.2026: a link to the catalogue marked "from the news,
    not verified")}, gzip+base64 under `limit`. Cut the catalogue first, then the oldest cards."""
    since = ((today or date.today()) - timedelta(days=days)).isoformat()
    when = lambda c: c.get('updated') or c.get('reviewed') or ''  # noqa: E731
    cards = sorted((c for c in cards if when(c) >= since), key=when, reverse=True)
    cat = [{'name': c['name'], 'country': c['country']} for c in catalogue]
    total = len(cards) + len(cat)
    while True:
        text = _encode({'cards': cards, 'catalogue': cat})
        if len(text) <= limit or not (cards or cat):
            return text, len(cards), len(cat), total - len(cards) - len(cat)
        if cat:
            cat = cat[:len(cat) * 3 // 4] if len(cat) > 1 else []
        else:
            cards = cards[:len(cards) * 3 // 4] if len(cards) > 1 else []


def chunks(cards, catalogue, limit=LIMIT):
    """[encoded] parts, each under `limit`: the news companies first (small, many fit in one part), then the cards,
    newest first (owner 23.11.2026: one dispatch could not carry 222 changed cards and the catalogue together)."""
    out, cat = [], [{'name': c['name'], 'country': c['country']} for c in catalogue]
    while cat:
        n = len(cat)
        while n > 1 and len(_encode({'cards': [], 'catalogue': cat[:n]})) > limit:
            n = n * 3 // 4
        out.append(_encode({'cards': [], 'catalogue': cat[:n]}))
        cat = cat[n:]
    rest = sorted(cards, key=lambda c: c.get('updated') or c.get('reviewed') or '', reverse=True)
    while rest:
        n = len(rest)
        while n > 1 and len(_encode({'cards': rest[:n], 'catalogue': []})) > limit:
            n = n * 3 // 4
        out.append(_encode({'cards': rest[:n], 'catalogue': []}))
        rest = rest[n:]
    return out


def main():
    """export.py OUT_DIR [HOURS]: the cards changed and the news companies added in the last HOURS (default a week),
    as part files OUT_DIR/part-NNN.b64 for one site dispatch each."""
    import psycopg
    from datetime import datetime, timezone
    from psycopg.rows import dict_row
    out = Path(sys.argv[1] if len(sys.argv) > 1 else 'cards')
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else DAYS * 24
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with psycopg.connect(**connect_params(), autocommit=True, row_factory=dict_row) as conn:
        cards = conn.execute('select public.export_company_cards_b4(%(d)s) as c', {'d': since.date()}).fetchone()['c'] or []
        catalogue = conn.execute(CATALOGUE_SQL.replace('order by', 'and created_at >= %(t)s order by'), {'t': since}).fetchall()
    if out.suffix:  # a caller passing one file (kb-daily before the feed): the cards first, as before
        text, sent, sent_cat, left = pack(cards, catalogue)
        out.write_text(text if (sent or sent_cat) else '')
        print(json.dumps({'cards': len(cards), 'cards_sent': sent, 'catalogue_sent': sent_cat, 'left_for_later': left}))
        return
    parts = chunks(cards, catalogue)
    if True:
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob('part-*.b64'):
            old.unlink()
        for n, p in enumerate(parts, 1):
            (out / f'part-{n:03}.b64').write_text(p)
    print(json.dumps({'cards': len(cards), 'catalogue': len(catalogue), 'parts': len(parts), 'hours': hours}))


if __name__ == '__main__':
    main()
