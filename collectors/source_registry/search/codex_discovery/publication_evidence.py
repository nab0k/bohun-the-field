"""Limited evidence from official publication pages and feeds."""
from datetime import date, datetime
from html.parser import HTMLParser
import json
import re
import unicodedata


POLISH_MONTHS = dict(zip(
    "stycznia lutego marca kwietnia maja czerwca lipca sierpnia wrzesnia pazdziernika listopada grudnia".split(),
    range(1, 13),
))


def folded(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold().replace("ł", "l"))
                   if not unicodedata.combining(c))


def publication_date(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"^opublikowano\s*:?\s+", "", value.strip(), flags=re.IGNORECASE)
    try:
        plain = re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        if plain:
            return date.fromisoformat(value)
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            return datetime.fromisoformat(value).date()
        polish = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})(?:\s*r\.)?", folded(value))
        if polish and polish[2] in POLISH_MONTHS:
            return date(int(polish[3]), POLISH_MONTHS[polish[2]], int(polish[1]))
    except ValueError:
        return None
    return None


class PublicationPage(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.lang = ""
        self.metadata = {}
        self.article_links = []
        self.article_dates = []
        self.title = ""
        self.heading = ""
        self.article = None
        self.link = None
        self.in_heading = False
        self.text_tag = None
        self.schema_text = None
        self.time_text = None
        self.feed(body.decode("utf-8", errors="replace"))
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.lang = attrs.get("lang") or ""
        elif tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if key and attrs.get("content"):
                self.metadata.setdefault(key.lower(), attrs["content"][:300])
        elif tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self.schema_text = []
        elif tag == "article":
            self.article = []
        elif tag in ("h2", "h3", "h4") and self.article is not None:
            self.in_heading = True
            if self.link is not None:
                self.link[2] = True
        elif tag == "a" and self.article is not None and attrs.get("href"):
            is_title = self.in_heading or "bookmark" in (attrs.get("rel") or "").split()
            self.link = [attrs["href"], [], is_title]
        elif tag == "time" and self.article is not None:
            marker = folded(f"{attrs.get('itemprop', '')} {attrs.get('class', '')}")
            if not any(word in marker for word in ("modified", "updated", "entry-date-updated")):
                value = publication_date(attrs.get("datetime"))
                if value:
                    self.article.append(value)
                elif not attrs.get("datetime"):
                    self.time_text = []
        if tag in ("title", "h1"):
            self.text_tag = tag

    def handle_data(self, text):
        if self.text_tag == "title" and len(self.title) < 300:
            self.title += text[:300]
        elif self.text_tag == "h1" and len(self.heading) < 300:
            self.heading += text[:300]
        if self.link is not None and len(self.link[1]) < 12:
            self.link[1].append(text[:100])
        if self.schema_text is not None and len(self.schema_text) < 8:
            self.schema_text.append(text[:24_000])
        if self.time_text is not None and len(self.time_text) < 8:
            self.time_text.append(text[:100])
        if self.article is not None and re.match(r"^opublikowano\b", folded(text.strip())):
            value = publication_date(text)
            if value:
                self.article.append(value)

    def handle_endtag(self, tag):
        if tag == self.text_tag:
            self.text_tag = None
        if tag == "script" and self.schema_text is not None:
            self.read_schema("".join(self.schema_text))
            self.schema_text = None
        if tag == "time" and self.time_text is not None:
            value = publication_date(" ".join(self.time_text))
            if value and self.article is not None:
                self.article.append(value)
            self.time_text = None
        if tag in ("h2", "h3", "h4"):
            self.in_heading = False
        elif tag == "a" and self.link is not None:
            href, parts, is_title = self.link
            title = " ".join(" ".join(parts).split())[:240]
            if is_title and title and len(self.article_links) < 16:
                self.article_links.append((href, title, self.article))
            self.link = None
        elif tag == "article":
            if self.article and len(self.article_dates) < 12:
                self.article_dates.extend(self.article[:2])
            self.article = None
            self.link = None
            self.in_heading = False

    def read_schema(self, raw):
        try:
            document = json.loads(raw)
        except (ValueError, RecursionError):
            return
        nodes = document if isinstance(document, list) else [document]
        for node in nodes[:20]:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            for item in [node, *(graph[:20] if isinstance(graph, list) else [])]:
                if not isinstance(item, dict):
                    continue
                kinds = item.get("@type")
                kinds = kinds if isinstance(kinds, list) else [kinds]
                if any(kind in ("Article", "NewsArticle", "BlogPosting") for kind in kinds):
                    value = publication_date(item.get("datePublished"))
                    if value:
                        self.metadata.setdefault("datepublished", value.isoformat())

    def individual_date(self):
        for key in ("article:published_time", "datepublished"):
            value = publication_date(self.metadata.get(key))
            if value:
                return value
        if self.heading.strip() and len(set(self.article_dates)) == 1:
            return self.article_dates[0]
        return None
