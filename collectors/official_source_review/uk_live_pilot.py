"""Capture one *bounded* live UK FTS window in memory through existing reader.

Requires the repository's official_sources.py next to this module or on PYTHONPATH.
No persistence, no database client, no records in logs by default.
"""
from __future__ import annotations
import json
from uk_hold_pilot import prepare_releases, summary, PilotError

# Known historical window from the verified GitHub Action, not a current feed.
FIXED_START='2026-10-15T09:00:00'
FIXED_END='2026-10-15T10:00:00'


def prepare_bounded_live(*, transport=None, start=FIXED_START, end=FIXED_END):
    import official_sources
    responses = []
    fetch = transport if transport is not None else official_sources._get

    def capture(url, **kwargs):
        raw = fetch(url, **kwargs)
        if not isinstance(raw, bytes):
            raise PilotError('source_payload_not_bytes')
        responses.append(raw)
        return raw

    receipt = official_sources.uk_probe(start, end, max_pages=2, transport=capture)
    if receipt.get('complete') is not True:
        raise PilotError('window_not_complete')
    if len(responses) != receipt.get('pages'):
        raise PilotError('receipt_page_mismatch')
    pages=[]
    for raw in responses:
        try:
            pages.append(json.loads(raw))
        except (ValueError,UnicodeDecodeError):
            raise PilotError('source_json_invalid') from None
    result=prepare_releases(pages,verified_complete=True)
    if result['observations'] != receipt.get('records'):
        raise PilotError('receipt_record_count_mismatch')
    return result


def main():
    import argparse
    p=argparse.ArgumentParser(description='BOHUN UK HOLD only bounded live read-only preview')
    p.add_argument('--live-read-only', action='store_true', required=True)
    args=p.parse_args()
    x=prepare_bounded_live()
    print(json.dumps(summary(x),sort_keys=True))

if __name__=='__main__':
    main()
