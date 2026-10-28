"""Private, immutable intake contracts. Pure code: no credentials, network or DB."""
from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import hashlib
import json
import re
from uuid import UUID

from .content_contract import (MAX_CAPTURE_BYTES, MAX_HTML_BYTES, MAX_TEXT_BYTES,
                               capture_size, visible_text)

VERSION = "inoreader-artifact-v1"
SOURCE_ID = 2
MAX_ITEMS = 5
MAX_PAYLOAD_BYTES = 6 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024
STATUSES = {"METADATA_ONLY", "BODY_PRESENT_COMPLETENESS_UNVERIFIED", "EMPTY_TEXT",
            "UNSUPPORTED_SHAPE", "TOO_LARGE", "CAPTURE_BUDGET_EXHAUSTED"}
ISSUES = {"METADATA_ONLY": {"NO_BODY"},
          "UNSUPPORTED_SHAPE": {"ITEM_NOT_OBJECT", "SUMMARY_NOT_OBJECT", "BODY_NOT_STRING", "INVALID_UTF8"},
          "TOO_LARGE": {"HTML_LIMIT_EXCEEDED", "TEXT_LIMIT_EXCEEDED"},
          "CAPTURE_BUDGET_EXHAUSTED": {"CAPTURE_BUDGET_EXHAUSTED"}}
ITEM_KEYS = {"external_id", "title", "url", "published_at", "raw_data"}
RAW_KEYS = {"inoreader_id", "folder", "origin_title", "origin_stream_id", "published",
            "url", "title", "content_capture"}
CAPTURE_KEYS = {"version", "status", "provider_field", "text_extraction_version",
                "raw_html", "raw_html_sha256", "raw_html_bytes", "text", "text_sha256",
                "text_bytes", "text_characters", "completeness", "issues"}
MANIFEST_KEYS = {"batch_id", "contract_version", "pipeline_version", "producer_sha",
                 "source_id", "collected_at", "outcome", "item_count", "payload_bytes",
                 "capture_bytes", "items"}
MEMBER_KEYS = {"ordinal", "external_id", "payload_sha256", "received_at", "existing_item_id"}


class IntakeError(ValueError):
    """Only fixed codes originating here may be printed by the CLI."""


def require(condition, code="INTAKE_CONTRACT_INVALID"):
    if not condition:
        raise IntakeError(code)


def utf8(value):
    require(isinstance(value, str) and "\x00" not in value)
    try:
        return value.encode("utf-8")
    except UnicodeError:
        raise IntakeError("INTAKE_UTF8_INVALID") from None


def sha256(value):
    return hashlib.sha256(utf8(value)).hexdigest()


def canonical(value):
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False)
        # PostgreSQL text/jsonb cannot retain NUL, including a JSON-escaped NUL.
        def check(node):
            if isinstance(node, str):
                utf8(node)
            elif isinstance(node, dict):
                for key, val in node.items():
                    utf8(key)
                    check(val)
            elif isinstance(node, list):
                for val in node:
                    check(val)
        check(value)
        utf8(result)
        return result
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise IntakeError("INTAKE_JSON_INVALID") from None


def parse(value, maximum=MAX_PAYLOAD_BYTES):
    require(len(utf8(value)) <= maximum, "INTAKE_SIZE_LIMIT")
    try:
        decoded = json.loads(value)
    except (ValueError, RecursionError):
        raise IntakeError("INTAKE_JSON_INVALID") from None
    # Reject duplicate keys, noncanonical types/whitespace and nonfinite numbers.
    require(canonical(decoded) == value, "INTAKE_JSON_NOT_CANONICAL")
    return decoded


def timestamp(value):
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else value
        require(isinstance(moment, datetime) and moment.utcoffset() is not None)
        return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError):
        raise IntakeError("INTAKE_TIMESTAMP_INVALID") from None


def uuid_string(value):
    try:
        require(isinstance(value, str) and str(UUID(value)) == value)
        return value
    except (ValueError, AttributeError):
        raise IntakeError("INTAKE_ID_INVALID") from None


