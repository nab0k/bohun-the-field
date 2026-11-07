"""Canonical digest of all publication-relevant candidate metadata.

Pure standard-library helper. No service calls and no publication permission.
"""
from __future__ import annotations

from hashlib import sha256
import json


VALID_TYPES = frozenset({"synthesis", "signal", "analysis"})


def candidate_identity_hash(material_id, material_type, version, author_model_id,
                            event_ids, event_fingerprint):
    """Return a SHA-256 pin or an empty string for malformed metadata.

    Event order is included intentionally: changing any field invalidates PASS.
    """
    if (not isinstance(material_id, str) or not material_id.strip()
            or not isinstance(material_type, str) or material_type not in VALID_TYPES
            or type(version) is not int or version < 1
            or not isinstance(author_model_id, str) or not author_model_id.strip()
            or not isinstance(event_fingerprint, str)
            or not isinstance(event_ids, (tuple, list))
            or not all(isinstance(e, str) and e.strip() for e in event_ids)
            or len(set(event_ids)) != len(event_ids)):
        return ""
    data = {"material_id": material_id, "material_type": material_type,
            "version": version, "author_model_id": author_model_id,
            "event_ids": list(event_ids), "event_fingerprint": event_fingerprint}
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()
