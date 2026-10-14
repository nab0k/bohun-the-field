"""Rules-first relevance classification with explicit rule IDs."""

import json
from pathlib import Path


def load_rules(path=None):
    path = Path(path or Path(__file__).with_name("relevance_rules_v0.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def classify(title, rules=None):
    rules = rules or load_rules()
    text = (title or "").casefold()
    negatives = [term for term in rules["exclude"] if term.casefold() in text]
    signals = [(signal, term) for signal, terms in rules["signals"].items() for term in terms if term.casefold() in text]
    regions = [(region, term) for region, terms in rules["regions"].items() for term in terms if term.casefold() in text]
    rule_ids = [f"exclude:{term}" for term in negatives]
    if negatives and not signals:
        return {"relevant": "no", "signal": "", "region": "", "rule_ids": "|".join(rule_ids)}
    if signals:
        signal = signals[0][0]
        rule_ids.extend(f"signal:{name}:{term}" for name, term in signals)
        rule_ids.extend(f"region:{name}:{term}" for name, term in regions)
        return {"relevant": "yes", "signal": signal, "region": regions[0][0] if regions else "global", "rule_ids": "|".join(rule_ids)}
    return {"relevant": "unsure", "signal": "", "region": regions[0][0] if regions else "", "rule_ids": "|".join(f"region:{name}:{term}" for name, term in regions)}


def classify_clusters(clusters, rules=None):
    return [{**row, **classify(row.get("title", ""), rules)} for row in clusters]

