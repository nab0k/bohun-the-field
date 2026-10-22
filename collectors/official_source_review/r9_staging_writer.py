"""Manual-only R9 transactional writer: empty or >10-tender complete UK windows.

NOT enabled on production; no scheduler or direct API call on import. Reuses
R7.1's login identity/privilege checks, SQL and HOLD validation. A real commit
requires explicit R9 authorization AND DB credentials in the operator environment.
"""
from __future__ import annotations

import os

from r9_batch_plan import WindowBatch, BatchPlanError, prepare_batch


class R9WriteError(ValueError):
    pass


def _authorized() -> None:
    if not os.environ.get('BOHUN_UK_R9_BATCH_APPROVAL_REF', '').startswith('owner-approved:'):
        raise R9WriteError('r9_explicit_approval_required')


def _apply_cursor(cur, batch: WindowBatch, *, adapter, base) -> dict:
    """One transaction for EVERY candidate plus exactly one COMPLETE receipt.

    Empty source windows or no-tender windows still insert a COMPLETE receipt.
    Large windows are NOT committed in independently visible partial chunks.
    """
    cur.execute('SELECT pg_advisory_xact_lock(%s)', (base.LOCK_ID,))
    counts = dict(inserted=0, updated=0, repeated=0)
    for candidate in batch.candidates:
        external_id, ocid, rid, title, at, digest, tags, metadata = base._canonical_metadata(candidate)
        cur.execute(base.FIND, (external_id,))
        existing = cur.fetchone()
        if existing is None:
            cur.execute(base.INSERT, (external_id, ocid, rid, title, at, digest, tags, adapter(metadata)))
            if not cur.fetchone():
                raise R9WriteError('missing_inserted_id')
            counts['inserted'] += 1
        else:
            item_id, old_ocid, old_rid, old_hash = existing
            if (old_ocid, old_rid) != (ocid, rid):
                raise R9WriteError('identity_collision')
            if not isinstance(old_hash, str) or not base.HASH_RE.fullmatch(old_hash):
                raise R9WriteError('existing_hash_invalid')
            if old_hash == digest:
                cur.execute(base.SEEN, (item_id,))
                counts['repeated'] += 1
            else:
                cur.execute(base.UPDATE, (title, at, digest, tags, adapter(metadata), item_id))
                cur.execute(base.VERSION, (item_id, old_hash, digest))
                counts['updated'] += 1
    if sum(counts.values()) != batch.tender_candidates:
        raise R9WriteError('transaction_count_mismatch')
    cur.execute(base.RECEIPT, (batch.window.utc_from, batch.window.utc_to,
                               batch.observed_releases, batch.tender_candidates,
                               counts['inserted'], counts['updated'], counts['repeated']))
    row = cur.fetchone()
    if not row:
        raise R9WriteError('missing_complete_receipt')
    return {'qa': 'HOLD', 'public_release': False, 'candidate_count': batch.tender_candidates,
            'observed_releases': batch.observed_releases, 'pages': batch.pages,
            'counts': counts, 'receipt_id': str(row[0]), 'checkpoint_advanced': False}


class _DryRun(Exception):
    def __init__(self, result):
        self.result = result


def write_batch(batch: WindowBatch, *, connect=None, dry_run=True) -> dict:
    """Manual call only. A dry run executes SQL then ALWAYS rolls back."""
    import uk_staging_writer as base
    if type(dry_run) is not bool:
        raise R9WriteError('dry_run_must_be_boolean')
    if not isinstance(batch, WindowBatch) or batch.qa != 'HOLD' or batch.public_release is not False:
        raise R9WriteError('invalid_hold_batch')
    if (batch.tender_candidates > 200 or batch.source_unique_releases > 2500
            or batch.observed_releases < batch.source_unique_releases or batch.pages < 1):
        raise R9WriteError('write_cap_exceeded')
    _authorized()
    base._approved()
    dsn = base._dsn_without_secret()
    if connect is None:
        import psycopg
        from psycopg.types.json import Jsonb
        connect, adapter = psycopg.connect, Jsonb
    else:
        adapter = lambda data: data  # Injected database in OFFLINE tests only.
    with connect(dsn, sslmode='require', connect_timeout=15) as conn:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute('SELECT current_user')
                    who = cur.fetchone()
                    login = os.environ.get('BOHUN_UK_DB_WRITER_LOGIN', '')
                    if not who or who[0] != login or login in {
                            '', 'postgres', 'supabase_admin', 'service_role', 'anon', 'authenticated'}:
                        raise R9WriteError('wrong_writer_identity')
                    base._preflight(cur)
                    cur.execute("SET LOCAL statement_timeout = '90s'")
                    outcome = _apply_cursor(cur, batch, adapter=adapter, base=base)
                    if dry_run:
                        raise _DryRun(outcome)
                    return {**outcome, 'dry_run': False, 'committed': True}
        except _DryRun as dry:
            return {**dry.result, 'dry_run': True, 'committed': False}


def prepare_window(window, transport, *, max_tenders=200):
    """Network only on explicit call with provided transport."""
    from r8_source_reader import read_window
    from uk_hold_pilot import prepare_releases
    read_result = read_window(window, transport)
    return prepare_batch(read_result, prepare_releases, max_tenders=max_tenders)
