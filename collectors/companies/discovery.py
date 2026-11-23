"""New companies from the knowledge base mentions (B4CE-04, owner 23.11.2026: every company named in a news item,
signal or synthesis must be in the companies base; design reference/slice3/DESIGN_COMPANY_DISCOVERY_v0.md, EXP-033).

The knowledge base extractor (kb-extract-v2) stores every organisation it sees as knowledge.node of kind 'company'
or 'org'. About half of the 'company' nodes are products, systems, descriptions or public bodies, so nothing is
added as is. Each mention goes through three steps:

1. split: "Rolls-Royce / MTU" and "Rheinmetall BAE Systems Land (RBSL) and KNDS UK (WFEL)" name several companies;
2. classify by code rules: company / gov (armed forces, ministries, agencies) / university (deferred category) /
   not_org (product, description, country, plural group) / unsure (left to the model step);
3. match against companies.company and its aliases: one record -> link, several -> ambiguous (no link),
   none -> a new candidate company.

Dry run only in this version: nothing is written. The model step for 'unsure' names is a later stage.
"""
import json
import re
import unicodedata

from .normalize import LEGAL_FORMS, name_norm

_FORM_WORD = re.compile(r"(?<![\w])(" + "|".join(sorted(LEGAL_FORMS, key=len, reverse=True)) + r")\.?(?![\w])", re.I)

# words of a public body: an army, a ministry, an agency, a command, a government or an international body
GOV = re.compile(
    r"\b(army|armee|armée|navy|naval forces|air force|space force|marines?|armed forces|forces armées|armed|forces|"
    r"guards?|squadron|regiment|brigade|battalion|division|corps|command|task force|ministry|ministerium|ministère|"
    r"ministerio|ministero|ministerstvo|verteidigungsministerium|department|departement|agency|agence|agentur|office|"
    r"government|gouvernement|regierung|parliament|parlament|senate|congress|commission|council|"
    r"nato|otan|european union|\beu\b|united nations|pentagon|bundeswehr|luftstreitkräfte|streitkräfte|"
    r"dirección general|direzione generale|police|polizei|gendarmerie|coast guard|border guard|national guard|directorate|direction générale|"
    r"procurement organi[sz]ation|missile defense organi[sz]ation|joint program office|program executive office|"
    r"defence science and technology laboratory|dstl|darpa|diu|sofwerx|spacewerx|afwerx|"
    r"defense forces|defence forces|military|militär|militaire)\b", re.I)
# words that make a public body whatever else the name says ("Israel Missile Defense Organization")
GOV_STRONG = re.compile(r"\b(ministry|ministerium|ministère|ministerio|ministero|ministerstvo|verteidigungsministerium|"
                        r"army|navy|air force|space force|armed forces|department of|defen[cs]e department|"
                        r"defen[cs]e organi[sz]ation|agency|command|directorate|government|joint program office)\b", re.I)
UNIVERSITY = re.compile(r"univerzit|universit|uniwersytet|yliopisto|universitet|universidad|universidade|"
                        r"polytechnic|politecnico|politechnika|hochschule|college|institute of technology", re.I)
# a name that describes a thing rather than an organisation
THING = re.compile(
    r"\b(system|systems integration|missiles?|boosters?|rocket motors?|drive motors?|solar arrays?|arrays|management systems|powder|interceptors?|radar|vehicle|chassis|ammunition|munitions?|round|shell|fuze|"
    r"warhead|launcher|launch tube|canisters?|turret|gun|cannon|howitzer|rifle|engine|engines|turbine|propulsion|"
    r"powertrain|powerpack|transmission|airframe|fuselage|hull|satellites?|payload|sensor|sensors|modules?|"
    r"components?|sub-components|subcomponents|castings|assembl(y|ies)|software|hardware|sdks?|apis?|platforms?|"
    r"equipment|devices?|materials?|parts|kits?|pods?|housings?|structures?|structural|composites?|"
    r"harness|harnesses|generator|batter(y|ies)|cells|chips?|semiconductors?|gpus?|t/r|antenna|network|data|services|services|"
    r"facility|facilities|plant|production line|programme|program|project|initiative|exercise|operation|"
    r"contractors|manufacturers|suppliers|companies|firms|vendors|partners network|unknown|various|other)\b", re.I)
