"""Typed source boundaries. A transport/body hash is never a rights or truth grant."""
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json

from .contracts import Document, aware, canonical_url, digest, exact_fields, json_text, nonempty
from .research_packet import SourceRecord

POLICY_VERSION = "exp009-source-profile-v1"
VERIFIER_VERSION = "bounded-profile-checks-v1"


def source_profile(value):
    """An administrator-reviewed rule set, never supplied by model/source text."""
    exact_fields(value, {"version", "reference", "source_id", "source_db_id", "kind", "origin",
                         "path_prefix", "extractor", "start_marker", "end_marker", "blocked_markers",
                         "min_text_bytes", "max_text_bytes", "rights_reference", "safety_reference",
                         "source_stage", "enabled"})
    for key in ("reference", "source_id", "extractor", "start_marker", "end_marker",
                "rights_reference", "safety_reference"):
        nonempty(value[key])
        if len(value[key]) > 1024:
            raise ValueError("PROFILE_FIELD_BOUND")
    from urllib.parse import urlsplit
    origin = urlsplit(value["origin"])
    if (value["version"] != POLICY_VERSION or value["kind"] not in {"web", "inoreader"}
            or type(value["enabled"]) is not bool or type(value["source_db_id"]) is not int
            or value["source_db_id"] < 1 or origin.scheme != "https" or not origin.hostname
            or origin.username or origin.password or origin.port not in (None, 443)
            or origin.path or origin.query or origin.fragment
            or not isinstance(value["path_prefix"], str) or not value["path_prefix"].startswith("/")
            or "?" in value["path_prefix"] or "#" in value["path_prefix"]):
        raise ValueError("PROFILE_BOUNDARY_INVALID")
    if (type(value["min_text_bytes"]) is not int or type(value["max_text_bytes"]) is not int
            or not 1 <= value["min_text_bytes"] <= value["max_text_bytes"] <= 131072
            or not isinstance(value["blocked_markers"], list) or not 1 <= len(value["blocked_markers"]) <= 10
            or any(not isinstance(s, str) or not s or len(s) > 256 for s in value["blocked_markers"])
            or value["source_stage"] not in {"announced", "planned", "signed", "delivered", "unknown"}):
        raise ValueError("PROFILE_CHECKS_INVALID")
    expected = "trafilatura-2.3.1" if value["kind"] == "web" else "htmlparser-text-v1"
    if value["extractor"] != expected:
        raise ValueError("PROFILE_EXTRACTOR_UNREVIEWED")
    return dict(value)


def admit_source(source, profile, raw_html, *, kind, provenance):
    """Check a whole version against an approved profile, not an arbitrary true flag.

    Completeness means only the documented profile checks, never market truth.
    Rejections retain UNKNOWN flags and a machine-readable reason. No IO here.
    """
    from dataclasses import replace
    from urllib.parse import urlsplit
    p = source_profile(profile)
    reason = None
    url = urlsplit(source.url)
    origin = f"{url.scheme}://{url.netloc}"
    raw_ok = isinstance(raw_html, str) and 0 < len(raw_html.encode()) <= 262144
    if not p["enabled"]:
        reason = "PROFILE_DISABLED"
    elif source.source_id != p["source_id"] or kind != p["kind"] or origin != p["origin"] or not url.path.startswith(p["path_prefix"]):
        reason = "PROFILE_SOURCE_BOUNDARY"
    elif not raw_ok or not source.text or not p["min_text_bytes"] <= len(source.text.encode()) <= p["max_text_bytes"]:
        reason = "PROFILE_SIZE_OR_BODY"
    elif p["start_marker"] not in raw_html or p["end_marker"] not in raw_html or raw_html.index(p["start_marker"]) >= raw_html.rindex(p["end_marker"]):
        reason = "PROFILE_CONTAINER_INCOMPLETE"
    elif any(marker.casefold() in (raw_html + source.text).casefold() for marker in p["blocked_markers"]):
        reason = "PROFILE_EXCLUDED_CONTENT"
    elif datetime.fromisoformat(source.published_at.replace("Z", "+00:00")) > datetime.fromisoformat(source.received_at.replace("Z", "+00:00")):
        reason = "PROFILE_FUTURE_PUBLICATION"
    accepted = reason is None
    result = replace(source, source_stage=p["source_stage"],
                     completeness="COMPLETE" if accepted else "UNKNOWN",
                     rights="PERMITTED" if accepted else "UNKNOWN", sensitive=not accepted)
    machine = {"version": VERIFIER_VERSION, "policy_ref": p["reference"], "policy_hash": digest(p),
               "source_key": result.identity, "text_hash": source.text_hash,
               "html_hash": hashlib.sha256(raw_html.encode()).hexdigest() if raw_ok else None,
               "extractor": p["extractor"], "checked_at": source.receipt()["received_at"],
               "accepted": accepted, "reason": reason or "PROFILE_CHECKS_PASSED",
               "provenance": provenance}
    return result, machine


