"""Find a company's own website and prove it is hers (B4CE-04, EXP-035 design v0, owner G2 25.11.2026).

197 of 302 cards had no products because no website was known. Order, free first:
1. registries that keep the website: Finland PRH/YTJ (website.url), Norway Brønnøysund (hjemmeside);
2. Wikidata P856 by the QID or LEI (P1278) already in the base;
3. web search (Tavily, Exa, Jina from source_registry.search.providers), one query, directories dropped.

Nothing is accepted without proof read on the site itself (home page plus one legal / imprint / contact page):
(a) the company's register number (SIREN, Y-tunnus, orgnr, VAT, KRS, IČO, UEI...), or
(b) the exact name without the legal form AND the register city.
A registry or Wikidata site needs only the name (the source already ties it to the company).
Every network call goes through `opener` / `search` so tests run offline.
"""
import json
import re
import unicodedata
import urllib.parse
import urllib.request

from .sources.website import UA, _fetch, allowed, to_text

LEGAL = re.compile(r'\b(gmbh|mbh|ag|kg|co|ohg|se|sas|sasu|sa|sarl|eurl|snc|as|asa|ab|oy|oyj|ltd|limited|plc|llc|inc|'
                   r'incorporated|corp|corporation|company|bv|nv|spa|srl|sl|sp|zoo|s\.?r\.?o|a\.?s|group|holding)\b')
# directories, social networks and registers: never the company's own site
NOT_OWN = ('linkedin.', 'wikipedia.', 'wikidata.', 'facebook.', 'twitter.', 'x.com', 'instagram.', 'youtube.',
           'societe.com', 'pappers.', 'infogreffe.', 'verif.com', 'manageo.', 'northdata.', 'kompass.', 'dnb.com',
           'zoominfo.', 'bloomberg.', 'crunchbase.', 'opencorporates.', 'proff.', 'purehelp.', 'finder.fi',
           'asiakastieto.', 'kauppalehti.', 'firmenwissen.', 'unternehmensregister.', 'handelsregister.', 'cbinsights.',
           'glassdoor.', 'indeed.', 'europa.eu', 'sam.gov', 'usaspending.', 'bizapedia.', 'buzzfile.',
           'dandb.', 'manta.com', 'mapquest.', 'yelp.', 'google.', 'annuaire', 'pagesjaunes.',
           'gulesider.', 'brreg.', 'prh.fi', 'ytj.fi', 'defence-industry.eu',
           'gleif.', 'lei-lookup', 'leilex', 'rocketreach.', 'apollo.io', 'pitchbook.', 'owler.', 'craft.co',
           # probe 25.11: search answers that were never a company's own site
           'tavily.com', 'leadiq.', 'tracxn.', 'gabin.ai', 'aeodirectory.', 'globaldata.', 'govtribe.', 'investing.',
           'yahoo.', 'devex.', 'railmarket.', 'satcatalog.', 'topograph.', 'cleat.ai', 'lightsource.ai',
           'usinenouvelle.', 'gunsweek.', 'spartanat.', 'jth.info', 'interconnect-wiring.', 'maps.apple', 'apple.com',
           'dnb.', 'signalhire.', 'cbinsights.', 'growjo.', 'zippia.', 'comparably.', 'datanyze.', 'theorg.',
           'defensenews.', 'army-technology.', 'naval-technology.', 'airforce-technology.', 'janes.', 'reuters.',
           'businesswire.', 'prnewswire.', 'globenewswire.', 'marketscreener.', 'macrotrends.', 'statista.')
LEGAL_PAGE = re.compile(r'mentions|legal|impressum|imprint|kontakt|contact|about|a-propos|qui-sommes|om-oss|'
                        r'yhteystied|tietoa|company|unternehmen|privacy|confidentialit', re.I)
