"""Literal extraction; every output value must be supported in its own quote."""
import re
from decimal import Decimal
from functools import lru_cache
from ..company_ner.linker import source_name, tokens
from ..companies.normalize import DEFENCE_BUYER
from . import legacy_rules
from .contracts import sha


def normalize(text):
    return text.replace("’", "'").replace("‘", "'").casefold()


def matches(text, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(normalize(phrase)) + r"(?!\w)", normalize(text)))


def modality(text, dictionaries):
    rules = dictionaries["modality"]
    # A signed MoU to explore manufacturing is a plan for manufacturing, not a
    # manufacturing contract. Keep dictionary hits for the reviewer to inspect.
    hits = {k: [s for s in v if matches(text, s)] for k, v in rules.items()}
    if hits.get("negated"):
        return "negated", hits
    if hits.get("planned") and (matches(text, "explore") or matches(text, "memorandum")
                                or matches(text, "memorandum of cooperation")):
        return "planned", hits
    for k in ("completed", "delivered", "signed", "announced", "planned"):
        if hits.get(k):
            return k, hits
    return "unknown", hits


def commitment(text, dictionaries):
    for kind in ("approval", "framework", "ceiling", "funded", "delivered", "contract"):
        if any(matches(text, x) for x in dictionaries["commitment"][kind]):
            return kind
    return None


def money(text):
    out = []
    for a in legacy_rules.amounts(text):
        start, end = a["span"]
        prefix = text[max(0, start-4):start].upper()
        currency = a["currency"]
        # Bare dollars do not establish USD in non-US reporting.
        if currency == "USD" and prefix.endswith(("CAD", "CA", "C")):
            currency = "CAD"
        elif currency == "USD" and prefix.endswith(("NZ", "NZD")):
            currency = "NZD"
        elif currency == "USD" and text[start:end].lstrip().startswith("$"):
            currency = "DOLLAR_UNSPECIFIED"
        out.append({"value": str(Decimal(str(a["value"])).normalize()),
                    "currency": currency, "literal": text[start:end], "span": [start, end]})
    # The original EXP-013 rule does not cover French plural millions/de dollars.
    for m in re.finditer(r"(\d+(?:[,.]\d+)?)\s+(millions?|milliards?)\s+(?:de\s+)?(dollars|euros)", text, re.I):
        if any(m.start() < a["span"][1] and m.end() > a["span"][0] for a in out):
            continue
        value = Decimal(m[1].replace(",", ".")) * (10**9 if m[2].lower().startswith("milliard") else 10**6)
        out.append({"value": str(value.normalize()), "currency": "EUR" if m[3].lower() == "euros" else "DOLLAR_UNSPECIFIED",
                    "literal": m[0], "span": [m.start(), m.end()]})
    return sorted(out, key=lambda a: a["span"])


def sentences(text):
    # Newlines preserve titles. Do not split decimals, U.S., initials or Ltd.
    boundary = re.compile(r"\n+|(?<=[.!?])\s+(?=[A-ZÀ-Ý\"“])")
    start = 0
    for m in boundary.finditer(text):
        end = m.start()
        piece = text[start:end]
        if piece.strip() and not re.search(r"\b(?:U\.S|U|S|Ltd|Inc|Mr|Dr)\.$", piece):
            left = start + len(piece) - len(piece.lstrip())
            yield left, end - (len(piece) - len(piece.rstrip()))
            start = m.end()
    if text[start:].strip():
        yield start + len(text[start:]) - len(text[start:].lstrip()), len(text.rstrip())


def quote_windows(text, start, end):
    # A long sentence is represented by consecutive word-bound windows. No
    # values beyond a fact's own <=200-character quote are attached to that fact.
    while start < end:
        stop = min(start+200, end)
        if stop < end:
            cut = text.rfind(" ", start, stop+1)
            if cut > start:
                stop = cut
        yield start, stop
        start = stop
        while start < end and text[start].isspace():
            start += 1


TYPE_MAP = {"contract_signed": "contract", "contract_award": "contract", "procurement_plan": "contract",
            "grant_award": "funding", "investment": "funding", "tender_notice": "opportunity",
            "grant_call": "opportunity", "challenge_call": "opportunity", "capacity": "production",
            "delivery": "production", "partnership": "partnership", "acquisition": "partnership",
            "regulation": "policy", "test_demo": "technology"}


def event_type(title, text):
    # Specific title cues outrank incidental contracts in the body.
    t = normalize(title)
    for kind, rx in (("funding", r"\b(funding|grant|non-repayable contribution)\b"),
                     ("production", r"\b(shipset|production milestone|production sprint|delivers|delivered)\b|\bto produce\b.*\bannually\b"),
                     ("policy", r"\b(drops|scraps|cancels|renonce)\b")):
        if re.search(rx, t):
            return kind
    if re.search(r"\b(contract|order|contrat)\b|confirme l'achat", t):
        return "contract"
    if re.search(r"\b(explore|partnership|joint venture|teams up)\b", t):
        return "partnership"
    found = legacy_rules.events(title + "\n" + text, title_len=len(title))
    return TYPE_MAP.get(found[0]["type"], "other") if found else "other"


def title_subject(title):
    # Literal surface candidate only: never a confirmed corporate identity.
    rx = r"\b(?:awarded|award|advances|achieves|receives|wins|delivers|signed|sign|agree|drops|confirme|plans|announces|secures|to explore|to produce)\b"
    m = re.search(rx, title, re.I)
    s = title[:m.start()].strip(" ,—–:-") if m else ""
    if not s or len(s) > 110 or s.lower() in {"the", "a", "an"}:
        return None
    return s


