"""One CAPACITY observation: what a quote states, checked by code (EXP-028, private, offline).

A model proposes the fields; these rules only confirm that the quote carries them. Passing does not prove the
quote is true or material. Word lists were written against the panel, so hold-out controls must judge them.
"""
import re
from decimal import Decimal, InvalidOperation

STAGES = ("intention", "funded", "under_construction", "commissioned", "demonstrated_output")
OPERATIONAL = {"commissioned", "demonstrated_output"}
METRICS = {"actual_output", "nominal_capacity", "deliveries", "relative_capacity", "relative_output", "relative_floor_space"}
RELATIVE = {"relative_capacity", "relative_output", "relative_floor_space"}
BOUNDS = {"exact", "min", "max", "approx"}
TIERS = {"component", "finished", "platform", "unknown"}
FOLD = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
                      "–": "-", "—": "-", "‑": "-", " ": " ", " ": " "})

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12, "fifteen": 15, "sixteen": 16, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
SCALES = {"thousand": 10**3, "k": 10**3, "million": 10**6, "mn": 10**6, "mln": 10**6, "mio": 10**6,
          "billion": 10**9, "bn": 10**9}
NUMBER = re.compile(r"(?<![\w.,-])(\d{1,3}(?:[ ,.]\d{3})+|\d+(?:[.,]\d+)?|" + "|".join(WORDS) + r")"
                    r"(?=-fold\b|(?![\w-]))(?:\s*(thousand|million|billion|mln|mio|mn|bn|k)\b)?", re.I)
BOUND_CUES = (("max", r"(?:up to|as many as|at most|no more than|a maximum of|maximum of)"),
              ("min", r"(?:more than|over|above|at least|in excess of|exceeding|a minimum of)"),
              ("approx", r"(?:about|around|approximately|roughly|nearly|almost|some)"))
MONEY = re.compile(r"(?:[$€£]|\b(?:usd|eur|gbp|aud|sek|nok)\b)\s*$", re.I)
COMPONENT = ("forging", "barrel", "casing", "shell body", "motor", "propellant", "charge", "fuze", "warhead",
             "seeker", "component", "tube", "engine", "explosive")
FUTURE = re.compile(r"\b(?:will|would|expected|expects|plans?|planned|planning|intends?|intended|aims?|aiming|"
                    r"scheduled|due to|to be|set to|forecast|targets?|by the end of|going to|towards?|eventually|"
                    r"when\b[^.]{0,80}\bbecomes?)\b", re.I)
PAST = re.compile(r"\b(?:broke|laid|opened|started|began|begun|delivered|produced|doubled|was|were|has|have|"
                  r"took place|completed|is producing|are producing|into operation)\b", re.I)
NEGATED = re.compile(r"\b(?:not|never|no longer|delayed|postponed|cancelled|canceled|suspended|yet to)\b", re.I)
STAGE_CUES = {
    "intention": r"\b(?:will|expected|plans?|planned|intends?|aims?|scheduled|to be|set to|announc\w+|selected|by the end of|"
                 r"when\b[^.]{0,80}\bbecomes?)\b",
    "funded": r"\b(?:invest\w*|fund\w*|financ\w*|contract\w*|award\w*|allocat\w*|budget\w*)\b",
    "under_construction": r"(?:ground-?\s?breaking|br(?:oke|eaks?|eaking) ground|foundation stone|laid the foundation|under construction|"
                          r"construction (?:started|began|of|on)|starts? (?:the )?construction|(?:beginning|start) of construction|"
                          r"started building|(?:is|are) (?:building|constructing))",
    "commissioned": r"(?:into operation|operational|inaugurat\w*|commissioned|open(?:ed|s|ing)\b|ribbon[- ]cutting|cuts? (?:the )?ribbon|"
                    r"(?:has |have )?completed (?:a|an|the|its)\b|"
                    r"(?:began|started|starts?) (?:serial )?production|production (?:began|started))",
    "demonstrated_output": r"\b(?:produc\w+|deliver\w+|output|manufactur\w+|doubled|tripled)\b",
}
METRIC_CUES = {
    "actual_output": r"\b(?:produc\w+|output|manufactur\w+|made|built|doubled|tripled)\b",
    "nominal_capacity": r"\b(?:capacity|capable of|able to produce|up to|designed to|rated|"
                        r"will (?:\w+ )?(?:produce|manufacture|build)|to be (?:produced|manufactured|built))\b",
    "deliveries": r"\b(?:deliver\w+|shipped|handed over|transferred)\b",
    "relative_capacity": r"\bcapacity\b",  # the plan or fact status says whether it has happened
    "relative_output": r"\b(?:produc\w+|output|manufactur\w+)\b",
    "relative_floor_space": r"\b(?:space|floor|footprint|square)\b",
}
PERIOD_CUES = {"day": r"\b(?:day|daily)\b", "week": r"\b(?:week|weekly)\b", "month": r"\b(?:month|monthly)\b",
               "quarter": r"\b(?:quarter|q[1-4])\b", "year": r"\b(?:year|yearly|annual\w*|per annum|p\.a\.|(?:19|20)\d\d)\b"}
