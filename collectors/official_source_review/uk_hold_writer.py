"""Review-only source-locked SQL plan for a future, *separately approved* UK pilot.

The entrypoint deliberately performs no database writes: render_sql_plan returns
parameterized SQL and privacy-safe counts. An executor may be added only after
owner approval and an audit of every reader consuming intelligence.items.
This is not an EXP-009 intake bypass or a production importer.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from uk_hold_pilot import Candidate, SOURCE_NAME, SOURCE_URL, PilotError

SOURCE_LOOKUP_SQL = """SELECT id FROM intelligence.sources
WHERE name = %s AND url = %s AND source_type = 'procurement'
  AND collection_method = 'official_api' AND active = TRUE"""
SOURCE_REGISTER_SQL = """INSERT INTO intelligence.sources(name,url,source_type,country_code,collection_method,active)
VALUES (%s,%s,'procurement','GB','official_api',true) RETURNING id"""
# These statements are specifications for review only; none is executed by this module.
FIND_EXISTING_SQL = """SELECT id,raw_data FROM intelligence.items
WHERE source_id=%s AND external_id=%s FOR UPDATE"""
INSERT_ITEM_SQL = """INSERT INTO intelligence.items
(source_id,external_id,title,url,item_type,published_at,raw_data,first_seen_at,last_seen_at)
VALUES (%s,%s,%s,NULL,'tender',%s,%s,NOW(),NOW()) RETURNING id"""
UPDATE_SIGHTING_SQL = """UPDATE intelligence.items SET last_seen_at=NOW()
WHERE id=%s"""
UPDATE_VERSION_SQL = """UPDATE intelligence.items SET title=%s, published_at=%s,
 raw_data=%s, updated_at=NOW(),last_seen_at=NOW() WHERE id=%s"""
INSERT_CHANGE_SQL = """INSERT INTO intelligence.item_changes(item_id,source_change_id,detected_at,old_data,new_data)
VALUES (%s,NULL,NOW(),%s,%s)"""

@dataclass(frozen=True)
class Mutation:
    effect: str
    external_id: str
    item_type: str
    digest: str


def classify(existing_by_external_id: dict, candidates: list[Candidate]):
    """Read-only classification. Source-specific collision must be caught first.

    Existing snapshot is supplied by a future *read-only* SQL fetch. Matching
    version compares the stored full-source hash in raw_data.uk_fts.
    """
    if not isinstance(existing_by_external_id, dict):
        raise PilotError('invalid_existing_index')
    results = []
    seen = set()
    for c in candidates:
        if not isinstance(c, Candidate) or c.item_type != 'tender':
            raise PilotError('non_uk_tender_candidate')
        if c.external_id in seen:
            raise PilotError('duplicate_batch_identity')
        seen.add(c.external_id)
        old = existing_by_external_id.get(c.external_id)
        if old is None:
            state = 'insert_hold'
        elif not isinstance(old, dict) or not isinstance(old.get('uk_fts'), dict):
            # Never overwrite unknown/protected data by a guessed mapping.
            raise PilotError('existing_item_without_expected_provenance')
        elif old['uk_fts'].get('source_record_sha256') == c.record_sha256:
            state = 'refresh_seen_only'
        else:
            state = 'new_metadata_version_review'
        results.append(Mutation(state, c.external_id, c.item_type, c.record_sha256))
    return results


def render_sql_plan(mutations: list[Mutation]):
    from collections import Counter
    counts = Counter(m.effect for m in mutations)
    if any(m.effect not in {'insert_hold','refresh_seen_only','new_metadata_version_review'} for m in mutations):
        raise PilotError('unsafe_effect')
    return {
        'status': 'OFFLINE_PLAN_ONLY', 'qa': 'HOLD', 'ready_for_import': False,
        'source': 'uk_fts', 'source_exists_precondition': True,
        'atomic_transaction_required': True,
        'per_source_lock_required': True,
        'counts': dict(sorted(counts.items())),
        'writes_executed': 0, 'source_registration_executed': False,
        'publication_approved': False,
    }