@lru_cache(maxsize=16384)
def name_parts(name):
    return tokens(name)


def company_mentions(text, catalog, ner_spans=()):
    result = []
    present = set(tokens(text))
    for c in catalog:
        for name in [c.get("name", ""), *c.get("aliases", [])]:
            parts = name_parts(name)
            if not parts or (len(parts) == 1 and len(parts[0]) < 4):
                continue
            if not set(parts) <= present:
                continue
            hit = source_name(text, parts)
            if hit:
                result.append({"name": hit["text"], "catalog_candidate": c.get("id", c.get("catalog_record_id")),
                               "company_id": None, "link_status": "candidate", "country": c.get("country"),
                               "sanctioned": c.get("sanctioned") is True})
                break
    for span in ner_spans:
        a, b = span.get("start"), span.get("end")
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text) or text[a:b] != span.get("text"):
            raise ValueError("NER_MENTION_NOT_SOURCE_BOUND")
        result.append({"name": text[a:b], "company_id": None, "link_status": "candidate"})
    return sorted({x["name"]: x for x in result}.values(), key=lambda x: x["name"])


def extract(doc, resources, catalog=(), ner_spans=()):
    d = resources.dictionaries
    kind = event_type(doc.title, doc.text)
    subject = title_subject(doc.title)
    predicate = {"contract": "contract_award", "funding": "funding_award", "production": "production_capacity"}.get(kind, kind+"_statement")
    facts = []
    for s, e in sentences(doc.text):
        for a, b in quote_windows(doc.text, s, e):
            q = doc.text[a:b]
            # Ignore unrelated footer prose; retain event sentences and data.
            if len(q) < 15:
                continue
            amounts = money(q)
            mode, mode_hits = modality(q, d)
            quantities = [{"literal": m[0], "span": [m.start(), m.end()]} for m in
                          re.finditer(r"\b\d[\d,. ]*\s*(?:kits?|shipsets?|systems?|aircraft|helicopters?|projects?|years?|annually|per year)\b", q, re.I)]
            dates = [m[0] for m in re.finditer(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:Jan\w*|Feb\w*|Mar\w*|Apr\w*|May|Jun\w*|Jul\w*|Aug\w*|Sep\w*|Oct\w*|Nov\w*|Dec\w*)\s+\d{4})\b", q, re.I)]
            mentions = company_mentions(q, catalog)
            buyer = [m[0] for m in DEFENCE_BUYER.finditer(q)]
            local_subject = subject if subject and subject in q else None
            title_amount = a == 0 and any(v["span"][1] <= len(doc.title) for v in amounts)
            award_clause = (re.search(re.escape(subject) + r"[^.!?\n]{0,80}\b(?:awarded|received|secured|won|signed|contract|funding|contribution)\b[^.!?\n]{0,80}[$€£]", q, re.I)
                            if subject and "READ:" not in q else None)
            relevant_amount = bool(local_subject and (title_amount or award_clause))
            if not (amounts or mode != "unknown" or quantities or dates or mentions or buyer or local_subject):
                continue
            fact = {"id": "F"+sha(doc.id+"|"+str(a)+"|"+q)[:20], "document_id": doc.id,
                    "predicate": predicate, "text": q, "quote": q, "quote_start": a, "quote_end": b,
                    "quote_sha256": sha(q), "quote_verified": doc.text[a:b] == q,
                    "artifact_sha256": sha(doc.text), "modality": mode, "modality_hits": mode_hits,
                    "commitment": commitment(q, d), "amounts": amounts, "quantities": quantities, "dates": dates,
                    "amount_relevance": "event_clause" if relevant_amount else "unassigned",
                    "participant": local_subject, "companies": mentions, "buyers": buyer,
                    "subject_name": local_subject,
                    "object_text": q[q.index(local_subject)+len(local_subject):].strip() if local_subject else None,
                    "not_disclosed": any(matches(q, x) for x in d["not_disclosed_markers"]),
                    "source_url": doc.url, "source": doc.outlet, "block": "what_happened"}
            facts.append(fact)
    all_mentions = company_mentions(doc.text, catalog, ner_spans)
    key = {"participant": bool(subject and any(f["participant"] for f in facts)),
           "partner": bool(subject and re.search(r"\band\b|\bet\b", subject, re.I)),
           "object": any(f["participant"] and len(f["quote"].split()) >= 6 for f in facts),
           # A programme-wide sum in a sidebar is not the participant's award.
           "amount_or_not_disclosed": any((f["amounts"] and f["amount_relevance"] == "event_clause") or
                                           (f["not_disclosed"] and f["participant"]) for f in facts),
           "buyer": any(f["buyers"] for f in facts), "jurisdiction": bool(subject),
           "date": any(f["dates"] for f in facts), "donor": bool(subject),
           "deadline": any(f["dates"] and matches(f["text"], "deadline") for f in facts)}
    required = d["key_facts_by_event_type"].get(kind, [])
    missing = [k for k in required if not key.get(k)]
    if kind == "other":
        missing.append("event_type")
    return {"document_id": doc.id, "type": kind, "subject": subject, "facts": facts,
            "companies": all_mentions, "key_facts": key, "missing": missing,
            "headline_modality": modality(doc.title, d)[0]}