PLACES = {
    'china', 'russia', 'ukraine', 'germany', 'deutschland', 'france', 'italie', 'italy', 'norway', 'sweden', 'poland',
    'spain', 'estados unidos', 'united states', 'usa', 'united kingdom', 'uk', 'japan', 'india', 'israel', 'turkey',
    'türkiye', 'canada', 'australia', 'moldavie', 'moldova', 'honduras', 'iran', 'taiwan', 'korea', 'south korea',
    'north korea', 'republic of korea', 'mexico', 'brasil', 'argentina', 'chile', 'egypt', 'algeria', 'morocco',
    'saudi arabia', 'qatar', 'uae', 'pakistan', 'vietnam', 'philippines', 'thailand', 'malaysia', 'singapore', 'finland', 'denmark', 'netherlands', 'belgium', 'austria', 'switzerland', 'czech republic',
    'romania', 'greece', 'portugal', 'brazil', 'indonesia', 'europe', 'europa', 'africa', 'asia'}
COMPANY_WORDS = re.compile(
    r"\b(industries|industri|technologies|systems|group|holdings?|labs?|laboratories|"
    r"robotics|aerospace|aeronautics|aviation|defen[cs]e|dynamics|electronics|shipyards?|shipbuilding|werft|"
    r"motors?|motoren|automotive|ventures|capital|partners|asset management|investments?|bank|"
    r"semiconductor|microelectronics|optics|photonics|instruments|engineering|manufacturing|works|werke|"
    r"international|corporation|company|enterprises|inc|ltd|llc|plc|gmbh|ag|sa|spa|ab|oy|oyj|asa|nv|bv|se)\b", re.I)
SPLIT = re.compile(r"\s+/\s+|\s*;\s*|\s+and\s+(?=[A-Z])|\s+&\s+(?=[A-Z][a-z]+\s+[A-Z])|\s+und\s+(?=[A-Z])|\s+et\s+(?=[A-Z])")
PAREN = re.compile(r"\s*\(([^)]*)\)")
MAX_WORDS = 7
# ordinary words of the defence and technology vocabulary: a name made only of these and company words
# ("Control Systems", "Rocket Motor", "Additive Manufacturing") is not a company name
GENERIC_TOKENS = set("""
additive advanced aerial aero air airborne aircraft allied applied armour armor armoured armored artificial
autonomous avionics battle chief civil cloud combat command communications composite computing control core
cyber data deep defence defense digital drone drones dynamic electric electrical electronic environmental
european federal fire fluid flight force forces future general global ground heavy high hypersonic industrial
integrated intelligence joint land laser light logistics machine maritime marine mechanical medical micro
military mission mobile modular motor naval navigation bridge industry network new north northern nuclear ocean operations optical power
precision quantum radio rapid research rocket satellite science scientific secure security sensor shield ship
smart solar south southern space special strategic strike submarine supply support surface tactical technical
test training unmanned uncrewed underwater united vehicle vision water west western wireless world
""".split())


def _plain(text):
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(ch for ch in text if not unicodedata.combining(ch)).lower().strip()


def parts(name):
    """[(name, alias)] for every organisation a mention names: 'A / B' -> two parts, 'X (XYZ)' -> X with alias XYZ.
    A parenthesis with a place or a parent ('(Surabaya)', '(Rolls-Royce Power Systems)') is dropped from the name."""
    name = re.sub(r"\s+", " ", (name or '').replace(' ', ' ')).strip()
    out = []
    for piece in SPLIT.split(name):
        piece = piece.strip(' ,.-')
        if not piece:
            continue
        alias = None
        for inner in PAREN.findall(piece):
            inner = inner.strip()
            if re.fullmatch(r"[A-Z0-9][A-Z0-9&.\-]{1,9}", inner):  # an acronym: GA-ASI, RBSL, IMOD
                alias = inner
        base = PAREN.sub('', piece).strip(' ,.-')
        base = re.split(r"\s+[–—-]\s+", base)[0].strip()  # "Wärtsilä Corporation – Helsinki"
        head, _, tail = base.partition(',')
        if tail and not _FORM_WORD.fullmatch(tail.strip(' .')):
            base = head.strip()  # "BAE Systems Bofors AB, Karlskoga, Sweden" -> the name before the place
        if base:
            out.append((base, alias))
    return out


