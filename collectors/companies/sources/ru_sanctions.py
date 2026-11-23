"""Russian defence-industry enterprises from public sanctions lists — companies and cities only (owner 23.11.2026
«Нам нужны только компании, и города»; EXP-033 DESIGN_RU_DEFENCE_ENTERPRISES_v0.md).

Sources (public, no key):
- US OFAC SDN list: SDN.CSV (name, type, programs, remarks with INN / OGRN / organization type) and ADD.CSV
  (addresses; only the city and the country are read);
- UK Sanctions List (FCDO): UK-Sanctions-List.xml, Russia regime, entities (names, Cyrillic name, registration
  numbers, the city of the address, business sector, statement of reasons).

Never read or stored: people (individual designations, names of directors, owners or managers), phone numbers,
e-mails, street addresses, postal codes. A record is kept when its address is in Russia and the text of the list
says it works for the military: weapons, ammunition, military electronics, aircraft, ships, missiles, drones, the
armed forces or the defence ministry.
"""
import csv
import io
import re
import xml.etree.ElementTree as ET

from ..normalize import name_norm

OFAC_SDN = 'https://sanctionslistservice.ofac.treas.gov/api/download/SDN.CSV'
OFAC_ADD = 'https://sanctionslistservice.ofac.treas.gov/api/download/ADD.CSV'
UK_XML = 'https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml'
OFAC_PAGE = 'https://sanctionssearch.ofac.treas.gov/Details.aspx?id={id}'
UK_PAGE = 'https://www.gov.uk/government/publications/the-uk-sanctions-list'

DEFENCE = re.compile(
    r"weapon|armament|ammunition|munition|military|defen[cs]e (industr|sector|enterprise|product|ministry)|"
    r"ministry of defen|armed forces|missile|rocket|artillery|tank|armou?red|drone|unmanned|uav|"
    r"radar|electronic warfare|guidance|warship|naval|shipbuild|submarine|aircraft|helicopter|aviation|"
    r"aerospace|explosive|propellant|gunpowder|small arms|firearm|rostec|almaz|kalashnikov|tactical missiles|"
    r"manufacture of weapons|military[- ]industrial|opk|оборон|вооруж|военн", re.I)
INN = re.compile(r"(?:Tax ID No\.|INN)\s*[:.]?\s*(\d{10}|\d{12})\b", re.I)
OGRN = re.compile(r"(?:Government Gazette Number|OGRN|Registration (?:ID|Number))\s*[:.]?\s*(\d{13}|\d{15})\b", re.I)
ORG_TYPE = re.compile(r"Organization Type:\s*([^;]+)", re.I)
SECTOR = re.compile(r"Business Sector:\s*([A-Za-z ,&/-]{3,80}?)(?:\s+The |\s*\||$)")


def _clean(v):
    v = (v or '').strip()
    return '' if v == '-0-' else v


def ofac(sdn_text, add_text):
    """Russian-address entities of the SDN list."""
    cities = {}
    for row in csv.reader(io.StringIO(add_text)):
        if len(row) >= 5 and _clean(row[4]).lower() == 'russia':
            city = _clean(row[3]).split(',')[0].strip()
            city = re.sub(r"\s*\d{5,6}\s*", ' ', city).strip()  # postal codes out
            if city:
                cities.setdefault(row[0], city)
    out = []
    for row in csv.reader(io.StringIO(sdn_text)):
        if len(row) < 12 or _clean(row[2]) or row[0] not in cities:
            continue  # individuals, vessels and aircraft have a type; entities have -0-
        remarks = row[11] or ''
        org = ORG_TYPE.search(remarks)
        text = ' '.join([row[1], org.group(1) if org else '', remarks])
        out.append({'source': 'OFAC', 'source_id': row[0], 'name': _clean(row[1]), 'name_ru': None,
                    'inn': (INN.search(remarks) or [None, None])[1], 'ogrn': (OGRN.search(remarks) or [None, None])[1],
                    'city': cities[row[0]], 'sector': org.group(1).strip() if org else None,
                    'defence': bool(DEFENCE.search(text)), 'url': OFAC_PAGE.format(id=row[0])})
    return out


def uk(xml_text):
    """Russia-regime entities of the UK list with an address in Russia."""
    root = ET.fromstring(xml_text)
    out = []
    for d in root.findall('Designation'):
        if 'Russia' not in (d.findtext('RegimeName') or '') or d.findtext('IndividualEntityShip') != 'Entity':
            continue
        city = None
        for a in d.iter('Address'):
            if (a.findtext('AddressCountry') or '').strip().lower() == 'russia':
                city = next((a.findtext(t).strip() for t in ('AddressLine6', 'AddressLine5', 'AddressLine4')
                             if (a.findtext(t) or '').strip()), None)
                break
        if not city:
            continue
        names = [n.findtext('Name6') for n in d.iter('Name') if (n.findtext('NameType') or '').lower().startswith('primary')]
        ru = next((n.findtext('NameNonLatinScript') for n in d.iter('NonLatinName')), None)
        regs = [r.text.strip() for r in d.iter('BusinessRegistrationNumber') if r.text and r.text.strip()]
        info = d.findtext('OtherInformation') or ''
        sector = SECTOR.search(info)
        reasons = d.findtext('UKStatementofReasons') or ''
        text = ' '.join([names[0] if names else '', ru or '', sector.group(1) if sector else '', reasons])
        digits = [re.sub(r'\D', '', r) for r in regs]
        out.append({'source': 'UK', 'source_id': d.findtext('UniqueID'), 'name': (names or [None])[0], 'name_ru': ru,
                    'inn': next((x for x in digits if len(x) in (10, 12)), None),
                    'ogrn': next((x for x in digits if len(x) in (13, 15)), None),
                    'city': city, 'sector': sector.group(1).strip() if sector else None,
                    'defence': bool(DEFENCE.search(text)), 'url': UK_PAGE})
    return out


