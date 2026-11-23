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


def main():
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(**connect_params(), autocommit=True, row_factory=dict_row) as conn:
        since = date.today() - timedelta(days=DAYS)  # cards written or changed lately (EDF details, refreshes)
        cards = conn.execute('select public.export_company_cards_b4(%(d)s) as c', {'d': since}).fetchone()['c'] or []
        catalogue = conn.execute(CATALOGUE_SQL).fetchall()
    text, sent, sent_cat, left = pack(cards, catalogue)
    Path(sys.argv[1] if len(sys.argv) > 1 else 'cards.b64').write_text(text)
    print(json.dumps({'cards_in_base': len(cards), 'cards_sent': sent, 'catalogue_sent': sent_cat,
                      'left_for_later': left, 'chars': len(text)}))


if __name__ == '__main__':
    main()
