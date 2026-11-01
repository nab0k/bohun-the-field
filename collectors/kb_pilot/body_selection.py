"""Pure body selection: provenance is explicit and presence never establishes QA."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re

MIN_CHARS = 300
MAX_TEXT_BYTES = 128 * 1024
CAPTURE = "kb-fulltext-v2"


@dataclass(frozen=True)
class Body:
    text: str | None = None
    source_kind: str | None = None
    text_sha256: str | None = None
    reason: str = "NO_VALID_BODY"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_text(title, body):
    # Exact input convention of embed.item_text; not a full-body length proof.
    return ((title or "").strip() + "\n" + (body or "").strip())[:1800].strip()


def valid_text(capture, min_chars=MIN_CHARS):
    if not isinstance(capture, dict):
        return False
    text = capture.get("text")
    if not isinstance(text, str) or len(text) < min_chars:
        return False
    try:
        return len(text.encode("utf-8")) <= MAX_TEXT_BYTES and sha(text) == capture.get("text_sha256")
    except UnicodeError:
        return False


def select_body(raw, url, min_chars=MIN_CHARS):
    if not isinstance(raw, dict):
        return Body(reason="RAW_DATA_INVALID")
    provider = raw.get("content_capture")
    if (isinstance(provider, dict) and type(provider.get("version")) is int and provider.get("version") == 1
            and provider.get("provider_field") == "summary.content"
            and provider.get("status") == "BODY_PRESENT_COMPLETENESS_UNVERIFIED"
            and valid_text(provider, min_chars)):
        return Body(provider["text"], "inoreader_provider", provider["text_sha256"], "OK")
    capture = raw.get("publisher_capture")
    if capture is None:
        return Body(reason="NO_VALID_PROVIDER_OR_PUBLISHER")
    if not isinstance(capture, dict) or not valid_text(capture, min_chars):
        return Body(reason="PUBLISHER_TEXT_INVALID")
    text = capture["text"]
    try:
        timestamp = datetime.fromisoformat(capture.get('received_at', '').replace('Z', '+00:00'))
        if timestamp.utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return Body(reason='PUBLISHER_TIMESTAMP_INVALID')
    if (capture.get("capture") != CAPTURE or capture.get("source_kind") != "publisher_article"
            or not isinstance(url, str) or not url.startswith("https://")
            or any(capture.get(k) != url for k in ("url", "requested_url", "final_url"))
            or type(capture.get("http_status")) is not int or capture["http_status"] != 200
            or capture.get("extractor") != "trafilatura" or capture.get("extractor_version") != "2.3.1"
            or type(capture.get("text_bytes")) is not int or capture["text_bytes"] != len(text.encode())
            or type(capture.get("characters")) is not int or capture["characters"] != len(text)
            or not re.fullmatch(r"[0-9a-f]{64}", str(capture.get("html_sha256", "")))
            or capture.get("completeness") != "UNKNOWN"):
        return Body(reason="PUBLISHER_PROVENANCE_INVALID")
    return Body(text, "publisher_article", capture["text_sha256"], "OK")


def ready_body(raw, url, title, embedding_hash, origin_method, min_chars=MIN_CHARS):
    body = select_body(raw, url, min_chars)
    if body.source_kind == "publisher_article":
        if embedding_hash != sha(embedding_text(title, body.text)):
            return Body(reason="PUBLISHER_EMBEDDING_STALE_OR_MISSING")
        if origin_method not in ("self", "same_text", "near_duplicate"):
            return Body(reason="PUBLISHER_ORIGIN_UNCHECKED")
    return body