@dataclass(frozen=True)
class Assessment:
    """Trusted, version-bound assessment, supplied by a reviewed policy/assessor."""
    text_hash: str
    completeness: str = "UNKNOWN"
    rights: str = "UNKNOWN"
    sensitive: bool = True
    reference: str = ""

    def apply(self, source):
        if self.text_hash != source.text_hash or not self.reference:
            raise ValueError("ASSESSMENT_NOT_BOUND_TO_SOURCE")
        from dataclasses import replace
        return replace(source, completeness=self.completeness, rights=self.rights, sensitive=self.sensitive)


def inoreader_source(payload, *, received_at, assessment=None):
    from collectors.inoreader.intake_contract import parse, validate_payload
    value = validate_payload(parse(payload))
    item = value["item"]; capture = item["raw_data"]["content_capture"]
    if item["published_at"] is None or not item["url"]:
        raise ValueError("SOURCE_DATE_OR_URL_UNKNOWN")
    retained = capture["status"] == "BODY_PRESENT_COMPLETENESS_UNVERIFIED"
    source = SourceRecord("inoreader:2", hashlib.sha256(payload.encode()).hexdigest(),
                          item["external_id"], item["url"], item["title"], item["published_at"],
                          received_at, capture["text"] if retained else None,
                          capture["text_sha256"] if retained else None)
    return assessment.apply(source) if assessment is not None else source


def web_source(capture, *, source_id, title, published_at, assessment=None):
    exact_fields(capture, {"url", "received_at", "html_sha256", "text", "text_sha256", "extractor"})
    if hashlib.sha256(capture["text"].encode()).hexdigest() != capture["text_sha256"]:
        raise ValueError("WEB_TEXT_HASH_MISMATCH")
    source = SourceRecord(source_id, capture["html_sha256"], capture["url"], capture["url"],
                          title, published_at, capture["received_at"], capture["text"],
                          capture["text_sha256"])
    return assessment.apply(source) if assessment is not None else source


def envelope(source, *, kind, source_db_id, assessment_ref):
    if kind not in {"inoreader", "web"} or type(source_db_id) is not int or source_db_id < 1:
        raise ValueError("SOURCE_KIND_OR_ID_INVALID")
    nonempty(assessment_ref)
    return {"version": "runtime-source-v1", "kind": kind, "source_db_id": source_db_id,
            "assessment_ref": assessment_ref, "source": source.core(), "receipt": source.receipt()}


def restore_source(value):
    """Strict importer; never accept a caller-supplied QA flag or verified entity ID."""
    exact_fields(value, {"version", "kind", "source_db_id", "assessment_ref", "source", "receipt"})
    if value["version"] != "runtime-source-v1" or value["kind"] not in {"inoreader", "web"}:
        raise ValueError("SOURCE_VERSION_INVALID")
    if type(value["source_db_id"]) is not int or value["source_db_id"] < 1:
        raise ValueError("SOURCE_ID_INVALID")
    nonempty(value["assessment_ref"])
    core = dict(value["source"]); receipt = value["receipt"]
    exact_fields(receipt, {"source_key", "item_id", "url", "received_at"})
    # Candidate entities require explicit resolution outside this boundary.
    if core.pop("entities", None) != []:
        raise ValueError("SOURCE_ENTITIES_REQUIRE_RESOLUTION")
    source = SourceRecord(**core, item_id=receipt["item_id"], received_at=receipt["received_at"])
    if source.identity != receipt["source_key"] or canonical_url(source.url) != receipt["url"]:
        raise ValueError("SOURCE_IDENTITY_MISMATCH")
    return source