def classify(name):
    """(category, reason) for one organisation name, code rules only."""
    plain = _plain(name)
    words = name.split()
    if not plain or len(plain) < 2:
        return 'not_org', 'empty'
    if plain in PLACES or name_norm(name) in PLACES:
        return 'not_org', 'country or region'
    if UNIVERSITY.search(name):
        return 'university', 'university (deferred category)'
    has_form = bool(_FORM_WORD.search(name))
    if len(words) > MAX_WORDS:
        return 'not_org', f'description ({len(words)} words)'
    if GOV_STRONG.search(name) and not has_form:
        return 'gov', 'public body word'
    if GOV.search(name) and not has_form and not COMPANY_WORDS.search(re.sub(r"(?i)\bdefen[cs]e\b", '', name)) \
            and not THING.search(re.sub(r"(?i)\b(systems?|office|programs?|programmes?)\b", '', name)):
        return 'gov', 'public body word'
    lower = [w for w in words if w[:1].islower() and w.lower() not in ('de', 'di', 'da', 'du', 'des', 'la', 'le',
                                                                       'von', 'van', 'der', 'den', 'and', 'of', 'the',
                                                                       'und', 'et', 'y', 'e', 'spol.', 's', 'r.o.',
                                                                       'a.s.', 'sp.', 'z', 'o.o.')]
    if THING.search(name) and not has_form:
        return 'not_org', 'product or description word'
    if len(lower) >= 2 or (len(words) >= 3 and len(lower) >= 1 and not has_form):
        return 'not_org', 'running text, not a name'
    if re.search(r"\b[A-Z]{1,4}[-/]?\d{1,4}[A-Z]?\b", name) and not has_form and not COMPANY_WORDS.search(name):
        return 'not_org', 'model designation'
    if (re.search(r"\b(?=\w*\d)(?=\w*[A-Za-z])\w{3,}\b", name) or re.search(r"\b[A-Z]{2,}-\d+", name)) and not has_form:
        return 'unsure', 'model designation inside the name'
    if lower and not has_form:
        return 'unsure', 'lower-case word in the name'
    if has_form:
        return 'company', 'legal form'
    if COMPANY_WORDS.search(name):
        own = [w for w in re.findall(r"[\w'’-]+", _plain(name))
               if w not in GENERIC_TOKENS and w not in PLACES and not COMPANY_WORDS.fullmatch(w)]
        if own:
            return 'company', 'company word'
        return 'unsure', 'only generic words and a company word'
    return 'unsure', 'proper name without a company marker'


# words a register or a news item may add to the same company name: "Babcock International" = "Babcock International Group"
SUFFIX_OK = {'group', 'holding', 'holdings', 'corporation', 'corp', 'company', 'co', 'limited',
             'ltd', 'inc', 'plc', 'ag', 'se', 'sa', 'spa', 'ab', 'asa', 'oy', 'oyj', 'gmbh', 'nv', 'bv', 'llc', 'the'}


def key(name):
    """name_norm plus two spellings registers and news differ in: Defence/Defense and a legal form written
    without its last dot ("S.A", "S.p.A") that name_norm leaves as stray letters."""
    k = name_norm(name)
    k = re.sub(r"\bdefence\b", "defense", k)
    k = re.sub(r"(?:\s+\b[a-z]\b)+$", "", k)  # trailing "s a", "s p a"
    return k.strip()


