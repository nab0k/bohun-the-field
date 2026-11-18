"""A5TC-02 (EXP-034, D-20261118-A5TC-02-V2-TRANSFER): completeness of an article text — full / partial / mismatch / none, with reasons.

Length alone never gives "full". A text is "full" only if it belongs to the headline, has article structure,
ends like a finished text, shows no paywall/"read more" marker, and — where the page declares its own
article body (JSON-LD articleBody) — covers at least 80% of it. Standard library only.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata

MIN_FULL_CHARS = 600          # design A: proposed boundary, not a proof of completeness on its own
MIN_PARAGRAPHS = 3
PARAGRAPH_MIN_CHARS = 40
ARTICLEBODY_COVERAGE = 0.8
TITLE_TOKEN_SHARE = 0.3

TERMINAL = tuple('.!?"»”’)…') + ("。",)
TRUNCATION_ENDINGS = ("…", "...", "[…]", "[...]", "(…)")
MARKERS = re.compile(
    r"(subscribe (now )?to (read|continue)|to continue reading|for (paid )?subscribers|subscriber[- ]only|"
    r"already a subscriber|sign in to (read|continue)|log in to (read|continue)|premium (content|article)|"
    r"read more\b|continue reading|jetzt abonnieren|weiterlesen|abo(nnement)? erforderlich|"
    r"читати далі|читать далее|продовження|lire la suite|réservé aux abonnés|czytaj (dalej|więcej)|"
    r"mitgliedschaft erforderlich|nur (für )?[\w-]*abonnenten|jetzt [\w-]*mitglied werden)",
    re.IGNORECASE)
# Trailing lines that are not article text: photo credits, photo-sale notices, embedded social posts.
# Fix 18.11 (live day 16.11): "Illustrative photo: X", "Archive photo: X" and tweet embeds were read as an unfinished end.
CREDIT_LINE = re.compile(
    r"^(((illustrative|archive|file|stock|cover|main|top) )?photos?\b.*|.*\bphotos? can be purchased\b.*|"
    r"фото\b.*|foto\b.*|source:.*|джерело:.*)$", re.IGNORECASE)
EMBED_LINE = re.compile(r"(https?://t\.co/|pic\.twitter\.com/|^—\s.*\(@\w+\)\s.*\d{4}$)", re.IGNORECASE)
# A complete short news item: below MIN_FULL_CHARS but with the provider lead inside, article structure and a clean end.
SHORT_NEWS_MIN_CHARS = 400


def strip_credits(text: str) -> str:
    lines = [l for l in (text or "").strip().split("\n")]
    while lines and (not lines[-1].strip() or CREDIT_LINE.match(lines[-1].strip()) or EMBED_LINE.search(lines[-1].strip())):
        lines.pop()
    return "\n".join(lines).strip()
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "from", "as", "is", "are",
        "was", "were", "be", "its", "it", "this", "that", "after", "over", "into", "says", "said", "new", "der", "die",
        "das", "und", "von", "zur", "zum", "mit", "für", "den", "dem", "des", "ein", "eine", "jetzt"}


def tokens(value: str) -> list[str]:
    value = unicodedata.normalize("NFKC", html.unescape(value or "")).casefold()
    return re.findall(r"[^\W_]+", value, re.UNICODE)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n+", text or "") if len(p.strip()) >= PARAGRAPH_MIN_CHARS]


def jsonld_article(page: bytes | None) -> dict:
    """articleBody length and isAccessibleForFree from JSON-LD blocks, if the page declares them."""
    out: dict = {}
    if not page:
        return out
    try:
        doc = page.decode("utf-8", errors="replace")
    except Exception:
        return out
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', doc, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                stack.extend(v for k, v in node.items() if k == "@graph" or isinstance(v, (dict, list)))
                body = node.get("articleBody")
                if isinstance(body, str) and len(body) > out.get("articlebody_chars", 0):
                    out["articlebody_chars"] = len(html.unescape(re.sub(r"<[^>]+>", " ", body)).strip())
                free = node.get("isAccessibleForFree")
                if free is not None and "free" not in out:
                    out["free"] = str(free).strip().lower() not in ("false", "0", "no")
    return out


def _contains(hay: list, needle: list) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _page_tokens(page: bytes) -> list:
    doc = page.decode("utf-8", errors="replace")
    doc = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", doc, flags=re.S | re.I)
    return tokens(re.sub(r"<[^>]+>", " ", doc))


DOCUMENT_TYPES = {"transparency data", "guidance", "policy paper", "corporate report", "form", "statistics",
                  "official statistics", "research and analysis", "independent report", "decision", "collection"}
ATTACHMENT = re.compile(r'href="[^"]+\.(pdf|odt|ods|odp|csv|xlsx?|docx?)"', re.I)


def is_document_landing(title: str, page: bytes | None) -> bool:
    """A GOV.UK document page: the publication itself sits in attachments that are not read."""
    m = re.match(r"^\s*([^:]{3,40}):\s+", html.unescape(title or ""))
    return bool(page and m and m.group(1).strip().casefold() in DOCUMENT_TYPES
                and ATTACHMENT.search(page.decode("utf-8", errors="replace")))


def assess(title: str, text: str | None, page: bytes | None = None, identity: str | None = None,
           teaser: str | None = None) -> dict:
    """Return {"state": full|partial|mismatch|none, "reasons": [...], "signals": {...}}."""
    if not text or not text.strip():
        return {"state": "none", "reasons": ["no_text"], "signals": {}}
    text = text.strip()
    reasons: list[str] = []
    signals: dict = {"chars": len(text), "paragraphs": len(paragraphs(text))}

    # 1. Does the text belong to the headline?
    if identity not in (None, "ok"):
        reasons.append("identity:" + identity)
    title_tokens = [t for t in tokens(title) if len(t) >= 4 and t not in STOP]
    if len(title_tokens) >= 3:
        body = set(tokens(text[:3000]))
        share = sum(1 for t in title_tokens if t in body or any(b.startswith(t[:6]) for b in body if len(t) >= 7)) / len(title_tokens)
        signals["title_token_share"] = round(share, 2)
        if share < TITLE_TOKEN_SHARE:
            reasons.append("title_not_in_text")
    if any(r.startswith(("identity:publisher_title_mismatch", "title_not_in_text")) for r in reasons):
        return {"state": "mismatch", "reasons": reasons, "signals": signals}

    # 2. Is it the whole article?
    partial: list[str] = []
    lead = tokens(teaser or "")
    lead_inside = len(lead) >= 8 and _contains(tokens(text), lead)
    if len(text) < MIN_FULL_CHARS and not (lead_inside and len(text) >= SHORT_NEWS_MIN_CHARS):
        partial.append("short_text")
    if signals["paragraphs"] < MIN_PARAGRAPHS:
        partial.append("few_paragraphs")
    core = strip_credits(text)
    if core.endswith(TRUNCATION_ENDINGS):
        partial.append("truncated_ending")
    elif not core.endswith(TERMINAL):
        partial.append("no_terminal_punctuation")
    if MARKERS.search(text[-400:]) or MARKERS.search(text[:400]):
        partial.append("more_or_paywall_marker")
    if page and len(lead) >= 8 and not _contains(tokens(text), lead) and _contains(_page_tokens(page), lead):
        partial.append("lead_missing")  # the page shows the provider's lead/dek, the extracted body lost it
    ld = jsonld_article(page)
    signals.update({k: v for k, v in ld.items()})
    if ld.get("free") is False:
        partial.append("paywalled_jsonld")
    if ld.get("articlebody_chars"):
        cover = len(text) / ld["articlebody_chars"]
        signals["articlebody_coverage"] = round(cover, 2)
        if cover < ARTICLEBODY_COVERAGE:
            partial.append("below_articlebody")
    reasons.extend(partial)
    other = [r for r in reasons if r.startswith("identity:")]
    if partial or other:
        return {"state": "partial", "reasons": reasons, "signals": signals}
    if is_document_landing(title, page):
        return {"state": "partial", "reasons": ["document_in_attachment"], "signals": signals}
    return {"state": "full", "reasons": ["structure_ok"], "signals": signals}
