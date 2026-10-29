"""Read open pages (GET only, no login), turn HTML into Markdown with section anchors, verify rows.

PDF is never stored: a PDF answer is recorded as such and the document stays a link-only card.
"""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MAX_BYTES = 3_000_000
TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; BOHUN-research/1.0; +https://bohundefence.com)"

# Publishers whose documents we keep as Markdown (official reuse); everything else is link-only.
OFFICIAL_SUFFIXES = (".gov", ".mil", ".gov.uk", ".mod.uk", ".parliament.uk", ".europa.eu", ".nato.int",
                     ".gc.ca", ".gov.pl", ".gov.au", ".govt.nz", ".bund.de", ".bundeswehr.de", ".gouv.fr",
                     ".defensie.nl", ".rijksoverheid.nl", ".regeringen.se", ".government.se", ".fmv.se",
                     ".forsvaret.no", ".regjeringen.no", ".fmn.dk", ".defmin.fi", ".gov.lt", ".gov.lv",
                     ".mil.pl", ".gov.ua", ".mil.gov.ua", ".congress.gov", ".govinfo.gov", ".un.org")
OFFICIAL_HOSTS = {"europa.eu", "nato.int", "congress.gov", "defense.gov", "war.gov", "state.gov", "gov.uk",
                  "dsca.mil", "federalregister.gov", "govinfo.gov", "sam.gov"}


def host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def is_official(url):
    h = host(url)
    return h in OFFICIAL_HOSTS or any(h.endswith(s) or h == s[1:] for s in OFFICIAL_SUFFIXES)


class _Markdown(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"}
    BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "table", "ul", "ol", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.heading, self.title, self.in_title = [], 0, None, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif re.fullmatch(r"h[1-4]", tag):
            self.heading = int(tag[1]); self.out.append("\n\n" + "#" * self.heading + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self.in_title = False
        elif re.fullmatch(r"h[1-4]", tag):
            self.heading = None; self.out.append("\n\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))


def html_to_markdown(html):
    parser = _Markdown()
    parser.feed(html)
    text = "".join(parser.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return parser.title.strip(), text


def sections(markdown):
    """Split Markdown into anchored sections: s0 is text before the first heading."""
    out, current = [], {"anchor": "s0", "heading": "", "lines": []}
    for line in markdown.split("\n"):
        m = re.match(r"^(#{1,4}) (.+)$", line)
        if m:
            if current["lines"] or current["heading"]:
                out.append(current)
            current = {"anchor": "s%d" % (len(out) + 1), "heading": m.group(2).strip(), "lines": []}
        else:
            current["lines"].append(line)
    out.append(current)
    return [{"anchor": s["anchor"], "heading": s["heading"], "text": "\n".join(s["lines"]).strip()}
            for s in out if s["heading"] or "".join(s["lines"]).strip()]


def fetch(url):
    """One GET. Returns a dict with status, kind (html, pdf, other, error), title, markdown."""
    if not url or not url.startswith(("https://", "http://")):
        return {"url": url, "status": None, "kind": "error", "error": "bad_url"}
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as reply:
            ctype = (reply.headers.get("Content-Type") or "").lower()
            final = reply.geturl()
            if "pdf" in ctype or final.lower().endswith(".pdf"):
                return {"url": url, "final_url": final, "status": reply.status, "kind": "pdf"}
            raw = reply.read(MAX_BYTES)
            charset = reply.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        return {"url": url, "status": exc.code, "kind": "error", "error": "http_%d" % exc.code}
    except Exception as exc:
        return {"url": url, "status": None, "kind": "error", "error": type(exc).__name__}
    if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
        return {"url": url, "final_url": final, "status": 200, "kind": "other"}
    title, markdown = html_to_markdown(raw.decode(charset, "replace"))
    return {"url": url, "final_url": final, "status": 200, "kind": "html", "title": title, "markdown": markdown}


def fetch_all(urls, workers=8):
    urls = sorted({u for u in urls if u})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(urls, pool.map(fetch, urls)))


# --- verification -------------------------------------------------------------

def norm(text):
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).lower()


def amount_forms(amount):
    """Ways an amount may be written: 58.62 → '58.62', '58,62'; 4760000000 → '4.76', '4,760'."""
    if amount in (None, ""):
        return []
    try:
        x = float(amount)
    except (TypeError, ValueError):
        return []
    forms = set()
    for scale in (1, 1e3, 1e6, 1e9):
        v = x / scale
        if v < 1 and scale > 1:
            continue
        for s in ("%g" % v, ("%.2f" % v).rstrip("0").rstrip("."), ("%.1f" % v).rstrip("0").rstrip(".")):
            forms.add(s); forms.add(s.replace(".", ","))
    if x >= 1000:
        forms.add("{:,.0f}".format(x)); forms.add("{:,.0f}".format(x).replace(",", "."))
    return [f for f in forms if f and f != "0"]


def check_row(row, text):
    """ok / date_absent / amount_absent against the text of the row's own source."""
    t = norm(text)
    if not t:
        return "source_unread"
    year = (row.get("date") or "")[:4]
    if year.isdigit() and year not in t:
        return "date_absent"
    forms = amount_forms(row.get("amount"))
    if forms and not any(f.lower() in t for f in forms):
        return "amount_absent"
    return "ok"
