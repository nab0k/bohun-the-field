"""Selection, source attribution and conservative event identity. No I/O."""
from datetime import datetime
import re
from urllib.parse import urlsplit, urljoin
from html.parser import HTMLParser
from .contracts import sha, canonical
from .extraction import matches, normalize


def select_topic(title, facts, topics):
    text = title + " " + " ".join(f["text"] for f in facts)
    candidates = []
    for t in topics["topics"]:
        # topics-v2: weak words (countries, agencies, companies) count towards
        # min_hits but never qualify a topic without min_strong strong words.
        hits = [k for k in t["keywords"] if matches(text, k)]
        weak = [k for k in t.get("weak_keywords", []) if matches(text, k)]
        if len(hits) >= t.get("min_strong", 0) and len(hits) + len(weak) >= t["min_hits"]:
            candidates.append({"slug": t["slug"], "title": t["title"], "hits": hits, "weak_hits": weak,
                               "priority": t["priority"]})
    candidates.sort(key=lambda t: (-len(t["hits"]), -len(t["hits"]) - len(t["weak_hits"]), t["priority"], t["slug"]))
    return {"main": candidates[0] if candidates else None, "all": candidates}


def hard_bans(doc, extraction, dictionaries):
    title = normalize(doc.title)
    text = normalize(doc.text)
    bans = []
    ua = re.search(r"\b(ukrain\w*|kyiv|kiev|obukhiv)\b|україн|украин|київ|киев", text)
    location = any(matches(text, x) for x in dictionaries["hard_bans_extra"]["ukraine_location_markers"])
    if ua and location:
        bans.append("hard_ban:ukraine_location")
    # A market article mentioning wartime applications is not automatically a
    # report of troop movements. Check the actual headline/event for this ban.
    if re.search(r"\b(attack|attacks|strike on|shelling|killed|kills|injur\w*|clashes|troop movements|front line)\b", title):
        bans.append("hard_ban:military_situation")
    if re.search(r"\b(job|jobs|vacancy|hiring|career|archaeolog\w*|dog show)\b", title):
        bans.append("off_topic")
    if extraction["type"] == "other":
        bans.append("off_topic")
    # A mere name match never confirms country or sanctions. Use only supplied
    # eligibility evidence on the main actor, never an incidental opponent.
    for c in extraction["companies"]:
        if extraction["subject"] and matches(extraction["subject"], c["name"]) and c.get("eligibility_verified"):
            if c.get("country") in {"RU", "BY", "KP", "IR"} or c.get("sanctioned"):
                bans.append("hard_ban:excluded_actor")
    return sorted(set(bans))


def is_primary_url(url, dictionaries, company_hosts=()):
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    p = dictionaries["primary_source_domains"]
    official = {h.lower().removeprefix("www.") for h in p["official_hosts"]}
    companies = {h.removeprefix("www.").lower() for h in (*company_hosts, *p.get("company_hosts_seed", ()))}
    if host in official or host in companies:
        return True
    return any(host == s.lstrip(".") or host.endswith(s) for s in p["government_suffixes"])


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.urls.extend(v for k, v in attrs if k == "href" and v)


def primary_candidates(doc, dictionaries, html=""):
    parser = Links(); parser.feed(html)
    urls = parser.urls + re.findall(r"https://[^\s<>\"']+", doc.text)
    result = []
    for value in urls:
        value = urljoin(doc.url, value.rstrip(".,);"))
        u = urlsplit(value)
        if (u.scheme == "https" and u.hostname and not u.username and not u.password
                and value != doc.url and is_primary_url(value, dictionaries, doc.company_hosts)):
            if value not in result:
                result.append(value)
    return result[:2]


def source_class(doc, dictionaries):
    """Domain lists from dictionaries-v2; an unlisted outlet stays as supplied (default unknown)."""
    if doc.source_class == "P0" or is_primary_url(doc.url, dictionaries, doc.company_hosts):
        return "P0"
    host = (urlsplit(doc.url).hostname or "").lower().removeprefix("www.")
    lists = dictionaries.get("source_classes", {})
    for cls in ("P1", "P2"):
        if any(host == h or host.endswith("." + h) for h in lists.get(cls, ())):
            return cls
    return doc.source_class


