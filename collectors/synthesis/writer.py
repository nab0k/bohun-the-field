"""Offline writer contract and conservative checks. No Gemini transport here."""
from copy import deepcopy
import json
import re
from .contracts import sha, canonical
from .extraction import matches, normalize, modality

BLOCKS = ("what_happened", "context", "policy_direction", "bigger_picture", "supply_chain", "why_it_matters")
BOUNDS = {"what_happened": (1, 3), "context": (0, 3), "policy_direction": (0, 2),
          "bigger_picture": (0, 1), "supply_chain": (0, 2), "why_it_matters": (0, 1)}


def request(packet, resources):
    system = re.search(r"```text\n(.*?)\n```", resources.prompt, re.S)
    objects = re.findall(r"```json\n(.*?)\n```", resources.prompt, re.S)
    if not system or len(objects) != 2:
        raise ValueError("WRITER_PROMPT_CONTRACT")
    schema = json.loads(objects[-1])
    schema = deepcopy(schema)
    for prop in schema["properties"].values():
        if "$ref" in prop:
            ref = prop.pop("$ref")
            if ref != "#/$defs/sentences":
                raise ValueError("UNKNOWN_SCHEMA_REF")
            limits = deepcopy(prop); prop.update(deepcopy(schema["$defs"]["sentences"])); prop.update(limits)
    schema.pop("$defs")
    return {"system_instruction": system[1], "packet": packet, "schema": schema,
            "temperature": .2, "max_output_tokens": 1200, "packet_hash": sha(canonical(packet)),
            "transport": "NOT_CONFIGURED", "model": None}


def words(text):
    return re.findall(r"\w+(?:[-']\w+)*", normalize(text))


def numbers(text):
    # Product numbers stay separately checked by lexical support; permit only
    # numbers literally present (normalizing grouping punctuation/spaces).
    out = set()
    for m in re.finditer(r"(?<!\w)\d+(?:[,.]\d+)*(?!\w)", text):
        s = m[0]
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", s):
            s = s.replace(",", "")
        out.add(s)
    return out


def validate(output, packet, dictionaries):
    codes = []
    facts = {f["id"]: f for f in packet["facts"]}
    if len(facts) != len(packet["facts"]):
        raise ValueError("DUPLICATE_FACT_ID")
    if not isinstance(output, dict) or set(output) != {"headline", *BLOCKS}:
        return {"ok": False, "codes": ["writer_schema"], "claims": []}
    rows = []
    h = output["headline"]
    if not isinstance(h, dict):
        return {"ok": False, "codes": ["writer_schema"], "claims": []}
    rows.append(("headline", h, 90))
    for block in BLOCKS:
        values = output[block]
        lo, hi = BOUNDS[block]
        if not isinstance(values, list) or not lo <= len(values) <= hi:
            codes.append("writer_schema:"+block); continue
        rows.extend((block, value, 400) for value in values)
    claims = []
    for block, row, bound in rows:
        reasons = []
        if (not isinstance(row, dict) or set(row) != {"text", "fact_ids"}
                or not isinstance(row.get("text"), str) or not 1 <= len(row["text"]) <= bound
                or not isinstance(row.get("fact_ids"), list) or not row["fact_ids"]
                or any(not isinstance(i, str) or i not in facts for i in row["fact_ids"])):
            codes.append("writer_schema_or_fact_id"); continue
        refs = [facts[i] for i in row["fact_ids"]]
        # The implication may rest on any supplied layer (rules v2.0 §8: "Why it matters" from blocks 2-6).
        allowed = {"what_happened"} if block == "headline" else set(BLOCKS) if block == "why_it_matters" else {block}
        if any(f["block"] not in allowed for f in refs):
            reasons.append("wrong_fact_block")
        text = row["text"]
        support = " ".join(f["text"] for f in refs)
        if not numbers(text) <= numbers(support):
            reasons.append("unsupported_number")
        for currency in ("USD", "EUR", "CAD", "NZD", "GBP", "dollars", "euros", "$", "€", "£"):
            if currency in text and currency not in support and not any(
                    a.get("currency") == currency for f in refs for a in f.get("amounts", [])):
                reasons.append("unsupported_currency")
        # Proper-name-like phrases and product identifiers must be literal in
        # cited evidence. This is a necessary check, not semantic entailment.
        # Attribution "according to <outlet>" is required by the rules, so source outlets count as support.
        outlets = " ".join(s.get("outlet", "") for s in packet.get("sources", []))
        for name in re.findall(r"\b[A-Z][A-Za-z0-9]*(?:[-][A-Za-z0-9]+)+(?:\s+[A-Z][\w-]+)*|\b[A-Z][\w-]+\s+[A-Z][\w-]+", text):
            if normalize(name) not in normalize(support) and normalize(name) not in normalize(outlets):
                reasons.append("unsupported_name")
        reported_mode = modality(text, dictionaries)[0]
        modes = {f.get("modality", "unknown") for f in refs}
        if reported_mode in {"signed", "delivered", "completed"} and reported_mode not in modes:
            reasons.append("modality_strengthened")
        if "negated" in modes and reported_mode != "negated":
            reasons.append("negation_lost")
        if "planned" in modes and not modes & {"signed", "delivered", "completed"} and reported_mode != "planned":
            reasons.append("plan_lost")
        commitments = {f.get("commitment") for f in refs}
        if "ceiling" in commitments and numbers(text) and not any(matches(text, x) for x in dictionaries["commitment"]["ceiling"]):
            reasons.append("ceiling_lost")
        if "approval" in commitments and matches(text, "contract"):
            reasons.append("approval_as_contract")
        if any(f.get("not_disclosed") for f in refs) and not matches(text, "not disclosed"):
            reasons.append("not_disclosed_lost")
        for value in dictionaries["forecast_markers"]:
            if matches(text, value):
                reasons.append("forecast")
        for value in dictionaries["banned_words_unless_quoted"]:
            if matches(text, value) and not any(matches(f.get("quote", ""), value) for f in refs):
                reasons.append("hype")
        target = words(text)
        # The supplied writer prompt is stricter than DESIGN (12 vs 15 words).
        for fact in packet["facts"]:
            source = " ".join(words(fact.get("quote", "")))
            if any(" ".join(target[n:n+13]) in source for n in range(max(0, len(target)-12))):
                reasons.append("verbatim_13_words"); break
        claims.append({"section": block, "claim_text": text, "fact_ids": row["fact_ids"],
                       "check_status": "mismatch" if reasons else "ok", "check_codes": sorted(set(reasons))})
        codes.extend(reasons)
    return {"ok": not codes, "codes": sorted(set(codes)), "claims": claims}


class FixtureWriter:
    """Saved responses keyed by exact packet hash; no output pretending to be live."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def __call__(self, req):
        self.calls += 1
        value = self.responses.get(req["packet_hash"])
        if value is None:
            raise ValueError("WRITER_FIXTURE_MISSING")
        return deepcopy(value)
