"""Bounded capture of provider HTML; presence never establishes completeness or QA."""

import datetime as dt
import hashlib
from html.parser import HTMLParser
import json


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 128 * 1024
MAX_CAPTURE_BYTES = 5 * 1024 * 1024
_IGNORED_TAGS = {"script", "style", "noscript", "svg"}


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored_tags = []

    def handle_starttag(self, tag, attrs):
        if tag in _IGNORED_TAGS:
            self.ignored_tags.append(tag)

    def handle_endtag(self, tag):
        if tag in self.ignored_tags:
            index = len(self.ignored_tags) - 1 - self.ignored_tags[::-1].index(tag)
            del self.ignored_tags[index:]

    def handle_data(self, data):
        if not self.ignored_tags:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)


def visible_text(html):
    """Extract HTML text without executing it; CSS visibility is not evaluated."""
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return " ".join(parser.parts)


def capture_size(capture):
    return len(json.dumps(capture, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _positive_limit(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Capture limits must be positive integers")
    return value


class CaptureBudget:
    """Account for all capture JSON, including bodyless status records.

    Captures are owned mutable dictionaries until collection finishes. If later
    status records need space, remove the latest retained body first. Metadata
    and explicit reasons survive; no source item is modified. An impossible
    status-only batch fails before the caller can write it to the database.
    """

    def __init__(self, limit=MAX_CAPTURE_BYTES):
        self.limit = _positive_limit(limit)
        self.bytes_used = 0
        self._captures = []

    def add(self, capture):
        size = capture_size(capture)
        self._captures.append(capture)
        self.bytes_used += size
        for retained in reversed(self._captures):
            if self.bytes_used <= self.limit:
                break
            if retained["raw_html"] is None:
                continue
            previous_size = capture_size(retained)
            compact = {**retained, "status": "CAPTURE_BUDGET_EXHAUSTED", "raw_html": None,
                       "raw_html_sha256": None, "text": None, "text_sha256": None,
                       "text_characters": 0, "issues": ["CAPTURE_BUDGET_EXHAUSTED"]}
            compact_size = capture_size(compact)
            if compact_size < previous_size:
                retained.update(compact)
                self.bytes_used += compact_size - previous_size
        if self.bytes_used > self.limit:
            self._captures.pop()
            self.bytes_used -= capture_size(capture)
            raise RuntimeError("CAPTURE_METADATA_BUDGET_EXHAUSTED")
        return capture


def extract_inoreader_content(item, *, captured_at=None, budget=None,
                              max_html_bytes=MAX_HTML_BYTES, max_text_bytes=MAX_TEXT_BYTES):
    """Capture only summary.content; a missing body is never inferred from a title."""
    _positive_limit(max_html_bytes)
    _positive_limit(max_text_bytes)
    captured_at = captured_at if captured_at is not None else dt.datetime.now(dt.timezone.utc)
    if not isinstance(captured_at, dt.datetime) or captured_at.utcoffset() is None:
        raise ValueError("An aware capture timestamp is required")
    result = {
        "version": 1,
        "status": "METADATA_ONLY",
        "provider_field": "summary.content",
        "captured_at": captured_at.astimezone(dt.timezone.utc).isoformat(),
        "text_extraction_version": "htmlparser-text-v1",
        "raw_html": None,
        "raw_html_sha256": None,
        "raw_html_bytes": 0,
        "text": None,
        "text_sha256": None,
        "text_bytes": 0,
        "text_characters": 0,
        "completeness": "UNKNOWN",
        "issues": [],
    }

    def finish(status, issue=None):
        result["status"] = status
        result["issues"] = [issue] if issue else []
        return budget.add(result) if budget is not None else result

    if not isinstance(item, dict):
        return finish("UNSUPPORTED_SHAPE", "ITEM_NOT_OBJECT")
    summary = item.get("summary")
    if summary is None:
        return finish("METADATA_ONLY", "NO_BODY")
    if not isinstance(summary, dict):
        return finish("UNSUPPORTED_SHAPE", "SUMMARY_NOT_OBJECT")
    html = summary.get("content")
    if html is None:
        return finish("METADATA_ONLY", "NO_BODY")
    if not isinstance(html, str):
        return finish("UNSUPPORTED_SHAPE", "BODY_NOT_STRING")
    try:
        raw_bytes = html.encode("utf-8")
    except UnicodeError:
        return finish("UNSUPPORTED_SHAPE", "INVALID_UTF8")
    result["raw_html_bytes"] = len(raw_bytes)
    if len(raw_bytes) > max_html_bytes:
        return finish("TOO_LARGE", "HTML_LIMIT_EXCEEDED")
    if not html.strip():
        return finish("METADATA_ONLY", "NO_BODY")
    text = visible_text(html)
    text_bytes = text.encode("utf-8")
    result["text_bytes"] = len(text_bytes)
    if len(text_bytes) > max_text_bytes:
        return finish("TOO_LARGE", "TEXT_LIMIT_EXCEEDED")
    result.update(raw_html=html, raw_html_sha256=hashlib.sha256(raw_bytes).hexdigest(),
                  text=text, text_sha256=hashlib.sha256(text_bytes).hexdigest(),
                  text_characters=len(text))
    return finish("BODY_PRESENT_COMPLETENESS_UNVERIFIED" if text else "EMPTY_TEXT",
                  None if text else "NO_EXTRACTED_TEXT")
