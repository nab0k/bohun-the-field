# Reused verbatim from analyst/rules.py at 8197629 (EXP-013).
"""EXP-013: event ontology v0, amounts and modality — literal rules with traceable evidence (RU / UK / EN).

Lesson from PETRARCH: a dictionary coder only knows what is in its dictionaries. Unknown wording yields
`other` and is counted, never guessed.
"""
import re

ONTOLOGY_VERSION = "events-v1"

_AMT = r"(\$|€|£|us\$)?[\d.,]+ ?(million|billion|bn|m|млн|млрд)?"
_W = r"(\S+ ){0,4}"
# (event type, pattern). Order matters: the first matching type wins; the title is checked before the body.
EVENTS = [
    ("acquisition", r"\b(acquir(e|es|ed|ing)|takeover|buys?|bought|to buy|win(s)? bid to buy|merger)\b(?!.*\bdrones?\b)|"
                    r"придба\w*|купи\w*|поглин|поглощ|rachat|übernahme"),
    ("contract_signed", r"sign(s|ed)? (a |an |the )?" + _W + r"(contract|deal|agreement)|"
                        r"підписа\w* (контракт|угод)|подписа\w* (контракт|соглашени)|signe un contrat|unterzeichne"),
    ("contract_award", r"award(s|ed)? (a |an |the )?" + _W + r"(contract|order|task order)|"
                       r"(wins?|won|secures?|secured|receives?|received|lands?|landed|books?|booked|gets) " + _W +
                       r"(contract|order|deal|award)|" + _AMT + r" (\S+ ){0,6}(award|contract|order)\b|"
                       r"(отримав|отримала|отримали|получил|получила|получили) (\S+ ){0,3}(контракт|замовлення|заказ)|"
                       r"\bcontract (award|worth|valued)|\border (for|worth)|commande|contrat|auftrag|zuschlag"),
    ("grant_award", r"(grant|funding) (awarded|from)|awarded \S+ funding|\b(selected|receives?|received|awarded) "
                    r"(for )?(\S+ )?(funding|grant)|грант"),
    ("tender_notice", r"\b(tender|request for proposals?|rfp|rfi|call for tenders?|procurement notice|solicitation)\b|"
                      r"тендер|закупівл\w+ оголош|объявил\w* закупк|appel d.offres|ausschreibung"),
    ("procurement_plan", r"\b(seeks?|looking for|plans? to (buy|purchase|acquire|procure)|to purchase|requirement for|"
                         r"confirme l.achat|achat de|purchase of|budget|plans? " + _AMT + r")|закупит|закупів|купит|бюджет"),
    ("grant_call", r"call for proposals|open call|funding call"),
    ("investment", r"\brais(e|es|ed|ing) (\$|€|£|us\$)?[\d.,]+ ?(m|bn|million|billion)?|"
                   r"(series [abcd]|seed) (round|funding)|funding round|\binvest(s|ed|ment) (of |in )|"
                   r"залучи\w* інвестиц|привлек\w* инвестиц|раунд"),
    ("capacity", r"(new|opens?|opened|build(s|ing)?|establish(es|ed)?|expand(s|ed|ing)?|ramp(s|ed|ing)? up|"
                 r"boost(s|ed)?|to produce|will produce|produce) (\S+ ){0,4}(factory|plant|production|production line|"
                 r"capacity|output|facility|centre|center|annually|per year|a year)|"
                 r"(завод|виробництв\w+|производств\w+) (\S+ ){0,2}(відкри|запуст|расшир|збільш|увелич)|"
                 r"(відкри|запуст|расшир|збільш|увелич)\w* (\S+ ){0,2}(завод|виробництв|производств)"),
    ("delivery", r"\b(deliver(s|ed)|handed over) (\S+ ){0,4}(to|for)\b|first deliver(y|ies)|"
                 r"передав|передал|поставил|поставка \S+"),
    ("partnership", r"(partnership|partners with|joint venture|teaming agreement|memorandum of understanding|\bmou\b|"
                    r"\btaps\b|collaborat\w+ with|teams? up)|спільне підприємство|меморандум|совместное предприятие"),
    ("test_demo", r"\b(test(s|ed)?|trial(s|ed)?|demonstrat\w+|milestone|achieves?|unveil(s|ed)?|first flight|"
                  r"launch(es|ed)?)\b|випробуван|испытан"),
    ("challenge_call", r"\b(challenge|competition|hackathon|accelerator cohort)\b"),
    ("regulation", r"export control|regulation|\blaw\b|decree|sanction|закон|постанов|указ|санкц"),
    ("military_operation", r"\b(strike|strikes|attack(s|ed)?|killed|kills|injur\w+|clashes|shelling|front line|"
                           r"air defen[cs]es? (down|destroy)|shot down|drones? down)\b|удар|обстріл|атак|загинул|поранен"),
    ("diplomacy_policy", r"\b(meets?|meeting|summit|talks|discuss(es|ed)?|says|said|urges?|calls on|visit(s|ed)?)\b|"
                         r"зустріч|встреч|переговор|заяви"),
]
EVENT_RE = [(t, re.compile(p, re.I)) for t, p in EVENTS]
MARKET_TYPES = {"acquisition", "contract_signed", "contract_award", "grant_award", "tender_notice",
                "procurement_plan", "grant_call", "investment", "capacity", "delivery", "partnership", "test_demo",
                "challenge_call", "regulation"}