_LINK = re.compile(r'<a\s[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.S | re.I)


FOLD = str.maketrans({'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'ß': 'ss', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'œ': 'oe'})


def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '').translate(FOLD)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', s)).strip()


def core(name):
    """Name without the legal form: 'Diehl Defence GmbH & Co. KG' -> 'diehl defence'."""
    n = norm(name).replace(' s r o', ' ').replace(' sp z o o', ' ')
    return re.sub(r'\s+', ' ', LEGAL.sub(' ', n)).strip()


def digits(s):
    return re.sub(r'\D', '', str(s or ''))


def home(url):
    u = url if '://' in url else 'https://' + url
    p = urllib.parse.urlsplit(u)
    return f'https://{p.hostname}/' if p.hostname else None


def own(url):
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    if not host or host == 'x.com' or host.endswith('.x.com') or re.search(r'\.gov(\.[a-z]{2})?$|\.gouv\.', host):
        return False
    return not any(b in host for b in NOT_OWN if b != 'x.com')


def _json(url, opener, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


# --- 1. registries ----------------------------------------------------------------------------------------
def registry(c, opener=urllib.request.urlopen):
    """{'site': url or None, 'ids': [register numbers found]}: the registry's website and numbers for the proof."""
    out = {'site': None, 'ids': []}
    name = c.get('legal_name') or c['name']
    city = norm(c.get('city'))
    try:
        if c['country'] == 'FI':
            d = _json('https://avoindata.prh.fi/opendata-ytj-api/v3/companies?' + urllib.parse.urlencode({'name': name}), opener)
            for x in d.get('companies') or []:
                if any(core(n.get('name')) == core(name) for n in x.get('names') or []):
                    out['ids'].append((x.get('businessId') or {}).get('value'))
                    out['site'] = out['site'] or ((x.get('website') or {}).get('url'))
        elif c['country'] == 'NO':
            nr = digits((c.get('ids') or {}).get('NATIONAL:NO'))
            if nr:
                xs = [_json(f'https://data.brreg.no/enhetsregisteret/api/enheter/{nr}', opener)]
            else:
                d = _json('https://data.brreg.no/enhetsregisteret/api/enheter?' + urllib.parse.urlencode({'navn': name, 'size': 5}), opener)
                xs = (d.get('_embedded') or {}).get('enheter') or []
            for x in xs:
                place = norm((x.get('forretningsadresse') or {}).get('poststed'))
                if core(x.get('navn')) == core(name) and (nr or not city or place == city):
                    out['ids'].append(x.get('organisasjonsnummer'))
                    out['site'] = out['site'] or x.get('hjemmeside')
        elif c['country'] == 'FR':
            d = _json('https://recherche-entreprises.api.gouv.fr/search?' + urllib.parse.urlencode({'q': name, 'per_page': 5}), opener)
            for x in d.get('results') or []:
                if core(x.get('nom_complet')) == core(name) and (not city or norm((x.get('siege') or {}).get('libelle_commune')) == city):
                    out['ids'].append(x.get('siren'))
    except Exception as e:  # noqa: BLE001 — a registry failure only means no help from it
        out['error'] = f'{type(e).__name__}'
    out['ids'] = [i for i in out['ids'] if i]
    return out


# --- 2. Wikidata ------------------------------------------------------------------------------------------
WD_API = 'https://www.wikidata.org/w/api.php?'


def wikidata(c, opener=urllib.request.urlopen):
    """P856 of the item: by QID, else the item whose LEI (P1278) matches. The entity API, not SPARQL (25.11.2026:
    the query service answered 429 «1 req / min» during an outage)."""
    ids = c.get('ids') or {}
    try:
        qid = ids.get('WIKIDATA')
        if not qid and ids.get('LEI'):
            d = _json(WD_API + urllib.parse.urlencode({'action': 'query', 'list': 'search', 'format': 'json',
                                                       'srsearch': f'haswbstatement:P1278={ids["LEI"]}'}), opener)
            hits = d.get('query', {}).get('search') or []
            qid = hits[0]['title'] if len(hits) == 1 else None
        if not qid:
            return None
        d = _json(WD_API + urllib.parse.urlencode({'action': 'wbgetclaims', 'entity': qid, 'property': 'P856',
                                                   'format': 'json'}), opener)
    except Exception:  # noqa: BLE001
        return None
    sites = [x['mainsnak'].get('datavalue', {}).get('value') for x in d.get('claims', {}).get('P856', [])]
    return next((s for s in sites if s), None)


# --- 3. search --------------------------------------------------------------------------------------------
def query(c):
    return ' '.join(filter(None, [c.get('legal_name') or c['name'], (c.get('city') or '').title(), COUNTRY.get(c['country'], c['country'])]))


COUNTRY = {'FR': 'France', 'DE': 'Germany', 'NO': 'Norway', 'US': 'USA', 'FI': 'Finland', 'PL': 'Poland', 'ES': 'Spain',
           'CZ': 'Czech Republic', 'SE': 'Sweden', 'IT': 'Italy', 'GB': 'United Kingdom', 'EE': 'Estonia', 'AT': 'Austria',
           'BE': 'Belgium', 'NL': 'Netherlands', 'DK': 'Denmark', 'CA': 'Canada'}


def label(url):
    """Registrable name of the host: 'https://www.lisi-aerospace-am.com/' -> 'lisi-aerospace-am'."""
    host = (urllib.parse.urlsplit(url).hostname or '').lower().removeprefix('www.')
    parts = host.split('.')
    return parts[-3] if len(parts) >= 3 and parts[-2] in ('co', 'com', 'org') else (parts[-2] if len(parts) >= 2 else host)


def domain_is_name(c, url):
    n = core(c.get('legal_name') or c['name'])
    return bool(n) and label(url).replace('-', '') == n.replace(' ', '')


def candidates(results, c=None, n=5):
    """Own-looking home pages, those whose domain carries a word of the name first (probe 25.11: search put
    directories before the company's site)."""
    out = []
    for r in results:
        h = home(r.get('url') or '')
        if h and own(h) and h not in out:
            out.append(h)
    if c:
        words = [w for w in core(c.get('legal_name') or c['name']).split() if len(w) >= 3]
        out.sort(key=lambda h: 0 if any(w in label(h).replace('-', '') for w in words) else 1)
    return out[:n]


# --- proof ------------------------------------------------------------------------------------------------
def legal_pages(raw, base, n=2):
    host = urllib.parse.urlsplit(base).hostname
    out = []
    for href, anchor in _LINK.findall(raw):
        url = urllib.parse.urljoin(base, href.strip())
        if urllib.parse.urlsplit(url).hostname != host or url in out:
            continue
        if LEGAL_PAGE.search(href + ' ' + re.sub(r'<[^>]+>', ' ', anchor)):
            out.append(url)
    order = ('mentions', 'impressum', 'imprint', 'legal', 'contact', 'kontakt')
    return sorted(out, key=lambda u: next((i for i, k in enumerate(order) if k in u.lower()), 9))[:n]


def read(url, opener):
    """Text of the home page and up to two legal / contact pages; {} when the site does not open."""
    texts, final = [], None
    try:
        if not allowed(url, opener):
            return {'error': 'robots.txt disallows'}
        final, raw = _fetch(url, opener)
        texts.append(to_text(raw))
        for link in legal_pages(raw, final):
            try:
                if allowed(link, opener):
                    texts.append(to_text(_fetch(link, opener)[1]))
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        return {'error': f'{type(e).__name__}: {str(e)[:60]}'}
    return {'url': home(final or url), 'text': ' '.join(texts)}


def proof(c, text, reg_ids, trusted, url=None):
    """Why this site is the company's, or None."""
    t = norm(text)
    flat = digits(text)
    ids = [str(v) for v in (c.get('ids') or {}).values() if v] + [str(i) for i in reg_ids]
    for i in ids:
        d = digits(i)
        if len(d) >= 8 and d in flat and not set(d) <= {'0'}:
            return f'register number {i} on the site'
        if re.fullmatch(r'[A-Z0-9]{12}', i) and re.search(r'[A-Z]', i) and i.lower() in t.replace(' ', ''):
            return f'UEI {i} on the site'
    name = core(c.get('legal_name') or c['name'])
    if not name or len(name) < 3 or f' {name} ' not in f' {t} ':
        return None
    if trusted:
        return 'name on the site; site from the register or Wikidata'
    if url and domain_is_name(c, url):
        return 'name on the site, and the domain is the name'
    city = norm(c.get('city'))
    if city and f' {city} ' in f' {t} ':
        return f'name and city ({c.get("city")}) on the site'
    return None


def find(c, search=None, opener=urllib.request.urlopen, log=lambda *_: None):
    """{'website', 'via', 'proof'} or {'website': None, 'tried': [...]}. search(query) -> [{'url', 'title'}]."""
    tried = []
    reg = registry(c, opener)
    first = [(reg['site'], 'register', True)] if reg.get('site') else []
    wd = wikidata(c, opener)
    if wd:
        first.append((wd, 'wikidata', True))
    for url, via, trusted in first:
        h = home(url)
        page = read(h, opener) if h else {'error': 'bad url'}
        why = proof(c, page.get('text', ''), reg['ids'], trusted) if 'text' in page else None
        tried.append({'url': h, 'via': via, 'result': why or page.get('error') or 'no proof on the site'})
        if why:
            return {'website': page['url'], 'via': via, 'proof': why, 'ids': reg['ids'], 'tried': tried}
    if search:
        try:
            found = candidates(search(query(c)), c)
        except Exception as e:  # noqa: BLE001
            found = []
            tried.append({'via': 'search', 'result': f'search failed: {type(e).__name__}'})
        for h in found:
            page = read(h, opener)
            why = proof(c, page.get('text', ''), reg['ids'], False, h) if 'text' in page else None
            tried.append({'url': h, 'via': 'search', 'result': why or page.get('error') or 'no proof on the site'})
            if why:
                return {'website': page['url'], 'via': 'search', 'proof': why, 'ids': reg['ids'], 'tried': tried}
    return {'website': None, 'ids': reg['ids'], 'tried': tried}