NOT_OUTPUT = re.compile(r"\bproduction (?:capacity|line|lines|site|sites|facility|facilities|plant|plants|hall|halls)\b")
BARE_AFTER = re.compile(r"\s*(?:$|[.,;:)]|(?:a|an|per|each|every|annual\w*|yearly|monthly|weekly|daily|in|by)\b)")
OUTPUT_NO_FIGURE = re.compile(r"\b(?:doubl\w+|tripl\w+|deliver\w+)\b")
DONE = re.compile(r"\b(?:opened|inaugurated|hosted a ribbon[- ]cutting|cut the ribbon)\b")  # the event, then what the plant will do
NEAR = 45


def folded(text):
    """Lower-case text with single-character typography folded and spaces collapsed, plus an index back."""
    out, index, space = [], [], False
    for at, ch in enumerate(text or ""):
        if ch == "­":
            continue
        ch = ch.translate(FOLD).lower()
        if ch.isspace():
            if not space and out:
                out.append(" "); index.append(at)
            space = True
        else:
            out.append(ch); index.append(at); space = False
    return "".join(out), index


def locate(quote, text):
    """Exact span of the quote in the document, ignoring typography, or None. An ambiguous quote still gets the first."""
    needle = folded(quote)[0].strip()
    hay, index = folded(text)
    at = hay.find(needle) if needle else -1
    if at < 0:
        return None
    return index[at], index[at + len(needle) - 1] + 1


def as_decimal(value):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def numbers_in(quote):
    """Numbers stated in the quote: value, bound and where they sit. Years stay numbers; the unit rule tells them apart."""
    found, text = [], folded(quote)[0]
    for match in NUMBER.finditer(text):
        raw, scale = match.group(1), (match.group(2) or "").lower()
        if raw in WORDS:
            value = Decimal(WORDS[raw])
        elif re.fullmatch(r"\d{1,3}(?:[ ,.]\d{3})+", raw):
            value = Decimal(re.sub(r"[ ,.]", "", raw))
        else:
            value = Decimal(raw.replace(",", "."))
        value *= SCALES.get(scale, 1)
        before = text[:match.start()].rstrip()
        bound = "exact"
        for name, cue in BOUND_CUES:
            if re.search(r"\b" + cue + r"$", before):
                bound = name
                break
        found.append({"value": value, "bound": bound, "start": match.start(), "end": match.end(),
                      "money": bool(MONEY.search(before))})
    return found