def merge(records):
    """One enterprise per INN (or OGRN, or name), sources kept."""
    by = {}
    for r in records:
        key = ('inn', r['inn']) if r['inn'] else ('ogrn', r['ogrn']) if r['ogrn'] else ('name', name_norm(r['name'] or ''))
        e = by.setdefault(key, {'name': r['name'], 'name_ru': r['name_ru'], 'inn': r['inn'], 'ogrn': r['ogrn'],
                                'city': r['city'], 'sector': r['sector'], 'defence': False, 'sources': []})
        for k in ('name_ru', 'inn', 'ogrn', 'city', 'sector'):
            e[k] = e[k] or r[k]
        e['defence'] = e['defence'] or r['defence']
        e['sources'].append({'source': r['source'], 'id': r['source_id'], 'url': r['url']})
    return list(by.values())


# --- cities, the model for industrial names, the base -----------------------------------------------------------
CITY_ALIASES = {'st petersburg': 'Saint Petersburg', 'st. petersburg': 'Saint Petersburg', 'saint-petersburg': 'Saint Petersburg',
                'sankt-peterburg': 'Saint Petersburg', 'sankt peterburg': 'Saint Petersburg', 'moskva': 'Moscow',
                'g. moscow': 'Moscow', 'moscow city': 'Moscow', 'nizhniy novgorod': 'Nizhny Novgorod',
                'yekaterinburg': 'Yekaterinburg', 'ekaterinburg': 'Yekaterinburg', 'rostov-on-don': 'Rostov-on-Don',
                'rostov on don': 'Rostov-on-Don', 'korolyov': 'Korolev'}
INDUSTRIAL = re.compile(
    r"design bureau|konstruktorsk|byuro|scientific[- ]production|nauchno|proizvodstven|\bNPO\b|\bNPP\b|\bNPK\b|"
    r"\bOKB\b|\bKB\b|research institute|issledovatel|instrument|priborostro|machine[- ]building|mashinostro|zavod|"
    r"\bplant\b|factory|shipyard|sudostroi|optic|elektron|electronic|radio|precision|mekhani|engineering|tekhnolog|"
    r"technolog|metall|chemical|khimi|aviat|avia", re.I)


def city(name):
    c = re.sub(r"^(g\.|gorod|city of)\s+", '', (name or '').strip(), flags=re.I).strip(' ,.')
    c = re.sub(r"\s+", ' ', c)
    return CITY_ALIASES.get(c.lower(), c.title() if c.isupper() else c) or None


PROMPT = """For each numbered Russian organisation decide whether it is part of Russia's defence industry: it makes or
repairs weapons, ammunition, military vehicles, ships, aircraft, missiles, drones, military electronics or their key
components, or does military research and design, or supplies the Russian armed forces. Banks, oil, retail, media,
software for civilian use and transport companies are not, unless the name says military. Answer with one JSON
array and nothing else: [{"i": <number>, "d": "yes" | "no" | "unclear"}]

"""


def ask_industrial(records, ask, batch=40):
    """{index in records: 'yes'|'no'|'unclear'} for the industrial-by-name records; failed batches are left out."""
    import json as _json
    out = {}
    for s in range(0, len(records), batch):
        part = records[s:s + batch]
        text = PROMPT + '\n'.join(f"{n}. {r['name']}" + (f" / {r['name_ru']}" if r.get('name_ru') else '') +
                                  (f" — {r['sector']}" if r.get('sector') else '') + f" ({r.get('city') or ''})"
                                  for n, r in enumerate(part, 1))
        try:
            m = re.search(r'\[.*\]', ask(text) or '', re.S)
            rows = _json.loads(m.group()) if m else []
        except Exception:  # noqa: BLE001
            continue
        for r in rows if isinstance(rows, list) else []:
            try:
                i, d = int(r.get('i')), str(r.get('d', '')).lower()
            except (TypeError, ValueError, AttributeError):
                continue
            if 1 <= i <= len(part) and d in ('yes', 'no', 'unclear'):
                out[s + i - 1] = d
    return out


def key(e):
    return f"inn:{e['inn']}" if e.get('inn') else f"ogrn:{e['ogrn']}" if e.get('ogrn') else f"name:{name_norm(e['name'] or '')}"


UPSERT_SQL = """insert into companies.ru_enterprise (key, name, name_ru, inn, ogrn, city, sector, defence_basis, model_answer, sources)
values (%(key)s, %(name)s, %(name_ru)s, %(inn)s, %(ogrn)s, %(city)s, %(sector)s, %(basis)s, %(answer)s, %(sources)s::jsonb)
on conflict (key) do update set name = excluded.name, name_ru = coalesce(excluded.name_ru, companies.ru_enterprise.name_ru),
  city = coalesce(excluded.city, companies.ru_enterprise.city), sector = coalesce(excluded.sector, companies.ru_enterprise.sector),
  defence_basis = excluded.defence_basis, model_answer = excluded.model_answer, sources = excluded.sources, updated_at = now()"""
