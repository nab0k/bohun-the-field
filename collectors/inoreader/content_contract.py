"""Fail-closed extraction of body content returned by Inoreader Stream Contents."""
import hashlib
from html.parser import HTMLParser


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def visible_text(html):
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return " ".join(parser.parts)


def extract_inoreader_content(item):
    """Preserve returned HTML without claiming it is complete or first-party evidence."""
    summary = item.get("summary") or {}
    html = summary.get("content") if isinstance(summary, dict) else None
    if not isinstance(html, str) or not html.strip():
        return {
            "content_status": "METADATA_ONLY",
            "inoreader_summary_html": None,
            "content_sha256": None,
            "visible_text": None,
            "visible_characters": 0,
            "evidence_eligible": False,
            "qa_status": "HOLD",
        }
    html = html.strip()
    text = visible_text(html)
    return {
        # Inoreader calls this the article content, but individual feeds can still
        # provide excerpts. Completeness therefore remains deliberately unclaimed.
        "content_status": "BODY_PRESENT_COMPLETENESS_UNVERIFIED",
        "inoreader_summary_html": html,
        "content_sha256": hashlib.sha256(html.encode()).hexdigest(),
        "visible_text": text,
        "visible_characters": len(text),
        "evidence_eligible": False,
        "qa_status": "HOLD",
    }
