"""The network and the products of a company card from the knowledge graph (EXP-035, owner 23.11.2026: «граф был бы
интересен, если б там были другие компании-подрядчики, доноры, инвесторы, прочие связи»).

The daily synthesis chain extracts relations from the news with an exact quote and checks the quote
(knowledge.edge). A company is tied to its knowledge nodes by companies.mention_link. Here only relations with a
checked quote are taken, and for the network only those whose other end is a company or an organisation; the other
ends that are products or programmes become the product list. A relation stays "verified" or "candidate" as the
chain marked it; the page draws candidates dashed.

Types on the page: customer, supplier, funder, investor, funded (the company gives money), owner, subsidiary,
competitor. EDF partners and group entities come from the card's own EDF claims on the site side.
"""
import re

from .normalize import name_norm

RELS = ('orders', 'supplies', 'funds', 'part_of', 'competes', 'produces')
PARTY = ('company', 'org')
THING = ('product', 'programme')
MAX_NODES = 15
MAX_PRODUCTS = 12

EDGES_SQL = """with me as (select l.company_id, l.node_id from companies.mention_link l where l.company_id = any(%(ids)s))
select me.company_id, e.rel, (e.src_node_id = me.node_id) as outgoing, e.status, e.occurred_on::text as occurred_on,
  e.amount::float as amount, e.currency, e.quote, e.source_url, e.created_at, o.kind, o.name,
  oc.name as other_company, oc.country as other_country
from me join knowledge.edge e on me.node_id in (e.src_node_id, e.dst_node_id)
join knowledge.node o on o.id = case when e.src_node_id = me.node_id then e.dst_node_id else e.src_node_id end
left join lateral (select c.name, c.country from companies.mention_link l2 join companies.company c on c.id = l2.company_id
  where l2.node_id = o.id and c.excluded_reason is null limit 1) oc on true
where e.superseded_by is null and e.quote_verified and e.rel = any(%(rels)s) and e.src_node_id <> e.dst_node_id
  and e.status in ('verified', 'candidate')"""
CHANGED_SQL = """select distinct l.company_id from companies.mention_link l join knowledge.edge e
  on l.node_id in (e.src_node_id, e.dst_node_id)
where e.created_at >= %(since)s and e.quote_verified and e.superseded_by is null and e.rel = any(%(rels)s)"""


def kind_of(rel, outgoing, other_kind):
    """The type of the other end for the network, or None."""
    if other_kind not in PARTY:
        return None
    if rel == 'orders':
        return 'supplier' if outgoing else 'customer'  # the company orders from it / it orders from the company
    if rel == 'supplies':
        return 'customer' if outgoing else 'supplier'
    if rel == 'funds':
        return 'funded' if outgoing else ('investor' if other_kind == 'company' else 'funder')
    if rel == 'part_of':
        return 'owner' if outgoing else 'subsidiary'
    if rel == 'competes':
        return 'competitor'
    return None


def key(name):
    """One node per organisation: "French Armaments General Directorate (DGA)" and "French Directorate General of
    Armaments (DGA)" are the same; otherwise the normalised name."""
    m = re.search(r'\(([A-Z][A-Za-z]{1,9})\)\s*$', name or '')
    return ('abbr', m.group(1).upper()) if m else ('name', name_norm(name or ''))


def _evidence(r):
    return {'quote': (r.get('quote') or '')[:300], 'url': r.get('source_url'), 'date': r.get('occurred_on'),
            'status': r.get('status')}


def build(rows, own_names=()):
    """(network, products) for one company from its edge rows. Nodes: newest evidence first, verified wins, at most
    MAX_NODES; the company itself and its own other names are dropped."""
    own = {key(n) for n in own_names if n}
    nodes, products = {}, {}
    for r in sorted(rows, key=lambda r: (r.get('occurred_on') or '', str(r.get('created_at') or '')), reverse=True):
        if key(r['name']) in own or key(r.get('other_company') or '') in own:
            continue
        if r['rel'] == 'produces' or (r['rel'] == 'supplies' and r['outgoing'] and r['kind'] in THING):
            if r['kind'] not in THING:
                continue
            k = key(r['name'])
            p = products.setdefault(k, {'name': r['name'], 'kind': r['kind'],
                                        'role': 'makes' if r['rel'] == 'produces' else 'supplies to',
                                        'status': r['status'], 'evidence': []})
            if r['rel'] == 'produces':
                p['role'] = 'makes'
            if r['status'] == 'verified':
                p['status'] = 'verified'
            if len(p['evidence']) < 2 and r.get('source_url') not in {e['url'] for e in p['evidence']}:
                p['evidence'].append(_evidence(r))
            continue
        t = kind_of(r['rel'], r['outgoing'], r['kind'])
        if not t:
            continue
        k = (t, key(r.get('other_company') or r['name']))
        n = nodes.setdefault(k, {'type': t, 'name': r.get('other_company') or r['name'], 'kind': r['kind'],
                                 'country': r.get('other_country'), 'in_catalogue': bool(r.get('other_company')),
                                 'status': r['status'], 'count': 0, 'amount': None, 'currency': None,
                                 'date': r.get('occurred_on'), 'evidence': []})
        n['count'] += 1
        if r['status'] == 'verified':
            n['status'] = 'verified'
        if r.get('amount') and not n['amount']:
            n['amount'], n['currency'] = r['amount'], r.get('currency')
        if len(n['evidence']) < 2 and r.get('source_url') not in {e['url'] for e in n['evidence']}:
            n['evidence'].append(_evidence(r))
    rank = lambda n: (n['status'] == 'verified', n['count'], n['date'] or '')  # noqa: E731
    net = sorted(nodes.values(), key=rank, reverse=True)[:MAX_NODES]
    prods = sorted(products.values(), key=lambda p: (p['role'] == 'makes', p['status'] == 'verified'), reverse=True)
    return net, prods[:MAX_PRODUCTS]


def attach(conn, cards):
    """Add "network" and "products" to each card (dicts with company_id, name)."""
    if not cards:
        return cards
    rows = conn.execute(EDGES_SQL, {'ids': [c['company_id'] for c in cards], 'rels': list(RELS)}).fetchall()
    by = {}
    for r in rows:
        by.setdefault(r['company_id'], []).append(r)
    for c in cards:
        names = [c['name']] + [cl['value'] for cl in c.get('claims') or [] if cl.get('ref') == 'b4:legal_name'
                               and isinstance(cl.get('value'), str)]
        c['network'], c['products'] = build(by.get(c['company_id'], []), names)
    return cards
