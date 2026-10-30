"""Prompts and JSON schemas for the three model steps of the pilot (EXP-028 DESIGN §8 v1.1)."""

COMMITMENTS = ["plan", "framework", "approval", "ceiling", "contract", "funded", "delivered", "site_selection",
               "other"]

GROUP_SYSTEM = """You prepare defence-industry news for analysis. You receive numbered news items from two days.
1. Group items that report the same real-world event (same programme, same parties, same decision), even if they
   come from different outlets or languages. Every item number appears in exactly one group.
2. For each group give search keys: the programme or product name, the main organisations, 3-6 distinctive
   search terms (English, no generic words like "defence" or "contract"), the countries involved (ISO 3166
   alpha-2 codes: buyer, supplier, host) and the place of the event (city or country with latitude and
   longitude) for a map.
3. Programme naming: known_programmes lists programmes we already keep a dossier for. If a group belongs to one of
   them, give its name exactly as listed. Otherwise name the programme the way its owner calls it (e.g. "PAC-3 MSE",
   "Galileo Second Generation"), not the headline.
4. topic: the closest slug from topics, or "other".
Use only the given text; do not add facts."""

GROUP_SCHEMA = {
    "type": "object", "required": ["groups"],
    "properties": {"groups": {"type": "array", "items": {
        "type": "object", "required": ["items", "programme", "organisations", "terms", "countries", "topic", "place"],
        "properties": {
            "items": {"type": "array", "items": {"type": "integer"}},
            "programme": {"type": "string"},
            "organisations": {"type": "array", "items": {"type": "string"}},
            "terms": {"type": "array", "items": {"type": "string"}},
            "countries": {"type": "array", "items": {"type": "string"}},
            "topic": {"type": "string"},
            "place": {"type": "object", "required": ["label", "lat", "lon"], "properties": {
                "label": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}}}}}}}}

RESEARCH_SYSTEM = """You are the research desk of a defence-industry analysis service (BD, procurement, supply chain
readers). For one news event you build the background an analyst needs, as in this standard: an event is placed in
its programme, its topic chronology with the status of money, the policy behind it, and the supply chain under it.

Run several Google Search queries (the event, the programme history, the supply chain); do not answer from
memory. Prefer primary sources: governments, parliaments, the EU, NATO, procurement and export notices
(DSCA, TED, SAM.gov), company press releases. Use media only when no primary source exists.
You also receive items from our own news archive: use them where relevant and cite them by archive id.

Cover:
- primary_source: the original announcement of THIS event (not a re-report).
- parent: the programme or larger effort this event belongs to, one sentence.
- timeline: 6-15 dated earlier steps of the same programme/topic, oldest first, over the years that matter
  (go back as far as the programme does). Each step: date (YYYY-MM-DD, use -01 for unknown day/month and set
  precision), what happened in one sentence, commitment status of money (plan, framework, approval, ceiling,
  contract, funded, delivered, site_selection, other), amount and ISO currency when stated, and the exact URL of
  the page that states it (or archive_id). No step without a source.
- supply_chain: 1-6 links down from the product: component, material or raw material it depends on, bottleneck
  or sole source if known, each with a source URL.
- restrictions: export bans, licensing, sanctions or suspensions that touch that supply chain, with dates and
  status, each with a source URL. Empty if none apply.
- documents: official documents (laws, strategies, budgets, notices, treaties) relevant to this event, with URL.
Write in English, as a research note with short dated lines under these headings: PRIMARY SOURCE, PARENT,
TIMELINE, SUPPLY CHAIN, RESTRICTIONS, DOCUMENTS. Every line must come from a page you found with Google Search
or from our archive (then write "archive <id>"). Never invent dates or amounts; if unsure, leave the line out."""

STRUCTURE_SYSTEM = """You turn a research note into JSON. Use only lines of the note. The note marks the web sources
of a line as [n]; source n is in the numbered source list. For source_url / url use exactly the uri of the cited
source n (the first one if several). A line marked "archive <id>" gets archive_id = id and no source_url.
Drop any line that has neither a source mark nor an archive id."""

_SOURCE = {"type": "object", "required": ["url", "title"], "properties": {
    "url": {"type": "string"}, "title": {"type": "string"}, "publisher": {"type": "string"},
    "date": {"type": "string"}}}