def check_quantity(quantity, stage, tier, quote):
    """Reasons a stated quantity is not what the quote says; [] when every dimension is there."""
    if not isinstance(quantity, dict):
        return ["QUANTITY_NOT_OBJECT"]
    reasons, text = [], folded(quote)[0]
    metric, unit_text = quantity.get("metric"), folded(quantity.get("unit_text") or "")[0].strip()
    value = as_decimal(quantity.get("value"))
    if metric not in METRICS:
        reasons.append("METRIC_UNKNOWN")
    elif not re.search(METRIC_CUES[metric], text):
        reasons.append("METRIC_NOT_IN_QUOTE")
    if metric == "actual_output" and stage != "demonstrated_output":
        reasons.append("ACTUAL_OUTPUT_WITHOUT_DEMONSTRATED_STAGE")
    if quantity.get("bound") not in BOUNDS:
        reasons.append("BOUND_UNKNOWN")
    if value is None:
        # "doubled" has no number to find; only a relative metric may go without one.
        if metric not in RELATIVE or not re.search(r"\b(?:doubl|tripl)", text):
            reasons.append("VALUE_INVALID")
        return reasons
    matches = [n for n in numbers_in(quote) if n["value"] == value]
    if not matches:
        return reasons + ["NUMBER_NOT_IN_QUOTE"]
    if not unit_text or unit_text not in text:
        reasons.append("UNIT_NOT_IN_QUOTE")
    else:
        unit_at = text.find(unit_text)
        close = [n for n in matches if 0 <= unit_at - n["end"] <= NEAR
                 or (0 <= n["start"] - unit_at - len(unit_text) <= NEAR and BARE_AFTER.match(text[n["end"]:]))]  # "8 forgings and 12 barrels"
        others = [n for n in numbers_in(quote) if n["value"] != value]
        close = [n for n in close if not any(min(n["end"], unit_at) <= o["start"] < max(n["start"], unit_at) for o in others)]
        # "100,000 shells ... rising to 200,000 a year": a bare number keeps the unit named before it.
        close += [n for n in matches if n["start"] > unit_at and BARE_AFTER.match(text[n["end"]:])
                  and any(o["start"] < unit_at for o in numbers_in(quote))]
        # "sixteen-fold" names the number inside its own unit words.
        close += [n for n in matches if unit_at <= n["start"] < unit_at + len(unit_text)]
        if not close:
            reasons.append("UNIT_NOT_NEXT_TO_NUMBER")
        else:
            matches = close
        after = text[unit_at + len(unit_text):].lstrip()
        if not any(unit_text.startswith(c) or c in unit_text for c in COMPONENT) and any(after.startswith(c) for c in COMPONENT):
            reasons.append("UNIT_IS_PART_OF_COMPONENT_NAME")  # "rocket" in "rocket motors"
    if any(c in unit_text for c in COMPONENT) and tier == "finished":
        reasons.append("COMPONENT_AS_FINISHED_PRODUCT")
    if all(n["money"] for n in matches):
        reasons.append("NUMBER_IS_MONEY")
    if quantity.get("bound") in BOUNDS and not any(n["bound"] == quantity["bound"] for n in matches):
        reasons.append("BOUND_NOT_IN_QUOTE")
    period = quantity.get("period")
    if metric not in RELATIVE:
        if period not in PERIOD_CUES:
            reasons.append("PERIOD_UNKNOWN")
        elif not re.search(PERIOD_CUES[period], text):
            reasons.append("PERIOD_NOT_IN_QUOTE")
    return reasons


def check_evidence(obs, document_text):
    """What the quote can carry: place in the document, stage, plan or fact, quantity."""
    reasons = []
    quote = obs.get("quote") if isinstance(obs.get("quote"), str) else ""
    if not quote.strip() or locate(quote, document_text) is None:
        reasons.append("QUOTE_NOT_IN_TEXT")
    text = folded(quote)[0]
    stage, status = obs.get("stage"), obs.get("status")
    if status not in ("fact", "plan"):
        reasons.append("STATUS_UNKNOWN")
    if stage is not None:
        if stage not in STAGES:
            reasons.append("STAGE_UNKNOWN")
        elif not re.search(STAGE_CUES[stage], NOT_OUTPUT.sub(" ", text) if stage == "demonstrated_output" else text):
            reasons.append("STAGE_NOT_IN_QUOTE")  # "production capacity" says what a plant could do, not what it did
    if stage == "demonstrated_output" and obs.get("quantity") is None and not OUTPUT_NO_FIGURE.search(text):
        reasons.append("OUTPUT_WITHOUT_FIGURE")  # "production has begun" reports a start, not an output
    if status == "fact":
        if stage == "intention":
            reasons.append("INTENTION_AS_FACT")
        future, done = FUTURE.search(text), DONE.search(text)
        event_first = stage == "commissioned" and obs.get("quantity") is None and future and done and done.start() < future.start()
        if future and not event_first and (stage in OPERATIONAL or not PAST.search(text)):
            reasons.append("FUTURE_WORDING_AS_FACT")
        if stage in OPERATIONAL and NEGATED.search(text):
            reasons.append("NEGATED_WORDING_AS_FACT")
    elif status == "plan":
        if stage in OPERATIONAL:
            reasons.append("PLAN_AS_OPERATIONAL")
        if not FUTURE.search(text) and not re.search(STAGE_CUES["intention"], text):
            reasons.append("PLAN_WITHOUT_FUTURE_WORDING")
    if obs.get("quantity") is not None:
        reasons += check_quantity(obs["quantity"], stage, obs.get("product_tier"), quote)
    return sorted(set(reasons))