def digest_string(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def keys(value, expected):
    require(isinstance(value, dict) and set(value) == expected)


def integer(value, minimum=0, maximum=2**63 - 1):
    return type(value) is int and minimum <= value <= maximum


def validate_payload(value):
    keys(value, {"contract_version", "source_id", "item"})
    require(value["contract_version"] == VERSION and type(value["source_id"]) is int
            and value["source_id"] == SOURCE_ID)
    item = value["item"]
    keys(item, ITEM_KEYS)
    require(isinstance(item["external_id"], str) and bool(item["external_id"].strip())
            and len(utf8(item["external_id"])) <= 2048)
    require(isinstance(item["title"], str) and len(item["title"]) <= 2000)
    require(item["url"] is None or isinstance(item["url"], str))
    require(item["published_at"] is None or timestamp(item["published_at"]) == item["published_at"])
    raw = item["raw_data"]
    keys(raw, RAW_KEYS)
    require(raw["inoreader_id"] == item["external_id"] and isinstance(raw["folder"], str)
            and raw["folder"].startswith("BOHUN_"))
    require(isinstance(raw["title"], str) and item["title"] == raw["title"][:2000]
            and item["url"] == raw["url"])
    for name in ("origin_title", "origin_stream_id"):
        require(raw[name] is None or isinstance(raw[name], str))
    require(raw["published"] is None or type(raw["published"]) in (int, float))
    capture = raw["content_capture"]
    keys(capture, CAPTURE_KEYS)
    require(type(capture["version"]) is int and capture["version"] == 1
            and capture["provider_field"] == "summary.content"
            and capture["text_extraction_version"] == "htmlparser-text-v1"
            and capture["completeness"] == "UNKNOWN" and isinstance(capture["status"], str)
            and capture["status"] in STATUSES)
    require(isinstance(capture["issues"], list)
            and all(isinstance(x, str) for x in capture["issues"]))
    for name in ("raw_html_bytes", "text_bytes", "text_characters"):
        require(integer(capture[name]))
    retained = capture["status"] in {"BODY_PRESENT_COMPLETENESS_UNVERIFIED", "EMPTY_TEXT"}
    if retained:
        html, text = capture["raw_html"], capture["text"]
        require(isinstance(html, str) and bool(html.strip()) and isinstance(text, str))
        require(len(utf8(html)) == capture["raw_html_bytes"] <= MAX_HTML_BYTES
                and len(utf8(text)) == capture["text_bytes"] <= MAX_TEXT_BYTES
                and len(text) == capture["text_characters"])
        require(sha256(html) == capture["raw_html_sha256"]
                and sha256(text) == capture["text_sha256"] and visible_text(html) == text)
        require((bool(text) and capture["status"] == "BODY_PRESENT_COMPLETENESS_UNVERIFIED"
                 and capture["issues"] == []) or
                (not text and capture["status"] == "EMPTY_TEXT"
                 and capture["issues"] == ["NO_EXTRACTED_TEXT"]))
    else:
        require(all(capture[name] is None for name in
                    ("raw_html", "text", "raw_html_sha256", "text_sha256"))
                and capture["text_characters"] == 0 and len(capture["issues"]) == 1
                and capture["issues"][0] in ISSUES[capture["status"]])
    # Ensure all nested values can be represented without silent coercion.
    canonical(value)
    return value


@dataclass(frozen=True)
class PreparedBatch:
    manifest_json: str
    manifest_sha256: str
    payloads: tuple[str, ...]

    @property
    def batch_id(self):
        return json.loads(self.manifest_json)["batch_id"]


def validate_manifest(text, expected_hash):
    require(digest_string(expected_hash) and sha256(text) == expected_hash,
            "INTAKE_MANIFEST_HASH_MISMATCH")
    value = parse(text, MAX_MANIFEST_BYTES)
    keys(value, MANIFEST_KEYS)
    uuid_string(value["batch_id"])
    require(value["contract_version"] == VERSION and type(value["source_id"]) is int
            and value["source_id"] == SOURCE_ID)
    require(isinstance(value["pipeline_version"], str) and
            re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", value["pipeline_version"]) is not None)
    require(isinstance(value["producer_sha"], str) and
            re.fullmatch(r"[0-9a-f]{40}", value["producer_sha"]) is not None)
    require(timestamp(value["collected_at"]) == value["collected_at"])
    require(isinstance(value["items"], list) and integer(value["item_count"], maximum=MAX_ITEMS)
            and len(value["items"]) == value["item_count"])
    require(value["outcome"] == ("STORED" if value["items"] else "EMPTY"))
    require(integer(value["payload_bytes"], maximum=MAX_PAYLOAD_BYTES)
            and integer(value["capture_bytes"], maximum=MAX_CAPTURE_BYTES))
    seen = set()
    for i, member in enumerate(value["items"]):
        keys(member, MEMBER_KEYS)
        require(type(member["ordinal"]) is int and member["ordinal"] == i)
        require(isinstance(member["external_id"], str) and member["external_id"].strip()
                and len(utf8(member["external_id"])) <= 2048 and member["external_id"] not in seen)
        seen.add(member["external_id"])
        require(digest_string(member["payload_sha256"]))
        require(timestamp(member["received_at"]) == member["received_at"])
        require(member["existing_item_id"] is None or integer(member["existing_item_id"], 1))
    return value


def validate_batch(batch):
    require(isinstance(batch, PreparedBatch) and isinstance(batch.payloads, tuple))
    manifest = validate_manifest(batch.manifest_json, batch.manifest_sha256)
    require(len(batch.payloads) == manifest["item_count"])
    size = total_capture = 0
    for member, text in zip(manifest["items"], batch.payloads):
        size += len(utf8(text))
        require(size <= MAX_PAYLOAD_BYTES, "INTAKE_SIZE_LIMIT")
        require(sha256(text) == member["payload_sha256"], "INTAKE_PAYLOAD_HASH_MISMATCH")
        value = validate_payload(parse(text))
        require(value["item"]["external_id"] == member["external_id"])
        capture = value["item"]["raw_data"]["content_capture"]
        total_capture += capture_size({**capture, "captured_at": member["received_at"]})
    require(size == manifest["payload_bytes"] and total_capture == manifest["capture_bytes"])
    require(total_capture <= MAX_CAPTURE_BYTES, "INTAKE_CAPTURE_LIMIT")
    return manifest


def prepare_batch(items, *, batch_id, producer_sha, pipeline_version, collected_at,
                  existing_item_ids=None):
    require(isinstance(items, list) and len(items) <= MAX_ITEMS)
    payloads, members = [], []
    for i, original in enumerate(items):
        item = copy.deepcopy(original)
        keys(item, ITEM_KEYS)
        keys(item["raw_data"], RAW_KEYS)
        capture = item["raw_data"]["content_capture"]
        keys(capture, CAPTURE_KEYS | {"captured_at"})
        received_at = timestamp(capture.pop("captured_at"))
        item["published_at"] = (timestamp(item["published_at"])
                                if item["published_at"] is not None else None)
        value = validate_payload({"contract_version": VERSION, "source_id": SOURCE_ID, "item": item})
        text = canonical(value)
        payloads.append(text)
        members.append(dict(ordinal=i, external_id=item["external_id"], payload_sha256=sha256(text),
                            received_at=received_at,
                            existing_item_id=(existing_item_ids or {}).get(item["external_id"])))
    manifest = dict(batch_id=uuid_string(batch_id), contract_version=VERSION,
                    pipeline_version=pipeline_version, producer_sha=producer_sha,
                    source_id=SOURCE_ID, collected_at=timestamp(collected_at),
                    outcome="STORED" if items else "EMPTY", item_count=len(items),
                    payload_bytes=sum(len(utf8(p)) for p in payloads),
                    capture_bytes=sum(capture_size({**json.loads(p)["item"]["raw_data"]["content_capture"],
                                                   "captured_at": m["received_at"]})
                                      for p, m in zip(payloads, members)), items=members)
    text = canonical(manifest)
    batch = PreparedBatch(text, sha256(text), tuple(payloads))
    validate_batch(batch)
    return batch