RESEARCH_SCHEMA = {
    "type": "object", "required": ["primary_source", "parent", "timeline", "supply_chain", "restrictions", "documents"],
    "properties": {
        "primary_source": _SOURCE,
        "parent": {"type": "object", "required": ["name", "summary"], "properties": {
            "name": {"type": "string"}, "summary": {"type": "string"}}},
        "timeline": {"type": "array", "items": {
            "type": "object", "required": ["date", "precision", "what", "commitment"],
            "properties": {"date": {"type": "string"}, "precision": {"type": "string", "enum": ["day", "month", "year"]},
                           "what": {"type": "string"}, "commitment": {"type": "string", "enum": COMMITMENTS},
                           "amount": {"type": "number"}, "currency": {"type": "string"},
                           "source_url": {"type": "string"}, "archive_id": {"type": "integer"}}}},
        "supply_chain": {"type": "array", "items": {
            "type": "object", "required": ["upper", "lower", "level", "note", "source_url"],
            "properties": {"upper": {"type": "string"}, "lower": {"type": "string"},
                           "level": {"type": "string", "enum": ["component", "material", "raw_material", "technology"]},
                           "note": {"type": "string"}, "source_url": {"type": "string"}}}},
        "restrictions": {"type": "array", "items": {
            "type": "object", "required": ["issuer", "subject", "kind", "status", "source_url"],
            "properties": {"issuer": {"type": "string"}, "subject": {"type": "string"},
                           "kind": {"type": "string", "enum": ["export_ban", "export_licensing", "sanction", "tariff",
                                                               "suspension"]},
                           "starts_on": {"type": "string"}, "ends_on": {"type": "string"},
                           "status": {"type": "string", "enum": ["in_force", "suspended", "lifted"]},
                           "source_url": {"type": "string"}}}},
        "documents": {"type": "array", "items": {
            "type": "object", "required": ["title", "url", "doc_type", "issuer"],
            "properties": {"title": {"type": "string"}, "url": {"type": "string"}, "doc_type": {"type": "string"},
                           "issuer": {"type": "string"}, "date": {"type": "string"}}}}}}

WRITER_SYSTEM = """You write one analytical brief for defence-industry professionals (business development, procurement,
supply chain). English, plain and precise, no hype. Use ONLY the numbered facts in the packet; every sentence ends
with the ids of the facts it relies on. Do not add numbers, dates or names that are not in the cited facts.

Sections (each 1-4 sentences; leave a section empty only if no fact supports it):
- headline: at most 90 characters, says what changed and why it matters.
- lead: two short sentences for the feed card (at most 230 characters together).
- what_happened: the event itself, from the news and the primary source.
- context: the programme and earlier steps (the chronology facts, with dates and money status).
- policy_direction: the policy or doctrine behind it (doctrine facts).
- bigger_picture: one helicopter-view sentence on where the whole topic is moving, built from several
  chronology facts, ending with a short signal.
- supply_chain: what limits output down the chain and dated restrictions.
- why_it_matters: who in industry should care and what they can do.
Coverage rules: if the packet has any S or R fact, supply_chain must use them; if it has any D fact, policy_direction
must use it; if it has three or more dated T facts, context must walk through them with dates and money status.
Also list the companies named in your text with their role."""

_SENTENCES = {"type": "array", "items": {"type": "object", "required": ["text", "refs"], "properties": {
    "text": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}}}}}

WRITER_SCHEMA = {
    "type": "object",
    "required": ["headline", "lead", "what_happened", "context", "policy_direction", "bigger_picture",
                 "supply_chain", "why_it_matters", "companies"],
    "properties": {"headline": {"type": "string"}, "lead": {"type": "string"},
                   "what_happened": _SENTENCES, "context": _SENTENCES, "policy_direction": _SENTENCES,
                   "bigger_picture": _SENTENCES, "supply_chain": _SENTENCES, "why_it_matters": _SENTENCES,
                   "companies": {"type": "array", "items": {"type": "object", "required": ["name", "role"],
                                 "properties": {"name": {"type": "string"}, "role": {"type": "string"}}}}}}

SECTIONS = ["what_happened", "context", "policy_direction", "bigger_picture", "supply_chain", "why_it_matters"]