def check_scope(obs):
    """Who, where and what. Names stay unresolved here: binding to catalogue IDs is a separate step."""
    reasons = [code for key, code in (("company", "COMPANY_MISSING"), ("site", "SITE_MISSING"), ("product", "PRODUCT_MISSING"))
               if not (isinstance(obs.get(key), str) and obs[key].strip())]
    if obs.get("product_tier") not in TIERS:
        reasons.append("PRODUCT_TIER_UNKNOWN")
    return reasons


def check_observation(obs, document_text):
    return sorted(set(check_evidence(obs, document_text) + check_scope(obs)))


def to_capacity(obs, source_id, key):
    """An observation in the shape capacity_v2.qualify reads. Call check_observation first; this only needs its quote found."""
    span = locate(obs.get("quote") or "", obs["document_text"])
    if span is None:
        raise ValueError("quote not in document")
    quantity = obs.get("quantity")
    modality = "planned" if obs.get("status") == "plan" else "observed" if obs.get("attribution") in ("government", "third_party") else "issuer_reported"
    result = {"id": key, "source_id": source_id, "quote": obs["document_text"][span[0]:span[1]],
              "span": {"start": span[0], "end": span[1]}, "stage": obs.get("stage"), "modality": modality,
              "stance": obs.get("stance", "SUPPORT"), "event_date": obs.get("event_date"),
              "company_id": obs.get("company_id"), "site_id": obs.get("site_id"), "product_id": obs.get("product_id")}
    if quantity is not None:
        result["quantity"] = {"metric": quantity.get("metric"), "value": quantity.get("value"), "bound": quantity.get("bound"),
                              "unit": quantity.get("unit"), "period": quantity.get("period"), "modality": modality}
    return result


def verify_packet(rows):
    """Everything the second reader must judge, unit and period included (main's packet drops unit)."""
    keys = ("company", "site", "product", "product_tier", "stage", "status", "event_date", "quantity", "quote")
    return {"observations": [dict({"n": n}, **{k: r.get(k) for k in keys}) for n, r in enumerate(rows)]}


def apply_verdicts(rows, answer):
    """Same return as main's: kept rows, rejected count, count without a usable verdict.

    One row needs one consistent boolean. Repeating the same verdict is harmless; contradicting it, a non-boolean or
    a missing verdict leaves the row unverified. Indexes outside the rows are ignored.
    """
    verdicts = (answer or {}).get("verdicts") if isinstance(answer, dict) else None
    said = {}
    for verdict in verdicts if isinstance(verdicts, list) else []:
        if not isinstance(verdict, dict) or type(verdict.get("n")) is not int or not 0 <= verdict["n"] < len(rows):
            continue
        said.setdefault(verdict["n"], set()).add(verdict.get("ok") if type(verdict.get("ok")) is bool else None)
    kept = [r for n, r in enumerate(rows) if said.get(n) == {True}]
    rejected = sum(1 for n in range(len(rows)) if said.get(n) == {False})
    return kept, rejected, len(rows) - len(kept) - rejected


