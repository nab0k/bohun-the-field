"""Work queue for company cards, design B v1 slice 3 (step 3.2, B4CE-04).

Order (design B v1, "Очередь"):
  1 event          the company is in a fresh event: its legal name appears in a published synthesis
                   of the last EVENT_DAYS days, or it has a grant/award signal in the last SIGNAL_DAYS days;
  2 open_register  its country has a free register route (registries.resolve): CZ, NO, FR, FI, and PL
                   when a KRS number is known (national identifier or LEI);
  3 lei            it has an LEI (GLEIF gives the legal name and register number);
  4 no_route       no free route: stays a candidate until a customer document names it.
Companies that already have an approved card are not queued again unless an event touches them
(then the card is refreshed). Companies without a country cannot be routed: tier 'no_country'.
Every entry carries the reason for its place. Pure functions: the caller supplies the rows.
"""
import re
from datetime import date, timedelta

from .normalize import name_norm

EVENT_DAYS = 14
SIGNAL_DAYS = 90
OPEN_REGISTER = {'CZ', 'NO', 'FR', 'FI'}
TIERS = ['event', 'open_register', 'lei', 'no_route', 'no_country']
# A name shorter than this, or a single generic word, would match news text by accident.
MIN_NAME_CHARS = 6
GENERIC = {'defence', 'defense', 'aerospace', 'systems', 'technologies', 'group', 'industries', 'international',
           'solutions', 'engineering', 'electronics', 'security', 'holding', 'research', 'army', 'navy'}


PLACES = {  # country and nationality words: a name reduced to one of these is not a mention
    'america', 'american', 'australia', 'australian', 'austria', 'austrian', 'belgium', 'belgian', 'britain', 'british',
    'canada', 'canadian', 'china', 'chinese', 'czech', 'denmark', 'danish', 'estonia', 'estonian', 'europe', 'european',
    'finland', 'finnish', 'france', 'french', 'georgia', 'georgian', 'germany', 'german', 'greece', 'greek', 'india',
    'indian', 'israel', 'israeli', 'italy', 'italian', 'japan', 'japanese', 'korea', 'korean', 'latvia', 'lithuania',
    'netherlands', 'dutch', 'norway', 'norwegian', 'poland', 'polish', 'portugal', 'romania', 'russia', 'russian',
    'spain', 'spanish', 'sweden', 'swedish', 'switzerland', 'swiss', 'turkey', 'turkish', 'ukraine', 'ukrainian',
    'united', 'states', 'kingdom', 'nato', 'atlantic', 'baltic', 'nordic', 'pacific'}
# One-word company names in companies.company (21.11.2026) that are ordinary English words or common
# surnames (checked against the system dictionary, reviewed by hand). Distinctive names that are also
# dictionary words (Patria, Bombardier, Marconi, Intermarine) stay matchable.
ORDINARY = {
    'actionable', 'alchemy', 'allies', 'altercation', 'ascendance', 'astute', 'banzai', 'basalt', 'beetroot',
    'bitwise', 'blackberry', 'cantaloupe', 'catalyst', 'cohere', 'contact', 'digital', 'exploration', 'graham',
    'halogen', 'hexagon', 'impact', 'institute', 'irreversible', 'latitude', 'meridian', 'metric', 'mnemonic',
    'nimbus', 'orange', 'steeple', 'sterling', 'subset', 'switchboard', 'swordfish', 'systematic', 'tensor',
    'triode', 'unplugged', 'vector', 'verify'}
_WORD = re.compile(r"[\w'’]+|&")


def mention_key(name):
    """Normalised name tokens usable for a text match, or None when they would match by accident."""
    key = name_norm(name)
    words = key.split()
    if len(key) < MIN_NAME_CHARS or not words or all(w in GENERIC or w in PLACES for w in words):
        return None
    return tuple(words)


def _tokens(text):
    """[(normalised token, original token)]; '&' becomes 'and' as in name_norm."""
    out = []
    for m in _WORD.finditer(text or ''):
        raw = m.group()
        for part in (name_norm(raw) or ('and' if raw == '&' else '')).split():
            out.append((part, raw))
    return out


def _named(tokens, key):
    """True when key occurs as consecutive tokens and every word of it is written as a proper noun
    (capital first letter or digits) — 'impact', 'plug-and-play' or 'metric' in running text do not count."""
    n = len(key)
    for i in range(len(tokens) - n + 1):
        if all(tokens[i + j][0] == key[j] for j in range(n)):
            if all(raw[:1].isupper() or raw[:1].isdigit() or norm in ('and', 'of', 'de', 'di', 'des', 'du', 'la', 'le')
                   for norm, raw in tokens[i:i + n]):
                return True
    return False


