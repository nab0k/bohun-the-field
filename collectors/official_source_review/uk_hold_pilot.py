"""BOHUN UK FTS pilot: prepare HOLD-only procurement-release metadata.

Imports the already-reviewed normalize.py and R5.1 gate from the repo at runtime.
Strictly no API calls, database connection or file writes in this module.
Only *tender-tagged* releases enter the pilot; awards and other releases stay
outside this pilot until a separately reviewed policy is available.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Iterable, Mapping

import normalize
from r5_quality_hardened import _stable_identity

SOURCE_NAME = 'UK Find a Tender (OCDS)'
SOURCE_URL = 'https://www.find-tender.service.gov.uk/'
SOURCE_API = 'https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages'
MAX_WINDOW_RECORDS = 100
MAX_PILOT_TENDERS = 10

class PilotError(ValueError):
    pass

@dataclass(frozen=True)
class Candidate:
    external_id: str
    title: str
    published_at: datetime | None
    record_sha256: str
    raw_data: dict
    item_type: str = 'tender'
    url: None = None  # Do not synthesize a notice URL.


def _parsed_at(value):
    """No guessed timezone for source dates; only offset-aware RFC3339 values."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        d = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        if d.tzinfo is None:
            return None
        return d.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _metadata(release: Mapping):
    if _stable_identity('uk', release) is not None:
        raise PilotError('identity_invalid_or_overlong')
    x = normalize.uk_release(release)
    if x['verification_status'] != 'HOLD' or x['public_release'] is not False:
        raise PilotError('unsafe_normalization')
    if x['item_type_candidate'] != 'procurement_notice':
        return None
    tags = release.get('tag', []) or []
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise PilotError('invalid_tag')
    # Pilot avoids awards and other document types (cannot assert open status).
    raw = {
        'bohun': {
            'qa': 'HOLD', 'verification_status': 'UNVERIFIED',
            'public_release': False, 'capture_status': 'METADATA_ONLY',
            'ready_for_import': False, 'current_procurement_status': 'unknown',
        },
        'uk_fts': {
            'ocid': release['ocid'], 'release_id': release['id'],
            'source_api': SOURCE_API, 'tags': sorted(set(tags)),
            'source_record_sha256': x['source_record_sha256'],
            'release_date_raw': release.get('date') if isinstance(release.get('date'), str) else None,
        },
    }
    return Candidate(
        external_id=x['external_id'], title=x['title'],
        published_at=_parsed_at(release.get('date')),
        record_sha256=x['source_record_sha256'], raw_data=raw,
    )


def prepare_releases(pages: list[dict], *, verified_complete: bool, max_tenders: int = MAX_PILOT_TENDERS):
    """Prepare bounded HOLD candidates, with fail-closed pagination/conflict rules.

    verified_complete must be supplied from the guarded UK reader's successful
    complete-window return, NOT inferred from 'no next link' alone.
    """
    if type(verified_complete) is not bool or not verified_complete:
        raise PilotError('source_window_not_verified_complete')
    if not isinstance(max_tenders, int) or isinstance(max_tenders, bool) or not 1 <= max_tenders <= 10:
        raise PilotError('max_tenders_out_of_range')
    if not isinstance(pages, list) or not pages:
        raise PilotError('source_pages_missing')
    records = []
    for i, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get('releases'), list):
            raise PilotError('malformed_source_page')
        if i == len(pages) - 1 and (page.get('links') or {}).get('next'):
            raise PilotError('window_incomplete')
        records.extend(page['releases'])
        if len(records) > MAX_WINDOW_RECORDS:
            raise PilotError('source_window_too_large_for_pilot')
    by_id: dict[str, Candidate] = {}
    kinds = Counter()
    for row in records:
        if not isinstance(row, dict):
            raise PilotError('malformed_source_record')
        if _stable_identity('uk', row) is not None:
            raise PilotError('identity_invalid_or_overlong')
        norm = normalize.uk_release(row)
        kinds[norm['item_type_candidate']] += 1
        entry = _metadata(row)
        if entry is None:
            continue
        previous = by_id.get(entry.external_id)
        if previous is not None and previous.record_sha256 != entry.record_sha256:
            raise PilotError('same_release_conflicting_versions')
        by_id[entry.external_id] = entry
    selected = [by_id[k] for k in sorted(by_id)[:max_tenders]]
    return {
        'source': 'uk_fts', 'qa': 'HOLD', 'public_release': False,
        'source_window_complete': True, 'observations': len(records),
        'all_candidate_types': dict(sorted(kinds.items())),
        'pilot_candidates': len(selected),
        'eligible_unique_tender_notices': len(by_id),
        'selection_limited': len(by_id) > len(selected),
        'candidates': selected,
        'production_write_approved': False,
    }


def summary(manifest):
    """Safe public diagnostic: never print source titles, ids, hashes or raw data."""
    return {key: manifest[key] for key in (
        'source', 'qa', 'public_release', 'source_window_complete',
        'observations', 'all_candidate_types', 'pilot_candidates',
        'eligible_unique_tender_notices', 'selection_limited',
        'production_write_approved',
    )}


def load_bounded_fixture(path):
    """Fixture mode only. External caller must obtain public data lawfully."""
    from pathlib import Path
    p = Path(path)
    if p.stat().st_size > 16_000_000:
        raise PilotError('fixture_too_large')
    obj = json.loads(p.read_text('utf-8'))
    if isinstance(obj, dict):
        obj = [obj]
    if not isinstance(obj, list):
        raise PilotError('fixture_not_a_page_or_list')
    return obj


def main():
    import argparse
    ap = argparse.ArgumentParser(description='OFFLINE, UK FTS HOLD-only pilot planner')
    ap.add_argument('--fixture', required=True, help='UK OCDS response JSON, in local file only')
    ap.add_argument('--verified-complete', action='store_true', help='Must have guarded provenance receipt')
    ap.add_argument('--max-tenders', type=int, default=10)
    args = ap.parse_args()
    result = prepare_releases(load_bounded_fixture(args.fixture), verified_complete=args.verified_complete,
                              max_tenders=args.max_tenders)
    print(json.dumps(summary(result), ensure_ascii=False, sort_keys=True))

if __name__ == '__main__':
    main()
