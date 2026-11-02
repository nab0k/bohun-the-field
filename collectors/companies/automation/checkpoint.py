"""Versioned bounded wire codec for the existing core. No IO or dynamic imports."""
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from types import UnionType
from typing import get_type_hints, get_origin, get_args
import json

from . import contracts as c
from .changes import ChangeSet, Fill, HeldClaim, Issue, evidence_error
from .worker import CommitReceipt, WorkContext

VERSION = 1
MAX_BYTES = 4 * 1024 * 1024
TYPES = {t.__name__: t for t in (c.Policy, c.CompanySnapshot, c.JobSpec, c.SectionState,
         c.FieldValue, c.CompanyView, c.SourceDocument, c.Qualifiers, c.Evidence,
         c.ProposedClaim, c.ResearchReceipt, c.Lease, ChangeSet, Fill, HeldClaim, Issue,
         CommitReceipt, WorkContext)}
DATES = {("SourceDocument", "captured_at"), ("Lease", "expires_at"), ("SectionState", "next_due")}
PAIRS = {("CompanyView", "fields"), ("CompanyView", "section_states")}
CP_KEYS = {"existing_keys", "seen_triggers", "section_inputs", "profile_hashes", "section_states"}


def shape(value, keys):
    c.require(type(value) is dict and set(value) == set(keys), "WIRE_SHAPE_INVALID")


