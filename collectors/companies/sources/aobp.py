"""AOBP (Czech Defence and Security Industry Association) member list -> company candidates (B1CD-02).

Listed in the EDA list of national defence industry associations (April 2026). Membership is a lead,
not a defence level (methodology N02): every row becomes a `company_candidate` for B2CI.
Page: https://www.aobp.cz/clenove/ — sections by specialisation, one `member__item` per company.
"""
import html
import re

URL = "https://www.aobp.cz/clenove/"
SOURCE = "aobp_members"

_SECTION = re.compile(r'<div class="member">\s*<div class="member__title">\s*<h2>(.*?)</h2>(.*?)(?=<div class="member">|$)', re.S)
_ITEM = re.compile(r'<div class="member__item"[^>]*data-member="([^"]+)"[^>]*>\s*<a href="([^"]+)".*?'
                   r'<p class="title">(.*?)</p>\s*(.*?)</figcaption>', re.S)


def _text(fragment):
    t = re.sub(r'<[^>]+>', ' ', fragment)
    return re.sub(r'\s+', ' ', html.unescape(t)).strip()


def parse(page: str):
    """Return one candidate per company; a company in several sections keeps all specialisations."""
    out = {}
    for section, body in _SECTION.findall(page):
        spec = _text(section)
        for slug, href, title, desc in _ITEM.findall(body):
            row = out.setdefault(slug, {
                'name_as_listed': _text(title), 'country': 'CZ', 'list_source_id': SOURCE,
                'list_row_ref': 'https://www.aobp.cz' + href if href.startswith('/') else href,
                'evidence_quote': _text(desc)[:400], 'specialisations': [], 'status': 'new'})
            if spec not in row['specialisations']:
                row['specialisations'].append(spec)
    return list(out.values())
