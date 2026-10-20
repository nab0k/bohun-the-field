"""BOHUN UK/Canada/EU R4: longitudinal shadow importer, no persistence.

Consumes the *merged PR #73* normalize.prepare() candidate manifests and
validates them with the R3 review_gate. Models new identities, repeated sightings,
and observed hash changes across sampling windows without database/network I/O.

Review-only: not a source collector, database writer, or publication pipeline.
Never infer cancellation, contract signature, payment or granted funding.
"""
from collections import defaultdict
from datetime import datetime, timezone

from review_gate import validate_manifest, ReviewGateStop

MAX_SNAPSHOTS = 24
MAX_TOTAL_SIGHTINGS = 50_000
MAX_TIME_CHARS = 40

class ShadowImportStop(ValueError):
    """Fail closed when evidence, chronology or provenance are ambiguous."""



def _instant(timestamp):
    if not isinstance(timestamp, str) or not 15 <= len(timestamp) <= MAX_TIME_CHARS:
        raise ShadowImportStop('observed_at_invalid')
    try:
        dt = datetime.fromisoformat(timestamp.replace('Z','+00:00'))
    except ValueError:
        raise ShadowImportStop('observed_at_invalid') from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ShadowImportStop('observed_at_timezone_required')
    return dt.astimezone(timezone.utc)


def _single(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) - {'observed_at', 'manifest', 'receipt'}:
        raise ShadowImportStop('snapshot_fields_invalid')
    stamp = _instant(snapshot.get('observed_at'))
    try:
        source, scope, rows = validate_manifest(snapshot.get('manifest'))
    except ReviewGateStop as exc:
        raise ShadowImportStop('review_gate_' + str(exc)) from None
    receipt = snapshot.get('receipt')
    if receipt is not None:
        if (not isinstance(receipt, dict) or set(receipt) != {'source_system','collection_scope','complete'} or
                receipt.get('source_system') != source or receipt.get('collection_scope') != scope or
                type(receipt.get('complete')) is not bool):
            raise ShadowImportStop('receipt_not_matching')
    return stamp, source, scope, rows, receipt


def build_shadow(snapshots):
    """Plan a shadow ledger from ordered in-memory snapshots.

    Exactly one source/scope observation is allowed per UTC timestamp. Different
    hashes for the same source/external ID *at one timestamp* are held as
    conflicting evidence, not treated as competing authoritative versions.

    Inputs include raw titles/IDs inside the normalize manifests; none appear
    in the returned report. No filesystem, DB or HTTP clients are imported.
    """
    if (not isinstance(snapshots, list) or not snapshots or
            len(snapshots) > MAX_SNAPSHOTS):
        raise ShadowImportStop('snapshot_count_invalid')
    events = [_single(s) for s in snapshots]
    if sum(len(rows) for _,_,_,rows,_ in events) > MAX_TOTAL_SIGHTINGS:
        raise ShadowImportStop('sighting_count_limit')
    if [e[0] for e in events] != sorted(e[0] for e in events):
        raise ShadowImportStop('events_not_chronological')

    observed_scopes = set()
    receipt_counts = {'claimed_complete':0, 'claimed_incomplete':0, 'not_supplied':0}
    timeline = defaultdict(lambda: defaultdict(list))
    scope_sightings = defaultdict(set)
    for stamp,source,scope,rows,receipt in events:
        key=(stamp,source,scope)
        if key in observed_scopes:
            raise ShadowImportStop('duplicate_snapshot_run')
        observed_scopes.add(key)
        category = ('not_supplied' if receipt is None else
                    'claimed_complete' if receipt['complete'] else 'claimed_incomplete')
        receipt_counts[category] += 1
        for row in rows:
            identity=(source, row['external_id'])
            timeline[identity][stamp].append((scope, row['source_record_sha256']))
            scope_sightings[identity].add(scope)

    identities = 0
    observed_version_count = 0
    repeated_sightings = 0
    observed_changes = 0
    concurrent_conflicts = 0
    candidate_cross_scope = 0
    uk_related=defaultdict(set)
    for (source, external_id), instants in sorted(timeline.items()):
        identities += 1
        current_hashes=set()
        ever_hashes=set()
        for stamp, observations in sorted(instants.items()):
            this_hashes = {digest for _, digest in observations}
            if len(this_hashes) > 1:
                concurrent_conflicts += 1
                # No authoritative version chosen at ambiguous timestamp.
                # Hashes remain evidence only and do not update comparison state.
                continue
            current = next(iter(this_hashes))
            repeated_sightings += len(observations)-1
            if current in ever_hashes:
                repeated_sightings += 1 if ever_hashes else 0
                # A return to an older observed hash is a new change *event*;
                # do not treat it as a novel content hash/version.
                if current_hashes and current not in current_hashes:
                    observed_changes += 1
            else:
                observed_version_count += 1
                ever_hashes.add(current)
                if current_hashes and current not in current_hashes:
                    observed_changes += 1
            current_hashes={current}
        if len(scope_sightings[(source, external_id)])>1:
            candidate_cross_scope += 1
        if source == 'uk_fts':
            ocid, release_id = external_id.split('::',1)
            uk_related[ocid].add(release_id)

    return {
        'contract_version':'bohun-shadow-r4-v1',
        'mode':'OFFLINE_SIMULATION_ONLY',
        'qa':'HOLD',
        'public_release':False,
        'db_write_allowed':False,
        'publication_allowed':False,
        'independently_verified_source_completeness':False,
        'observed_at_supplied_by_caller_not_verified':True,
        'snapshot_count':len(events),
        'source_systems':sorted({e[1] for e in events}),
        'total_sightings':sum(len(e[3]) for e in events),
        'distinct_source_external_ids':identities,
        'distinct_observed_payload_hashes':observed_version_count,
        'repeated_sightings_estimate':repeated_sightings,
        'observed_content_change_events':observed_changes,
        'conflicting_same_instant_identities':concurrent_conflicts,
        'cross_scope_identity_candidates':candidate_cross_scope,
        'uk_multi_release_ocid_candidates':sum(len(ids)>1 for ids in uk_related.values()),
        'receipt_claims':receipt_counts,
        'warnings':[
            'Source wire format, rights and full capture are not verified',
            'Only content hash observations: not a verified state transition',
            'Missing record from a snapshot never proves cancellation or closure',
            'Same-instant conflicting hashes quarantined; no preferred value chosen',
            'Matching UK OCID links releases only; not evidence of awarded contract',
            'Canadian awards not yet included in current main-branch contract',
            'EXP-009 intake_batches still Inoreader-only; no database integration',
            'No automatic publication or production writes',
        ],
    }
