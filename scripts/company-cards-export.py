#!/usr/bin/env python3
"""Hand the newest auto-written company cards to the site job (B4CE-04, owner 23.11.2026 «Да, делай оба»).

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


def pack(cards, days=DAYS, today=None, limit=LIMIT):
    """(encoded, sent, dropped): cards reviewed in the last `days` days, newest first, gzip+base64 under `limit`."""
    since = ((today or date.today()) - timedelta(days=days)).isoformat()
    recent = sorted((c for c in cards if (c.get('reviewed') or '') >= since), key=lambda c: c.get('reviewed') or '',
                    reverse=True)
    keep = list(recent)
    while True:
        text = base64.b64encode(gzip.compress(json.dumps(keep, ensure_ascii=False, separators=(',', ':')).encode())).decode()
        if len(text) <= limit or not keep:
            return text, len(keep), len(recent) - len(keep)
        keep = keep[:max(1, len(keep) * 3 // 4)] if len(keep) > 1 else []


def main():
    import psycopg
    with psycopg.connect(**connect_params(), autocommit=True) as conn:
        cards = conn.execute('select public.export_company_cards_auto()').fetchone()[0] or []
    text, sent, dropped = pack(cards)
    Path(sys.argv[1] if len(sys.argv) > 1 else 'cards.b64').write_text(text)
    print(json.dumps({'cards_in_base': len(cards), 'sent': sent, 'left_for_later': dropped, 'chars': len(text)}))


if __name__ == '__main__':
    main()
