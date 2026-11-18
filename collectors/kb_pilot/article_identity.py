"""Compare the incoming headline with the fetched article's own metadata."""

from html import unescape
from html.parser import HTMLParser
import re
import unicodedata


class Headlines(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.headings = {}
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            fields = dict(attrs)
            key = (fields.get("property") or fields.get("name") or "").casefold()
            value = (fields.get("content") or "").strip()
            if key in ("og:title", "twitter:title", "og:site_name") and value:
                self.meta.setdefault(key, value)
        elif tag in ("h1", "title") and tag not in self.headings:
            self.headings[tag] = []
            self.current = tag

    def handle_endtag(self, tag):
        if tag == self.current:
            self.current = None

    def handle_data(self, data):
        if self.current:
            self.headings[self.current].append(data)

    def best(self):
        return (
            self.meta.get("og:title")
            or self.meta.get("twitter:title")
            or "".join(self.headings.get("h1", [])).strip()
            or "".join(self.headings.get("title", [])).strip()
        )


def tokens(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(char for char in value if unicodedata.category(char) != "Cf")
    return re.findall(r"[^\W_]+", value, re.UNICODE)


def without_site_suffix(value, site_name):
    parts = re.split(r"\s+[-–—|]\s+", value)
    if len(parts) > 1 and site_name and tokens(parts[-1]) == site_name:
        return " ".join(parts[:-1])
    return value


def _compare_strict(expected, page):
    if not isinstance(expected, str) or not tokens(expected):
        return "incoming_title_missing"
    parser = Headlines()
    try:
        parser.feed(page.decode("utf-8"))
        parser.close()
    except UnicodeError:
        return "publisher_title_unreadable"
    headline = parser.best()
    if not tokens(headline):
        return "publisher_title_missing"

    site_name = tokens(parser.meta.get("og:site_name", ""))
    incoming = tokens(without_site_suffix(expected, site_name))
    published = tokens(without_site_suffix(headline, site_name))
    if incoming == published:
        return "ok"

    # A single English article can change during copy-editing without changing
    # the headline. Keep other edits, including digits and negation, visible.
    articles = {"a", "an", "the"}
    if min(len(incoming), len(published)) >= 5 and abs(len(incoming) - len(published)) == 1:
        shorter, longer = sorted((incoming, published), key=len)
        if any(longer[:i] + longer[i + 1:] == shorter
               for i, word in enumerate(longer) if word in articles):
            return "ok"
    return "publisher_title_mismatch"


# A5TC-02 v2 (D-20261118-A5TC-02-V2-TRANSFER). Checked on two shadow samples (EXP-034):
# the strict rule rejected 6/6 correct pages on 16.11. Relaxations are narrow; numbers, negation
# and extra words still fail through the strict comparison.
GOVUK_TYPES = frozenset((
    "transparency data", "guidance", "news story", "press release", "policy paper", "corporate report", "form",
    "statistics", "official statistics", "research and analysis", "notice", "speech", "collection",
    "consultation outcome", "open consultation", "decision", "independent report", "promotional material"))


def _page_headlines(page):
    parser = Headlines()
    try:
        parser.feed(page.decode("utf-8"))
        parser.close()
    except UnicodeError:
        return []
    return [h for h in (parser.meta.get("og:title"), "".join(parser.headings.get("h1", [])).strip()) if h]


def compare_headline_rule(expected, page):
    """(result, rule): rule is strict / unescaped_title / govuk_type_prefix / site_or_rubric_prefix."""
    strict = _compare_strict(expected, page)
    if strict == "ok" or not isinstance(expected, str):
        return strict, "strict"
    title = unescape(expected)
    if _compare_strict(title, page) == "ok":
        return "ok", "unescaped_title"
    prefix = re.match(r"^\s*([^:]{3,40}):\s+(.+)$", title)
    if prefix and prefix.group(1).strip().casefold() in GOVUK_TYPES and _compare_strict(prefix.group(2), page) == "ok":
        return "ok", "govuk_type_prefix"
    wanted = tokens(title)
    if len(wanted) >= 3:
        for headline in _page_headlines(page):
            segments = re.split(r"\s*[:|]\s*", headline)
            if any(tokens(" ".join(segments[i:])) == wanted for i in range(1, len(segments))):
                return "ok", "site_or_rubric_prefix"
    return strict, "strict"


def compare_headline(expected, page):
    return compare_headline_rule(expected, page)[0]


def lead_tokens_on_page(page):
    """Visible page text as tokens (scripts and styles removed), for locating a provider lead."""
    doc = page.decode("utf-8", errors="replace")
    doc = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", doc, flags=re.S | re.I)
    return tokens(unescape(re.sub(r"<[^>]+>", " ", doc)))


def contains(hay, needle):
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def lead_merge(teaser, body, page):
    """(text, lead_source). The page shows the provider's lead but the extractor dropped it -> put it first."""
    lead = tokens(teaser or "")
    if len(lead) < 8 or not body or contains(tokens(body), lead):
        return body, None
    if contains(lead_tokens_on_page(page), lead):
        return teaser.strip() + "\n" + body, "provider_content_capture"
    return body, None