SHORT_WRITER_SYSTEM = """You write the short parts of an analytical brief for defence-industry professionals (BD,
procurement, supply chain). The chronology, supply chain and policy sections are already written from our verified
database (composed). English, plain and precise, no hype. Use ONLY the numbered facts; every sentence ends with the
ids of the facts it relies on; no number, date or name that is not in the cited facts.

- headline: at most 90 characters, what changed and why it matters.
- lead: two short sentences for the feed card (at most 230 characters together).
- what_happened: 1-2 sentences on the event itself (news N and primary source P facts).
- bigger_picture: one helicopter-view sentence on where the whole topic is moving, built from several dated T facts,
  ending with a short signal.
- why_it_matters: one sentence on who in industry should care and what they can do.
Also list the companies named in your text with their role."""

SHORT_WRITER_SCHEMA = {
    "type": "object", "required": ["headline", "lead", "what_happened", "bigger_picture", "why_it_matters", "companies"],
    "properties": {"headline": {"type": "string"}, "lead": {"type": "string"},
                   "what_happened": _SENTENCES, "bigger_picture": _SENTENCES, "why_it_matters": _SENTENCES,
                   "companies": {"type": "array", "items": {"type": "object", "required": ["name", "role"],
                                 "properties": {"name": {"type": "string"}, "role": {"type": "string"}}}}}}

NODE_KINDS = ["programme", "product", "company", "org", "technology", "material", "regulation", "procurement", "place"]
RELS = ["milestone", "supplies", "orders", "funds", "produces", "depends_on", "restricts", "regulates", "part_of",
        "competes", "exports_to", "price"]

EXTRACT_SYSTEM = """You read one defence-industry news item and write down what it states about the market, as a
small graph. Use only this text; never add background knowledge.

entities: every programme, product (weapon, platform, system), company, org (country, ministry, armed service, EU,
NATO, agency), technology, material, regulation (law, sanction, export rule), procurement (tender, grant, contract
notice) or place the relations below need. name exactly as in the text (English if the text gives it), kind from:
programme, product, company, org, technology, material, regulation, procurement, place.

relations: facts the text states, each as src - rel - dst:
- supplies: src (supplier) delivers to dst (buyer, prime contractor or programme)
- orders: src (buyer) orders or contracts dst (product, programme or company)
- funds: src finances dst (grant, loan, budget line)
- produces: src (company) makes dst (product)
- depends_on: src needs dst (component, material, technology)
- restricts: src (regulation or authority) bans, limits or suspends dst
- regulates: src (law, rule) governs dst
- part_of: src is part of dst
- competes: src competes with dst
- exports_to: src exports to dst (country)
- price: src (product or programme) has the stated amount; dst empty
- milestone: a step in the life of src (programme or product): test, delivery, first flight, launch, selection,
  approval, delay, cancellation; dst empty
For each relation give date (YYYY-MM-DD of the fact, not of the article, with precision day/month/year; empty if the
text gives none), amount and ISO currency, quantity and unit when stated, commitment (plan, framework, approval,
ceiling, contract, funded, delivered, site_selection, other), negative = true for cancellations, delays, cuts,
suspensions and bans, and quote: the exact words of the text (one sentence or part of it, max 300 characters, copied
character for character in the original language) that state the fact. No quote, no relation.
Return {"entities": [], "relations": []} when the text states no such facts."""

EXTRACT_SCHEMA = {
    "type": "object", "required": ["entities", "relations"],
    "properties": {
        "entities": {"type": "array", "items": {"type": "object", "required": ["name", "kind"], "properties": {
            "name": {"type": "string"}, "kind": {"type": "string", "enum": NODE_KINDS}}}},
        "relations": {"type": "array", "items": {"type": "object", "required": ["src", "rel", "quote"], "properties": {
            "src": {"type": "string"}, "rel": {"type": "string", "enum": RELS}, "dst": {"type": "string"},
            "date": {"type": "string"}, "precision": {"type": "string", "enum": ["day", "month", "year"]},
            "amount": {"type": "number"}, "currency": {"type": "string"}, "quantity": {"type": "number"},
            "unit": {"type": "string"}, "commitment": {"type": "string"}, "negative": {"type": "boolean"},
            "quote": {"type": "string"}}}}}}