def timestamp(value):
    c.require(type(value) is str and len(value) <= 40, "UTC_TIME_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise c.ContractError("UTC_TIME_REQUIRED") from None
    c.require(parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0, "UTC_TIME_REQUIRED")
    return c.aware(parsed)


def _matches(value, hint):
    if get_origin(hint) is UnionType:
        return any(_matches(value, h) for h in get_args(hint))
    return value is None if hint is type(None) else type(value) is hint


def pack(value, depth=0):
    c.require(depth <= 24, "WIRE_DEPTH_LIMIT")
    if is_dataclass(value):
        name = type(value).__name__
        c.require(TYPES.get(name) is type(value), "WIRE_TYPE_INVALID")
        return {"_type": name, **{f.name: pack(getattr(value, f.name), depth+1) for f in fields(value)}}
    if type(value) is datetime:
        return c.aware(value).isoformat()
    if type(value) in (tuple, list):
        return [pack(x, depth+1) for x in value]
    if type(value) is dict:
        c.require(all(type(k) is str for k in value) and "_type" not in value, "WIRE_KEY_INVALID")
        return {k: pack(v, depth+1) for k, v in value.items()}
    c.require(value is None or type(value) in (str, int, bool), "WIRE_PRIMITIVE_INVALID")
    return value


def unpack(value, depth=0):
    c.require(depth <= 24, "WIRE_DEPTH_LIMIT")
    if type(value) is list:
        return [unpack(x, depth+1) for x in value]
    if type(value) is not dict:
        c.require(value is None or type(value) in (str, int, bool), "WIRE_PRIMITIVE_INVALID")
        return value
    if "_type" not in value:
        c.require(all(type(k) is str for k in value), "WIRE_KEY_INVALID")
        return {k: unpack(v, depth+1) for k, v in value.items()}
    name = value["_type"]
    c.require(type(name) is str and name in TYPES, "WIRE_TYPE_INVALID")
    cls = TYPES[name]
    shape(value, {"_type"} | {f.name for f in fields(cls)})
    hints = get_type_hints(cls)
    kwargs = {}
    for f in fields(cls):
        item = unpack(value[f.name], depth+1)
        if (name, f.name) in DATES and item is not None:
            item = timestamp(item)
        if hints[f.name] is tuple:
            c.require(type(item) is list, "WIRE_TUPLE_REQUIRED")
            if (name, f.name) in PAIRS:
                c.require(all(type(x) is list and len(x) == 2 for x in item), "WIRE_PAIR_INVALID")
                item = [tuple(x) for x in item]
            item = tuple(item)
        c.require(_matches(item, hints[f.name]), "WIRE_FIELD_TYPE_INVALID")
        kwargs[f.name] = item
    try:
        result = cls(**kwargs)
    except (TypeError, ValueError, AttributeError):
        raise c.ContractError("WIRE_VALUE_INVALID") from None
    return result


def bounded(value):
    """Copy bounded parsed JSON. The transport must reject duplicate raw JSON keys."""
    text = c.json_text(value)
    c.require(len(text.encode("utf-8")) <= MAX_BYTES, "WIRE_BYTE_LIMIT")
    return json.loads(text)


def encode(value):
    return bounded(pack(value))


def decode(value, expected=None):
    result = unpack(bounded(value))
    if expected is not None:
        c.require(type(result) is expected, "WIRE_TYPE_INVALID")
    return result


def encode_checkpoint(checkpoint):
    shape(checkpoint, CP_KEYS)
    out = {}
    for name, limit in (("existing_keys", 5000), ("seen_triggers", 500)):
        values = checkpoint[name]
        c.require(type(values) in (set, frozenset) and len(values) <= limit
                  and all(c.is_hash(x) for x in values), "CHECKPOINT_KEYS_INVALID")
        out[name] = sorted(values)
    for name in ("section_inputs", "profile_hashes", "section_states"):
        mapping = checkpoint[name]
        c.require(type(mapping) is dict and len(mapping) <= 1000, "CHECKPOINT_MAP_INVALID")
        c.require(all(type(k) is tuple and len(k)==2 for k in mapping), "CHECKPOINT_SCOPE_INVALID")
        rows = []
        for (cid, section), value in sorted(mapping.items()):
            c.identifier(cid)
            c.require(section in c.SECTIONS, "SECTION_REQUIRED")
            c.require(isinstance(value, c.SectionState) if name == "section_states" else c.is_hash(value),
                      "CHECKPOINT_VALUE_INVALID")
            rows.append({"company_id": cid, "section": section, "value": pack(value)})
        out[name] = rows
    return bounded(out)


def decode_checkpoint(value):
    shape(value, CP_KEYS)
    value = bounded(value)
    result = {}
    for name, limit in (("existing_keys", 5000), ("seen_triggers", 500)):
        rows = value[name]
        c.require(type(rows) is list and len(rows) <= limit and all(c.is_hash(x) for x in rows)
                  and len(set(rows)) == len(rows), "CHECKPOINT_KEYS_INVALID")
        result[name] = frozenset(rows)
    for name in ("section_inputs", "profile_hashes", "section_states"):
        rows = value[name]
        c.require(type(rows) is list and len(rows) <= 1000, "CHECKPOINT_MAP_INVALID")
        mapping = {}
        for row in rows:
            shape(row, {"company_id", "section", "value"})
            c.identifier(row["company_id"])
            c.require(row["section"] in c.SECTIONS, "SECTION_REQUIRED")
            key = (row["company_id"], row["section"])
            c.require(key not in mapping, "CHECKPOINT_DUPLICATE_SCOPE")
            item = decode(row["value"], c.SectionState) if name == "section_states" else row["value"]
            c.require(name == "section_states" or c.is_hash(item), "CHECKPOINT_VALUE_INVALID")
            mapping[key] = item
        result[name] = mapping
    return result


def empty_checkpoint():
    return {"existing_keys": frozenset(), "seen_triggers": frozenset(),
            "section_inputs": {}, "profile_hashes": {}, "section_states": {}}


def scope_checkpoint(checkpoint, ids):
    """Opaque historical keys may include other companies; structured rows must be scoped."""
    for name in ("section_inputs", "profile_hashes", "section_states"):
        c.require(all(cid in ids for cid, _ in checkpoint[name]), "CHECKPOINT_FOREIGN_SCOPE")


def validate_changes(value, policy, now):
    c.require(type(value) is ChangeSet and value.section in c.SECTIONS
              and value.policy_hash == policy.fingerprint and c.is_hash(value.base_fingerprint), "CHANGE_SCOPE_INVALID")
    c.identifier(value.company_id)
    c.require(value.coverage in c.COVERAGE and value.reviewed_at is not None, "COMPLETE_CHANGE_REQUIRED")
    c.require(timestamp(value.reviewed_at) <= c.aware(now), "FUTURE_REVIEW")
    c.require(len(value.claims) <= policy.max_claims and all(type(h) is HeldClaim for h in value.claims),
              "CLAIMS_INVALID")
    hashes, refs = set(), set()
    for h in value.claims:
        p = h.proposal
        c.require(type(p) is c.ProposedClaim and p.company_id == value.company_id
                  and p.section == value.section and p.field_path.split('/')[1] == value.section.lower(),
                  "CLAIM_SCOPE_INVALID")
        c.require(h.qa_status == "HOLD" and h.public_release is False, "PRIVATE_HOLD_REQUIRED")
        c.require(p.claim_ref not in refs and p.content_hash not in hashes, "CLAIM_DUPLICATE")
        c.require(evidence_error(p, c.aware(now)) is None, "EVIDENCE_INVALID")
        refs.add(p.claim_ref); hashes.add(p.content_hash)
    seen_paths = set()
    for f in value.fills:
        c.require(type(f) is Fill and f.field_path in policy.fill_paths and f.field_path not in seen_paths,
                  "FILL_INVALID")
        c.require(any(h.proposal.content_hash == f.supporting_hash and h.proposal.field_path == f.field_path
                      and h.proposal.value_json == f.value_json for h in value.claims), "FILL_UNSUPPORTED")
        seen_paths.add(f.field_path)
    c.require(all(type(i) is Issue and type(i.code) is str and type(i.claim_ref) is str for i in value.issues),
              "ISSUES_INVALID")
    return value
