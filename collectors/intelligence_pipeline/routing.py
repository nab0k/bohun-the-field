"""Deterministic candidate routing; literal rules do not verify event semantics."""

from dataclasses import dataclass, replace
import re
import unicodedata

from .contracts import digest
from .research_packet import SourceRecord, build_packet, _strings, _text


def _fold(value):
    return unicodedata.normalize("NFKC", value).casefold()


@dataclass(frozen=True)
class Rule:
    category: str
    keywords: tuple

    def __post_init__(self):
        _text(self.category, 128)
        _strings(self.keywords, 30)
        if not self.keywords:
            raise ValueError("Rule keywords required")

    def hits(self, text):
        return tuple(k for k in self.keywords if re.search(
            r"(?<!\w)" + re.escape(_fold(k)) + r"(?!\w)", text))


@dataclass(frozen=True)
class RuleSet:
    version: str
    rules: tuple

    def __post_init__(self):
        _text(self.version, 128)
        if not isinstance(self.rules, tuple) or not 1 <= len(self.rules) <= 20 or any(
                not isinstance(r, Rule) for r in self.rules):
            raise ValueError("Bounded immutable rules required")
        if len({r.category for r in self.rules}) != len(self.rules):
            raise ValueError("Duplicate rule category")

    @property
    def identity(self):
        # Priority order is part of configuration, not an incidental dict order.
        return digest({"version": self.version, "rules": [
            {"category": r.category, "keywords": list(r.keywords)} for r in self.rules]})


DEFAULT_RULES = RuleSet("radar-literal-v1", (
    Rule("company_capacity", ("factory", "capacity", "завод", "производство", "виробництво")),
    Rule("product_technology", ("technology", "prototype", "технология", "прототип")),
    Rule("procurement_contract", ("contract", "procurement", "контракт", "закупівля", "закупка")),
    Rule("investment", ("investment", "funding", "инвестиции", "інвестиції")),
    Rule("grant_rd", ("grant", "грант", "r&d")),
    Rule("startup", ("startup", "стартап")),
    Rule("event", ("exhibition", "conference", "выставка", "виставка", "конференція")),
    Rule("regulation", ("regulation", "export control", "регулирование", "регулювання")),
    Rule("research", ("study", "research", "исследование", "дослідження")),
))


@dataclass(frozen=True)
class RoutingDecision:
    source: SourceRecord
    categories: tuple
    matches: tuple
    sufficiency: str
    relevance: str
    disposition: str
    reasons: tuple
    receipts: tuple

    def to_dict(self):
        return {"source_key": self.source.identity, "categories": list(self.categories),
                "matches": [{"category": c, "keywords": list(k)} for c, k in self.matches],
                "sufficiency": self.sufficiency, "relevance": self.relevance,
                "disposition": self.disposition, "reasons": list(self.reasons),
                "receipts": [s.receipt() for s in self.receipts]}


@dataclass(frozen=True)
class RoutingResult:
    rules: RuleSet
    input_count: int
    decisions: tuple
    selected_keys: tuple
    packet_limit: int

    def to_dict(self):
        return {"rule_version": self.rules.version, "rule_hash": self.rules.identity,
                "input_count": self.input_count, "unique_count": len(self.decisions),
                "duplicate_count": self.input_count - len(self.decisions),
                "packet_limit": self.packet_limit, "selected_keys": list(self.selected_keys),
                "shortfall": self.packet_limit - len(self.selected_keys),
                "source_counts": {sid: sum(d.source.source_id == sid for d in self.decisions)
                                  for sid in sorted({d.source.source_id for d in self.decisions})},
                "selected_source_counts": {sid: sum(d.source.source_id == sid and d.source.identity in
                                                     self.selected_keys for d in self.decisions)
                                           for sid in sorted({d.source.source_id for d in self.decisions})},
                "decisions": [d.to_dict() for d in self.decisions]}

    def packets(self):
        by_key = {d.source.identity: d for d in self.decisions}
        return tuple(build_packet(by_key[k].source, by_key[k].categories,
                                  self.rules.version, self.rules.identity,
                                  ("What does the source establish and what remains unverified?",
                                   "What independent evidence, alternatives and prior state are missing?"),
                                  receipts=by_key[k].receipts,
                                  unknowns=("INDEPENDENT_CORROBORATION_NOT_RETRIEVED",
                                            "CATEGORY_IS_LITERAL_CANDIDATE_ONLY"))
                     for k in self.selected_keys)


def route_batch(sources, *, rules=DEFAULT_RULES, packet_limit=5):
    """Bounded supplied batch only. Exact versions dedupe; fuzzy stories do not."""
    if not isinstance(sources, (tuple, list)) or len(sources) > 50 or any(
            not isinstance(s, SourceRecord) for s in sources):
        raise ValueError("At most 50 supplied SourceRecords required")
    if not isinstance(rules, RuleSet) or type(packet_limit) is not int or not 0 <= packet_limit <= 5:
        raise ValueError("Explicit rules and packet limit 0–5 required")
    grouped, versions = {}, {}
    for source in sources:
        grouped.setdefault(source.identity, []).append(source)
        versions.setdefault(source.version_key, set()).add(source.identity)
    decisions = []
    for key in sorted(grouped):
        receipts = tuple(sorted(grouped[key], key=lambda s: (s.receipt()["received_at"], s.item_id, s.url)))
        source = receipts[0]
        text = source.title
        if source.rights == "PERMITTED" and not source.sensitive and source.text:
            text += "\n" + source.text
        matches = tuple((r.category, hits) for r in rules.rules if (hits := r.hits(_fold(text))))
        categories = tuple(c for c, _ in matches)
        reasons = list(source.blockers)
        conflict = len(versions[source.version_key]) > 1
        if conflict:
            reasons.append("SOURCE_VERSION_CONFLICT")
        if not categories:
            reasons.append("NO_RULE_MATCH")
        else:
            reasons.append("LITERAL_RULE_MATCH; NOT_SEMANTIC_VERIFICATION")
        if conflict or source.sensitive or source.rights == "DENIED":
            disposition = "HOLD"
        elif source.blockers and categories:
            disposition = "VERIFY_SOURCE"
        elif not categories:
            disposition = "SOURCE_UPDATE"
        else:
            disposition = "RESEARCH_CANDIDATE"
        decisions.append(RoutingDecision(source, categories, matches,
                                         "BLOCKED" if conflict or source.blockers else "SUFFICIENT_FOR_PACKET",
                                         "CANDIDATE" if categories else "UNKNOWN",
                                         disposition, tuple(reasons), receipts))
    # Round-robin explicit category priority; stable identities break ties.
    queues = [[d.source.identity for d in decisions if d.disposition == "RESEARCH_CANDIDATE"
               and r.category in d.categories] for r in rules.rules]
    selected = []
    while len(selected) < packet_limit:
        added = False
        for queue in queues:
            while queue and queue[0] in selected:
                queue.pop(0)
            if queue and len(selected) < packet_limit:
                selected.append(queue.pop(0))
                added = True
        if not added:
            break
    decisions = [replace(d, reasons=d.reasons + (("SELECTED_WITHIN_LIMIT" if d.source.identity in selected
                  else "DEFERRED_PACKET_LIMIT"),)) if d.disposition == "RESEARCH_CANDIDATE" else d
                 for d in decisions]
    return RoutingResult(rules, len(sources), tuple(decisions), tuple(selected), packet_limit)