def source_entry(doc, dictionaries):
    cls = source_class(doc, dictionaries)
    return {"document_id": doc.id, "url": doc.url, "outlet": doc.outlet,
            "date": doc.published_at, "role": "primary" if cls == "P0" else "report",
            "class": cls, "text_sha256": sha(doc.text)}


def date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event_match(left, right):
    """Procedure IDs or hard facts; titles/similar names alone never merge."""
    if left.get("procedure_ref") and left["procedure_ref"] == right.get("procedure_ref"):
        return "procedure_id"
    if left["type"] != right["type"] or not left.get("subject") or left["subject"] != right.get("subject"):
        return None
    if not left.get("object_key") or left["object_key"] != right.get("object_key"):
        return None
    try:
        if abs((date(left["published_at"]) - date(right["published_at"])).total_seconds()) > 30*86400:
            return None
    except (ValueError, TypeError):
        return None
    a, b = left.get("amount"), right.get("amount")
    if not a or not b or a["currency"] != b["currency"]:
        return None
    x, y = float(a["value"]), float(b["value"])
    if x <= 0 or y <= 0 or abs(x-y)/max(x,y) > .05:
        return None
    return "hard_features"


def event_identity(extraction, doc):
    # Only explicit labelled identifiers; numbers elsewhere are not procedures.
    m = re.search(r"\b(?:contract|procedure|solicitation)\s+(?:no\.?\s*|number\s*|#\s*)([A-Z0-9][A-Z0-9/-]{5,})\b", doc.text, re.I)
    # Product identifiers occurring literally in the title are usable hard keys.
    products = re.findall(r"\b[A-Z]{2,}[A-Z0-9]*(?:-[A-Z0-9]+)+\b", doc.title)
    amounts = [a for f in extraction["facts"] if f["amount_relevance"] == "event_clause" for a in f["amounts"]]
    return {"procedure_ref": m[1].upper() if m else None, "type": extraction["type"],
            "subject": normalize(extraction["subject"] or ""), "object_key": "|".join(sorted(set(products))),
            "amount": amounts[0] if amounts else None, "published_at": doc.published_at,
            "key": sha(canonical(["procedure", m[1].upper()]) if m else
                       canonical(["single_document", doc.url, extraction["type"], normalize(extraction["subject"] or "")]))}


TITLE_STOP = {"the", "a", "an", "to", "for", "of", "and", "with", "in", "on", "by", "at", "as", "from", "up",
              "us", "u", "s", "more", "than", "each", "a", "year", "per", "new", "de", "la", "le", "les", "des", "pour"}


def title_tokens(title):
    # "25 000" / "25,000" -> "25000"; hyphenated product names stay whole (f-35).
    t = re.sub(r"(?<=\d)[ ,.](?=\d{3}\b)", "", normalize(title))
    return {w for w in re.findall(r"[\w]+(?:-[\w]+)*", t) if w not in TITLE_STOP}


def duplicate_clusters(groups, window_days=7, min_shared=3):
    """Possible duplicates not merged by hard features: >= min_shared title words within the window.

    groups: [{"title", "date", "rank"}]; returns {index: representative index}. The representative
    (lowest rank: best source class, most documents, earliest) goes to the writer; the rest HOLD.
    """
    parent = list(range(len(groups)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    tokens = [title_tokens(g["title"]) for g in groups]
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            if groups[i].get("banned") or groups[j].get("banned"):
                continue
            if abs((date(groups[i]["date"]) - date(groups[j]["date"])).total_seconds()) > window_days * 86400:
                continue
            if len(tokens[i] & tokens[j]) >= min_shared:
                parent[root(j)] = root(i)
    clusters = {}
    for i in range(len(groups)):
        clusters.setdefault(root(i), []).append(i)
    rep = {}
    for members in clusters.values():
        best = min(members, key=lambda i: groups[i]["rank"])
        for i in members:
            rep[i] = best
    return rep