PLANNED = re.compile(r"\b(plans? to|planned|will|intends? to|aims? to|expected to|could|may|considering|"
                     r"proposed|seeks? to|negotiat\w+|in talks)\b|планує|планирует|має намір|намерен|"
                     r"розгляда|рассматрива|можуть|может|переговор", re.I)

MULT = {"million": 1e6, "m": 1e6, "mn": 1e6, "mln": 1e6, "млн": 1e6, "мільйон": 1e6, "миллион": 1e6,
        "billion": 1e9, "bn": 1e9, "b": 1e9, "млрд": 1e9, "мільярд": 1e9, "миллиард": 1e9,
        "thousand": 1e3, "k": 1e3, "тис": 1e3, "тыс": 1e3}
CUR = {"$": "USD", "us$": "USD", "usd": "USD", "dollars": "USD", "доларів": "USD", "долларов": "USD",
       "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "євро": "EUR", "евро": "EUR",
       "£": "GBP", "gbp": "GBP", "pounds": "GBP", "₴": "UAH", "uah": "UAH", "грн": "UAH", "гривень": "UAH"}
_NUM = r"(\d{1,3}(?:[ ,.]\d{3})+|\d+(?:[.,]\d+)?)"
_MULT = r"(million|billion|thousand|mln|mn|bn|m|b|k|млн|млрд|тис\.?|тыс\.?|мільйон\w*|миллион\w*|мільярд\w*|миллиард\w*)"
AMOUNT_RES = [
    re.compile(r"(us\$|\$|€|£|₴)\s?" + _NUM + r"\s?" + _MULT + r"?(?!\w)", re.I),
    re.compile(_NUM + r"\s?" + _MULT + r"?\s?(usd|eur|gbp|uah|dollars|euros?|pounds|доларів|долларов|євро|евро|грн|гривень)(?!\w)", re.I),
]


def _number(raw):
    raw = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}([,.]\d{3})+", raw):
        return float(re.sub(r"[,.]", "", raw))
    return float(raw.replace(",", "."))


def amounts(text):
    out = []
    for i, rx in enumerate(AMOUNT_RES):
        for m in rx.finditer(text):
            if i == 0:
                cur, num, mult = m.group(1), m.group(2), m.group(3)
            else:
                num, mult, cur = m.group(1), m.group(2), m.group(3)
            try:
                value = _number(num)
            except ValueError:
                continue
            key = (mult or "").lower().rstrip(".")
            factor = next((f for k, f in MULT.items() if key == k or (len(k) > 3 and key.startswith(k))), 1)
            value *= factor
            if value < 1000:  # "$5" without multiplier is noise for this domain
                continue
            out.append({"value": value, "currency": CUR.get(cur.lower(), cur.upper()), "span": (m.start(), m.end())})
    out.sort(key=lambda a: a["span"][0])
    dedup = []
    for a in out:
        if not dedup or a["span"][0] >= dedup[-1]["span"][1]:
            dedup.append(a)
    return dedup


def events(text, title_len=None):
    """All matching types. With `title_len`, a match inside the title outranks any body match."""
    found = []
    for rank, (etype, rx) in enumerate(EVENT_RE):
        m = rx.search(text)
        if m:
            in_title = title_len is not None and m.start() < title_len
            found.append({"type": etype, "span": (m.start(), m.end()), "in_title": in_title, "rank": rank})
    found.sort(key=lambda e: (not e["in_title"], e["rank"]))
    return found


def modality(text, span, window=120):
    a = max(0, span[0] - window)
    return "planned" if PLANNED.search(text[a:span[1] + 40]) else "reported"
