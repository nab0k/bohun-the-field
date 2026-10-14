"""Rules-first relevance classification with explicit rule IDs."""

import json
from pathlib import Path


def load_rules(path=None):
    path = Path(path or Path(__file__).with_name("relevance_rules_v0.json"))
    return json.loads(path.read_text(encoding="utf-8"))


GENERALIST_HINTS = {"capital_investments", "vc", "market", "startup", "business", "news"}


def classify(title, rules=None, specialist=True):
    """specialist=False for general business/startup feeds: a signal word alone is not enough there,
    the title must also name the defence/dual-use domain (otherwise every startup round counts)."""
    rules = rules or load_rules()
    text = (title or "").casefold()
    negatives = [term for term in rules["exclude"] if term.casefold() in text]
    signals = [(signal, term) for signal, terms in rules["signals"].items() for term in terms if term.casefold() in text]
    regions = [(region, term) for region, terms in rules["regions"].items() for term in terms if term.casefold() in text]
    rule_ids = [f"exclude:{term}" for term in negatives]
    if negatives and not signals:
        return {"relevant": "no", "signal": "", "region": "", "rule_ids": "|".join(rule_ids)}
    domain = [term for term in rules.get("domain", []) if term.casefold() in text]
    if signals and not specialist and not domain:
        return {"relevant": "unsure", "signal": signals[0][0], "region": regions[0][0] if regions else "",
                "rule_ids": "|".join(rule_ids + [f"signal:{n}:{t}" for n, t in signals] + ["gate:no_domain_term"])}
    if signals:
        signal = signals[0][0]
        rule_ids.extend(f"domain:{term}" for term in domain[:3])
        rule_ids.extend(f"signal:{name}:{term}" for name, term in signals)
        rule_ids.extend(f"region:{name}:{term}" for name, term in regions)
        return {"relevant": "yes", "signal": signal, "region": regions[0][0] if regions else "global", "rule_ids": "|".join(rule_ids)}
    return {"relevant": "unsure", "signal": "", "region": regions[0][0] if regions else "", "rule_ids": "|".join(f"region:{name}:{term}" for name, term in regions)}


def classify_clusters(clusters, rules=None, specialist_feeds=None):
    """specialist_feeds: set of feed_ids from defence-specialist sources; None = treat all as specialist."""
    rules = rules or load_rules()
    def is_specialist(row):
        if specialist_feeds is None:
            return True
        feeds = set(filter(None, str(row.get("feeds", "")).split("|"))) or {row.get("first_seen_feed", "")}
        return bool(feeds & specialist_feeds)
    return [{**row, **classify(row.get("title", ""), rules, specialist=is_specialist(row))} for row in clusters]