class Index:
    """companies.company names and aliases by name_norm; excluded records are matched but reported as excluded."""

    def __init__(self, companies):
        self.by_key = {}
        self.names = {}
        for c in companies:
            self.names[c['id']] = c['name']
            for n in [c['name']] + list(c.get('aliases') or []):
                k = key(n)
                if k and len(k) >= 3:
                    self.by_key.setdefault(k, set()).add(c['id'])

        self.by_core = {}  # name without the SUFFIX_OK words at its end, for the second step
        for k, ids in self.by_key.items():
            core = self.core(k)
            if core:
                self.by_core.setdefault(core, set()).update(ids)

    @staticmethod
    def core(key):
        words = key.split()
        while words and words[-1] in SUFFIX_OK:
            words.pop()
        core = ' '.join(words)
        # one word is too weak a key: "Honeywell" is not "Honeywell International s.r.o." (a Czech subsidiary)
        return core if len(words) >= 2 else None

    def match(self, name, alias=None):
        ids = set()
        for n in filter(None, [name, alias]):
            ids |= self.by_key.get(key(n), set())
        if not ids:  # second step: the same name up to Group / Holdings / Ltd ...
            core = self.core(key(name))
            if core:
                ids = set(self.by_core.get(core, set()))
                if len(ids) == 1:
                    return 'linked_core', sorted(ids)
        if len(ids) == 1:
            return 'linked', sorted(ids)
        if ids:
            return 'ambiguous', sorted(ids)
        return 'new', []


def plan(mentions, companies):
    """One decision per (mention, part). Mentions: knowledge.node rows {id, kind, name, aliases, items, quote, what}."""
    index = Index(companies)
    out = []
    for m in mentions:
        for name, alias in parts(m['name']):
            category, reason = classify(name)
            if m.get('kind') == 'org' and category in ('company', 'unsure') and not _FORM_WORD.search(name):
                # the extractor already said "organisation, not company": a company word alone is not enough
                category, reason = ('gov', 'extractor kind org') if GOV.search(name) else ('unsure', 'extractor kind org')
            match, ids = ('', [])
            if category in ('company', 'unsure'):
                match, ids = index.match(name, alias)
                if match in ('linked', 'linked_core') and category == 'unsure':
                    category, reason = 'company', 'known company name'
            out.append({'node_id': m['id'], 'kind': m.get('kind'), 'mention': m['name'], 'name': name, 'alias': alias,
                        'category': category, 'reason': reason, 'match': match, 'company_ids': ids,
                        'items': m.get('items') or 0})
    return out


def summary(decisions):
    s = {'mentions': len({d['node_id'] for d in decisions}), 'parts': len(decisions), 'by_category': {}, 'company': {}}
    for d in decisions:
        s['by_category'][d['category']] = s['by_category'].get(d['category'], 0) + 1
        if d['category'] == 'company':
            s['company'][d['match']] = s['company'].get(d['match'], 0) + 1
    new = {name_norm(d['name']) for d in decisions if d['category'] == 'company' and d['match'] == 'new'}
    s['new_companies_distinct'] = len(new)
    return s


# --- model step for the names the rules leave 'unsure' --------------------------------------------------------------
MODEL_CATEGORIES = {'company', 'gov', 'university', 'not_org', 'person'}
BATCH = 40
PROMPT = """You sort names taken from defence and technology news. For each numbered name decide what it is:
company     - a business: manufacturer, shipyard, start-up, investor or fund, bank, consultancy (also a company division)
gov         - a public body: ministry, armed force or unit, agency, government, international organisation
university  - a university, college or academic research institute
not_org     - a product, system, programme, event, country, region or a generic phrase
person      - a person's name
Use only the name and the short context. Answer with one JSON array and nothing else:
[{"i": <number>, "c": "<category>", "country": "<ISO-2 code of the company's home country or empty>"}]

"""


def prompt(batch):
    lines = [f'{n}. {d["name"]} — context: {(d.get("context") or "")[:160]}' for n, d in enumerate(batch, 1)]
    return PROMPT + '\n'.join(lines)