def event_mentions(companies, items, today, days=EVENT_DAYS):
    """{company_id: (date, item_id)} for the newest synthesis that names the company exactly."""
    since = (today - timedelta(days=days)).isoformat()
    texts = []
    for it in items:
        if it.get('status') != 'approved' or (it.get('date') or '') < since:
            continue
        body = ' . '.join([it.get('title') or '', it.get('summary') or ''] + list(it.get('paragraphs') or []))
        texts.append((it['date'], it['id'], _tokens(body)))
    texts.sort(key=lambda t: (t[0], t[1]), reverse=True)
    # Words the feed itself writes in lower case are ordinary words ('digital', 'contact'): a one-word
    # name equal to such a word is not a mention, even where a headline capitalises it.
    common = {norm for _, _, toks in texts for norm, raw in toks if raw.islower()}
    out = {}
    for c in companies:
        key = mention_key(c['name'])
        if not key or (len(key) == 1 and (key[0] in common or key[0] in ORDINARY)):
            continue
        for d, iid, toks in texts:
            if _named(toks, key):
                out[c['id']] = (d, iid)
                break
    return out


def route(c):
    country = c.get('country')
    if not country:
        return 'no_country', 'no country in the record: register route unknown'
    if country in OPEN_REGISTER:
        return 'open_register', f'{country}: free national register'
    if country == 'PL' and (c.get('national_id') or c.get('lei')):
        return 'open_register', 'PL: KRS confirmed from the known number'
    if c.get('lei'):
        return 'lei', f'{country}: LEI known, legal name from GLEIF'
    return 'no_route', f'{country}: no free register and no LEI'


def build(companies, items, today=None, signal_days=SIGNAL_DAYS, keep=None):
    """companies: dicts with id, name, country, lei (bool), national_id (bool), last_signal (ISO date or None),
    has_card (bool). keep: ids of carded companies to queue even without an event (the refresh schedule chose
    them, refresh.py). Returns the ordered queue (list of dicts) and counts per tier."""
    keep = keep or set()
    today = today or date.today()
    mentions = event_mentions(companies, items, today)
    since_signal = (today - timedelta(days=signal_days)).isoformat()
    queue = []
    for c in companies:
        ev = []
        if c['id'] in mentions:
            d, iid = mentions[c['id']]
            ev.append((d, f'named in synthesis {iid} ({d})'))
        if c.get('last_signal') and c['last_signal'] >= since_signal:
            ev.append((c['last_signal'], f'grant or award signal {c["last_signal"]}'))
        reg_tier, reg_reason = route(c)
        if ev:
            last = max(d for d, _ in ev)
            tier, reason = 'event', '; '.join(r for _, r in ev) + f'; route: {reg_reason}'
        elif c.get('has_card') and c['id'] not in keep:
            continue  # approved card and nothing new: not queued unless its refresh is due
        else:
            last, tier, reason = None, reg_tier, reg_reason
        queue.append({'company_id': c['id'], 'name': c['name'], 'country': c.get('country'), 'tier': tier,
                      'route': reg_tier, 'refresh': bool(c.get('has_card')), 'last_event': last, 'reason': reason})
    rank = {t: i for i, t in enumerate(TIERS)}
    queue.sort(key=lambda q: (rank[q['tier']], _neg_date(q['last_event']), q['country'] or 'ZZ', q['name'].lower()))
    for i, q in enumerate(queue, 1):
        q['position'] = i
    counts = {t: sum(q['tier'] == t for q in queue) for t in TIERS}
    return queue, counts


def _neg_date(d):
    """Sort newest first without parsing: invert the digits of an ISO date."""
    return re.sub(r'\d', lambda m: str(9 - int(m.group())), d) if d else '~'


EXPORT_SQL = """select c.id, c.name, c.country,
  exists (select 1 from companies.company_identifier i where i.company_id = c.id and i.scheme = 'LEI') as lei,
  exists (select 1 from companies.company_identifier i where i.company_id = c.id and i.scheme = 'NATIONAL:' || c.country) as national_id,
  (select max(s.signal_date)::text from companies.signal s where s.company_id = c.id and s.type in ('grant', 'tender_award')) as last_signal,
  exists (select 1 from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED') as has_card
from companies.company c
where c.excluded_reason is null
order by c.id"""
