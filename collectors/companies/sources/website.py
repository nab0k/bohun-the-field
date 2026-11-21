"""Company website pages for product extraction (B4CE-04, slice 3.3).

Reads the home page and up to MAX_PAGES product pages on the same host. Rules (design B v1, risks):
robots.txt is respected; a 403, a login wall or a JavaScript-only page leaves the section empty with
the reason, with no attempt to get around it; https first, http only when https fails to connect.
Text is plain: scripts, styles and tags removed, whitespace collapsed. Every call goes through
`opener` so tests run without network.
"""
import html
import re
import urllib.parse
import urllib.request
import urllib.robotparser

UA = "BOHUN-research/1.0"
MAX_BYTES = 1_500_000
MAX_PAGES = 4
PRODUCT_HINTS = ('product', 'produkt', 'vyrob', 'výrob', 'portfolio', 'solution', 'reseni', 'řešení', 'system',
                 'offer', 'nabidk', 'nabídk', 'capabilit', 'equipment', 'vehicle', 'ammunition', 'munice', 'radar',
                 'tuote', 'produit', 'produkty', 'technolog')
SKIP = ('login', 'cart', 'kosik', 'košík', 'privacy', 'cookie', 'gdpr', 'career', 'kariera', 'kariéra', 'job',
        'news', 'novinky', 'aktuality', 'press', 'contact', 'kontakt', '.pdf', '.jpg', '.png', '.zip', 'mailto:', 'tel:')
_TAG = re.compile(r'<(script|style|noscript|svg)[^>]*>.*?</\1>', re.S | re.I)
_LINK = re.compile(r'<a\s[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.S | re.I)


def to_text(raw_html):
    t = _TAG.sub(' ', raw_html)
    t = re.sub(r'<br\s*/?>|</p>|</li>|</h\d>|</div>', '\n', t, flags=re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = html.unescape(t).replace('\xad', '')
    return re.sub(r'[ \t\r\f\v]+', ' ', re.sub(r'\n\s*\n+', '\n', t)).strip()


def product_links(raw_html, base):
    """Same-host links whose address or anchor text looks like a product page, in page order."""
    host = urllib.parse.urlsplit(base).hostname
    seen, out = set(), []
    for href, anchor in _LINK.findall(raw_html):
        url = urllib.parse.urljoin(base, html.unescape(href.strip()))
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ('http', 'https') or parts.hostname != host:
            continue
        url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
        probe = (parts.path + ' ' + re.sub(r'<[^>]+>', ' ', anchor)).lower()
        if url in seen or url.rstrip('/') == base.rstrip('/') or any(s in probe for s in SKIP):
            continue
        if any(h in probe for h in PRODUCT_HINTS):
            seen.add(url)
            out.append(url)
    return out


def _fetch(url, opener):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html'})
    with opener(req, timeout=30) as r:
        ctype = (r.headers.get('Content-Type') or '') if hasattr(r, 'headers') and r.headers else 'text/html'
        if 'html' not in ctype.lower():
            raise ValueError('not html: ' + ctype)
        body = r.read(MAX_BYTES)
        return getattr(r, 'url', None) or url, body.decode('utf-8', 'replace')


def allowed(url, opener):
    """robots.txt check; an unreachable robots.txt counts as allowed (standard behaviour)."""
    parts = urllib.parse.urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(f'{parts.scheme}://{parts.netloc}/robots.txt', headers={'User-Agent': UA})
        with opener(req, timeout=20) as r:
            rp.parse(r.read(200_000).decode('utf-8', 'replace').splitlines())
    except Exception:
        return True
    return rp.can_fetch(UA, url)


def read_site(website, opener=urllib.request.urlopen, max_pages=MAX_PAGES):
    """{'pages': [{'url', 'text'}], 'skipped': [(url, reason)]}. Never raises."""
    out = {'pages': [], 'skipped': []}
    if not website:
        out['skipped'].append((None, 'no website in the record'))
        return out
    start = website.strip() if '://' in website else 'https://' + website.strip()
    if not urllib.parse.urlsplit(start).path:
        start += '/'
    home = None
    for url in (start, start.replace('https://', 'http://', 1)) if start.startswith('https://') else (start,):
        try:
            if not allowed(url, opener):
                out['skipped'].append((url, 'robots.txt disallows'))
                return out
            final, raw = _fetch(url, opener)
            home = (final, raw)
            break
        except Exception as e:  # noqa: BLE001 — reason is recorded, the card shows an empty section
            out['skipped'].append((url, f'{type(e).__name__}: {str(e)[:80]}'))
    if not home:
        return out
    final, raw = home
    text = to_text(raw)
    if len(text) < 200:
        out['skipped'].append((final, 'almost no text: page needs JavaScript'))
    else:
        out['pages'].append({'url': final, 'text': text})
    for link in product_links(raw, final)[:max_pages]:
        try:
            if not allowed(link, opener):
                out['skipped'].append((link, 'robots.txt disallows'))
                continue
            u, r = _fetch(link, opener)
            t = to_text(r)
            if len(t) >= 200:
                out['pages'].append({'url': u, 'text': t})
            else:
                out['skipped'].append((u, 'almost no text: page needs JavaScript'))
        except Exception as e:  # noqa: BLE001
            out['skipped'].append((link, f'{type(e).__name__}: {str(e)[:80]}'))
    return out