def parse(text, size):
    """{index: (category, country)} from the model answer; anything unreadable is left out (stays 'unsure')."""
    m = re.search(r'\[.*\]', text or '', re.S)
    if not m:
        return {}
    try:
        rows = json.loads(m.group())
    except ValueError:
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else []:
        try:
            i, c = int(r.get('i')), str(r.get('c', '')).strip().lower()
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= i <= size and c in MODEL_CATEGORIES:
            country = str(r.get('country') or '').strip().upper()
            out[i] = (c, country if re.fullmatch(r'[A-Z]{2}', country) else '')
    return out


def classify_unsure(decisions, ask, contexts=None, limit=None, log=print):
    """Ask the model about the 'unsure' parts in batches. ask(prompt) -> text. Changes the decisions in place:
    category from the model, reason 'model', and for companies the match is kept from plan()."""
    contexts = contexts or {}
    todo = [d for d in decisions if d['category'] == 'unsure']
    if limit is not None:
        todo = todo[:limit]
    stats = {'asked': 0, 'answered': 0, 'errors': 0}
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        for d in batch:
            d['context'] = contexts.get(d['node_id'], '')
        stats['asked'] += len(batch)
        try:
            got = parse(ask(prompt(batch)), len(batch))
        except Exception as e:  # noqa: BLE001 — one failed batch must not stop the night
            stats['errors'] += 1
            log(f'model batch {start // BATCH + 1}: {type(e).__name__}')
            continue
        for n, d in enumerate(batch, 1):
            if n in got:
                d['category'], d['model_country'] = got[n]
                d['reason'] = 'model'
                if d['category'] == 'person':
                    d['category'] = 'not_org'  # people are never added (privacy rule of the companies base)
                    d['reason'] = 'model: person'
                stats['answered'] += 1
            d.pop('context', None)
    return stats


MENTIONS_SQL = """select n.id, n.kind, n.name, n.aliases,
  (select count(*) from knowledge.edge e where (e.src_node_id = n.id or e.dst_node_id = n.id) and e.item_id is not null) as items,
  (select left(coalesce(e.quote, e.what), 200) from knowledge.edge e
    where e.src_node_id = n.id or e.dst_node_id = n.id order by e.id limit 1) as context
from knowledge.node n where n.kind in ('company', 'org') order by n.id"""
COMPANIES_SQL = """select c.id, c.name, coalesce(array_agg(a.alias) filter (where a.alias is not null), '{}') as aliases
from companies.company c left join companies.company_alias a on a.company_id = c.id group by c.id, c.name"""
REVIEW_SQL = """insert into companies.discovery_review
  (run, node_id, mention, name, alias, category, reason, model_country, match, company_ids)
values (%(run)s, %(node_id)s, %(mention)s, %(name)s, %(alias)s, %(category)s, %(reason)s, %(model_country)s,
  %(match)s, %(company_ids)s)"""


# --- writing (owner 23.11.2026 «Да, делай»): new candidate companies and the mention -> company links ---------------
from .normalize import EXCLUDED_COUNTRIES  # noqa: E402
from .store import new_company_id  # noqa: E402

NOT_A_BUSINESS = re.compile(r"\b(foundation|stiftung|fondation|fundaci[oó]n|fondazione|association|verband|council|"
                            r"institute|institut|istituto|society|club|centre|center|zentrum|academy|akademie|"
                            r"school|hospital|church|party|union)\b", re.I)
INVESTOR = re.compile(r"\b(ventures?|capital|partners|asset management|investments?|fund|vc)\b", re.I)
LINKED_SQL = "select node_id from companies.mention_link"
INSERT_COMPANY_SQL = """insert into companies.company (id, name, name_norm, country, status, excluded_reason, flags,
  created_at, updated_at)
values (%(id)s, %(name)s, %(name_norm)s, %(country)s, %(status)s, %(excluded_reason)s, %(flags)s::jsonb, now(), now())"""
INSERT_LINK_SQL = """insert into companies.mention_link (node_id, company_id, part, how, run)
values (%(node_id)s, %(company_id)s, %(part)s, %(how)s, %(run)s) on conflict do nothing"""
REVIEW_ROWS_SQL = """select node_id, mention, name, alias, category, reason, model_country, match, company_ids
from companies.discovery_review where run = %(run)s"""


