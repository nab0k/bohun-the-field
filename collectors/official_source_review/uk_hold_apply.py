"""Guarded UK pilot SQL writer. NEVER called by scheduler or default paths.

Requires all distinct manual approvals, UK source row pre-existence and bounded
reader receipt. No publication permission is provided by inserting metadata.
This program does not register the source or create/alter database objects.
"""
from __future__ import annotations
import os

from uk_hold_pilot import PilotError, SOURCE_NAME, SOURCE_URL, Candidate
from uk_hold_writer import (
    SOURCE_LOOKUP_SQL, FIND_EXISTING_SQL, INSERT_ITEM_SQL,
    UPDATE_SIGHTING_SQL, UPDATE_VERSION_SQL, INSERT_CHANGE_SQL,
)

# A fixed transaction-level advisory lock serializes all UK pilot runs in this DB.
UK_LOCK = 6846691490514231850


def approval_check():
    """Environment reference values are guardrails, not replacement for owner approval."""
    a = os.environ.get('BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF', '')
    b = os.environ.get('BOHUN_UK_HOLD_READERS_AUDIT_REF', '')
    if not a.startswith('owner-approved:') or not b.startswith('reviewed:'):
        raise PilotError('explicit_db_write_and_hold_reader_approvals_required')
    if not os.environ.get('SUPABASE_DB_PASSWORD'):
        raise PilotError('database_credential_missing')
    return a, b


def _conn_options():
    return {
        'host': os.environ.get('SUPABASE_DB_HOST','aws-1-eu-central-1.pooler.supabase.com'),
        'port': int(os.environ.get('SUPABASE_DB_PORT','5432')),
        'dbname':'postgres',
        'user':os.environ.get('SUPABASE_DB_USER','postgres.tecrucawzukxqtchpxrh'),
        'password':os.environ['SUPABASE_DB_PASSWORD'],
        'sslmode':'require', 'connect_timeout':15,
    }


def execute_one_transaction(candidates, *, connect=None):
    """Only explicit caller may run this. Fails closed; rollback on any failure.

    No RLS bypass or publication attempt. Existing source name/URL required.
    All journal inserts are in same transaction as corresponding item update.
    """
    approval_check()
    if not isinstance(candidates, list) or len(candidates)>10 or not candidates:
        raise PilotError('pilot_candidate_count_must_be_1_to_10')
    from uk_hold_writer import classify
    # Validate batch identities and types without consulting DB.
    classify({}, candidates)
    if connect is None:
        import psycopg
        connect=psycopg.connect
        from psycopg.types.json import Jsonb
    else:
        Jsonb=lambda x:x  # fake connector for tests only
    # Production driver import uses Jsonb; fake adapter passes dict directly.
    with connect(**_conn_options()) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(%s)',(UK_LOCK,))
                cur.execute(SOURCE_LOOKUP_SQL,(SOURCE_NAME,SOURCE_URL))
                found=cur.fetchone()
                if found is None:
                    raise PilotError('uk_source_must_be_pre_registered')
                source_id=int(found[0]); counts={'insert_hold':0,'refresh_seen_only':0,'new_metadata_version_review':0}
                for c in candidates:
                    cur.execute(FIND_EXISTING_SQL,(source_id,c.external_id))
                    existing=cur.fetchone()
                    if existing is None:
                        cur.execute(INSERT_ITEM_SQL,(source_id,c.external_id,c.title,c.published_at,Jsonb(c.raw_data)))
                        inserted=cur.fetchone()
                        if not inserted:
                            raise PilotError('insert_id_missing')
                        counts['insert_hold']+=1
                    else:
                        item_id,old_data=existing
                        if not isinstance(old_data,dict) or not isinstance(old_data.get('uk_fts'),dict):
                            raise PilotError('existing_item_provenance_mismatch')
                        if old_data['uk_fts'].get('source_record_sha256')==c.record_sha256:
                            cur.execute(UPDATE_SIGHTING_SQL,(item_id,))
                            counts['refresh_seen_only']+=1
                        else:
                            cur.execute(UPDATE_VERSION_SQL,(c.title,c.published_at,Jsonb(c.raw_data),item_id))
                            cur.execute(INSERT_CHANGE_SQL,(
                                item_id,Jsonb(old_data),Jsonb(c.raw_data)))
                            counts['new_metadata_version_review']+=1
                # No item is marked verified, ready_for_import or public_release.
                return {'qa':'HOLD','committed_counts':counts,'public_release':False,
                        'source_id':source_id,'verified_defence_relevance':0}