CAPACITY_EXTRACT_SYSTEM = """You read one defence-industry document and list everything it states about production capacity:
a plant, line or site being planned, funded, built, opened or completed, and every production or delivery figure.
List only what the document itself states. A document often holds several such facts: give each its own observation.

An opening and a future output are two facts, so two observations, even inside one sentence. Copy `quote` verbatim from the
document, one sentence or less, containing every value you report. Name company, site and product as written. product_tier:
component (forgings, barrels, motors, charges, shell bodies), finished (rounds, missiles, vehicles), platform (ships,
aircraft), or unknown.

stage belongs to the fact in this observation: intention (announced, selected, expected or any future output),
funded (money or contract), under_construction (ground broken, foundation laid, being built), commissioned (opened,
completed, in operation), demonstrated_output (actual production or delivery is reported).
Use none when the fact does not place a facility in any of these, as with a milestone in building one hull.
status: fact when the document says it happened or is happening, plan when it says it will or may.
A figure that is still to come is stage intention and status plan, even at a plant that has already opened.
attribution: issuer, government or third_party. event_date: ISO date of the event if stated, otherwise leave it out.

Leave quantity out unless the quote gives a production or delivery figure; money, staff and floor area are never
production, except metric relative_floor_space for a stated rise in floor area. Fill:
value (number exactly as stated, never estimated; leave out only for doubled or tripled; for sixteen-fold give 16),
bound (exact, min for "more than", max for "up to", approx for "about"), unit (singular noun for what is counted),
unit_text (those words as they appear in the quote, never the number itself), period (day, week, month, quarter, year; none
for relative), metric (actual_output, nominal_capacity, deliveries, relative_capacity, relative_output,
relative_floor_space). A ceiling, target or future output is nominal_capacity. A delivery is deliveries, not output.
Give no observation for a fact you cannot quote."""

CAPACITY_VERIFY_SYSTEM = """You check production-capacity observations another reader took from a document. Each is a claim to
compare with its quote. Say ok=false only if the quote fails to support or contradicts what the observation claims.

Judge only the fields it fills. If quantity is null, it claims no figure, so never reject it for lacking one. company, site
and product come from the document around the quote, so reject them only if the quote contradicts them. The stage may be
stated in plain words: opened or inaugurated supports commissioned, broke ground or being built supports under_construction,
actual production or delivery supports demonstrated_output. A quote may hold other facts too; ignore those.
bound min means more than or at least, max means up to, approx means about.

Say ok=false when the claim is wider, stronger or different: a target as achieved, a ceiling as output, delivery as output, a
component as a finished product, a future event as done, a start of work as output, a different number, unit or period.
Give exactly one verdict for each n."""

CAPACITY_SCHEMA = {
    "type": "object", "required": ["observations"], "properties": {"observations": {"type": "array", "items": {
        "type": "object", "required": ["quote", "company", "site", "product", "product_tier", "stage", "status", "attribution"],
        "properties": {
            "quote": {"type": "string"}, "company": {"type": "string"}, "site": {"type": "string"},
            "product": {"type": "string"}, "product_tier": {"type": "string", "enum": sorted(TIERS)},
            "stage": {"type": "string", "enum": list(STAGES) + ["none"]},
            "status": {"type": "string", "enum": ["fact", "plan"]},
            "attribution": {"type": "string", "enum": ["issuer", "government", "third_party"]},
            "event_date": {"type": "string"},
            "quantity": {"type": "object", "required": ["bound", "unit", "unit_text", "period", "metric"], "properties": {
                "value": {"type": "number"}, "bound": {"type": "string", "enum": sorted(BOUNDS)},
                "unit": {"type": "string"}, "unit_text": {"type": "string"},
                "period": {"type": "string", "enum": sorted(PERIOD_CUES) + ["none"]},
                "metric": {"type": "string", "enum": sorted(METRICS)}}}}}}}}


def normalized(obs):
    """A model's answer in the form the rules read: "none" and absent values become None.

    A plan the quote words as future is an intention whatever stage the plant has reached. Only ever lowers the stage.
    """
    obs = dict(obs)
    obs["stage"] = None if obs.get("stage") in ("none", "") else obs.get("stage")
    if obs.get("status") == "plan" and obs["stage"] not in (None, "intention", "funded") and FUTURE.search(folded(obs.get("quote") or "")[0]):
        obs["stage"], obs["stage_lowered"] = "intention", True
    quantity = obs.get("quantity")
    if isinstance(quantity, dict):
        quantity = dict(quantity)
        quantity["period"] = None if quantity.get("period") in ("none", "") else quantity.get("period")
        obs["quantity"] = quantity if quantity else None
    else:
        obs["quantity"] = None
    return obs