def clean(name):
    """Stray brackets and quotes the splitter leaves: 'KNDS Deutschland)' -> 'KNDS Deutschland',
    'Naval Group (ADMP' -> 'Naval Group'; a balanced '(Leonardo)' stays."""
    name = re.sub(r"\s+", ' ', name or '').strip()
    name = re.sub(r"^[\s\"'\[]+|[\s\"'\]]+$", '', name)
    while name.endswith(')') and name.count(')') > name.count('('):
        name = name[:-1].rstrip()
    if name.count('(') > name.count(')'):
        name = name[:name.rfind('(')].rstrip()
    return name.strip(' ,-')


def write_plan(decisions, linked_nodes=()):
    """(links, new_companies). links: [(node_id, company_id | key, part, how)]; new companies are keyed by key()
    and created once however many mentions name them. Public bodies, universities, people, non-businesses
    (foundations, institutes) and the 'unsure' are left out."""
    links, new = [], {}
    for d in decisions:
        if d['category'] != 'company' or d['node_id'] in linked_nodes:
            continue
        name = clean(d['name'])
        if d['match'] in ('linked', 'linked_core'):
            for cid in d['company_ids']:
                links.append((d['node_id'], cid, name, d['match']))
        elif d['match'] == 'new':
            if NOT_A_BUSINESS.search(name) or len(name_norm(name)) < 3:
                continue
            k = key(name)
            c = new.setdefault(k, {'name': name, 'countries': set(), 'investor': bool(INVESTOR.search(name))})
            if d.get('model_country'):
                c['countries'].add(d['model_country'])
            links.append((d['node_id'], k, name, 'new'))
    return links, new


def write(conn, decisions, run, companies, log=print):
    """Create the new candidate companies and the links in one transaction. companies: fresh rows of the base
    (id, name, aliases) — a name that appeared in the base meanwhile is linked, not created again."""
    linked_nodes = {r['node_id'] for r in conn.execute(LINKED_SQL).fetchall()}
    links, new = write_plan(decisions, linked_nodes)
    index = Index(companies)
    ids, created, excluded = {}, 0, 0
    with conn.transaction():
        for k, c in new.items():
            match, found = index.match(c['name'])
            if match in ('linked', 'linked_core'):
                ids[k] = (found[0], match)
                continue
            country = next(iter(c['countries'])) if len(c['countries']) == 1 else None
            status, reason = ('excluded', f'legal_country_{country}') if country in EXCLUDED_COUNTRIES else ('candidate', None)
            excluded += status == 'excluded'
            cid = new_company_id()
            flags = ['from_news'] + (['investor'] if c['investor'] else [])
            conn.execute(INSERT_COMPANY_SQL, {'id': cid, 'name': c['name'], 'name_norm': name_norm(c['name']),
                                              'country': country, 'status': status, 'excluded_reason': reason,
                                              'flags': json.dumps(flags)})
            ids[k] = (cid, 'new')
            created += 1
        n_links = 0
        for node_id, target, part, how in links:
            cid, how2 = ids.get(target, (target, how)) if how == 'new' else (target, how)
            conn.execute(INSERT_LINK_SQL, {'node_id': node_id, 'company_id': cid, 'part': part, 'how': how2, 'run': run})
            n_links += 1
    out = {'created': created, 'created_excluded_country': excluded, 'links': n_links,
           'new_names': len(new), 'already_in_base': len(new) - created}
    log(json.dumps(out))
    return out

# nightly: only the mentions not settled yet (no link, no final decision; 'unsure' ones are asked again)
NEW_MENTIONS_SQL = MENTIONS_SQL.replace(
    "where n.kind in ('company', 'org')",
    "where n.kind in ('company', 'org')\n  and not exists (select 1 from companies.mention_link l where l.node_id = n.id)"
    "\n  and not exists (select 1 from companies.discovery_review r where r.node_id = n.id and r.category <> 'unsure')")
