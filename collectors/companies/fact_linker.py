# -*- coding: utf-8 -*-
"""Fact linking module for C2-03.

This module loads factual entries (e.g. from `atlas/undersea.json`) and
attempts to associate each fact with an existing company record in the
SQLite store.  The association is based on a normalized name match and, as
a fallback, a simple fuzzy match (Levenshtein distance ≤ 2, implemented via
`difflib.SequenceMatcher`).

The public API consists of three functions:

* ``load_facts()`` – returns a list of dicts with keys ``id``, ``name``,
  ``country`` (optional) and ``source_url``.
* ``match_fact(store, fact)`` – returns ``(company_id, confidence)`` where
  ``confidence`` is a float in ``[0, 1]`` (1.0 for an exact match).
* ``link_facts(store)`` – iterates over all facts, finds a match and stores
  the link via ``store.add_fact_link``.

The module deliberately avoids any network access and works entirely on the
local repository data, complying with the BOHUN‑local sandbox rules.
"""

import json
import pathlib
import difflib
from typing import List, Tuple, Optional

from .store import SqliteStore
from .normalize import name_norm

# Path to the undersea facts JSON (relative to the repository root).
_UNDERSEA_PATH = pathlib.Path(__file__).resolve().parents[3] / "bohundefense-atmo22" / "atlas" / "undersea.json"


def _generate_fact_id(fact: dict) -> str:
    """Generate a deterministic fact identifier.

    The identifier is a short hash of the fact's name and source URL –
    sufficient for uniqueness within the undersea dataset.
    """
    import hashlib

    base = f"{fact.get('name','')}|{fact.get('url','')}"
    return "fact_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]


def load_facts() -> List[dict]:
    """Load facts from the local ``undersea.json`` file.

    Returns a list of dictionaries with the minimal fields required for
    linking:

    * ``id`` – deterministic fact identifier.
    * ``name`` – the company name as it appears in the source.
    * ``country`` – optional ISO‑2 country code (may be missing).
    * ``source_url`` – URL of the original article.
    """
    try:
        data = json.loads(_UNDERSEA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(f"Fact source not found: {_UNDERSEA_PATH}")

    facts = []
    for entry in data.get("companies", []):
        fact_id = _generate_fact_id(entry)
        facts.append(
            {
                "id": fact_id,
                "name": entry.get("name", ""),
                "country": entry.get("country"),  # may be None
                "source_url": entry.get("url", ""),
            }
        )
    return facts


def _fuzzy_match(candidate: str, target: str) -> bool:
    """Return True if the Levenshtein distance is ≤ 2.

    ``difflib.SequenceMatcher`` does not give a distance directly, so we
    approximate by requiring a similarity ratio ≥ 0.85 for typical short
    strings (empirically matches distance ≤ 2 for names ≤ 20 chars).
    """
    ratio = difflib.SequenceMatcher(None, candidate, target).ratio()
    return ratio >= 0.85


def match_fact(store: SqliteStore, fact: dict) -> Tuple[Optional[str], float]:
    """Attempt to match a fact to a company.

    *Exact match* – normalized name equals a stored name (or alias) and the
    country matches (or both are ``None``). Confidence = 1.0.

    *Fuzzy match* – same as above but using a similarity fallback; confidence
    = 0.7.

    Returns ``(company_id, confidence)`` or ``(None, 0.0)`` if no match.
    """
    norm = name_norm(fact["name"]).lower()
    if not norm:
        return None, 0.0

    # Exact lookup via the store helper.
    exact = store.companies_by_name(norm, fact.get("country"))
    if exact:
        return exact[0], 1.0

    # Fallback: iterate over all companies and apply fuzzy matching.
    # This is acceptable for the small undersea dataset.
    cursor = store.db.execute(
        "SELECT id, name_norm, country FROM company"
    )
    for row in cursor:
        if fact.get("country") and row["country"] != fact["country"]:
            continue
        if _fuzzy_match(row["name_norm"], norm):
            return row["id"], 0.7
    return None, 0.0


def link_facts(store: SqliteStore) -> None:
    """Load all facts, match them, and store the links.

    The function is idempotent – ``INSERT OR IGNORE`` in ``add_fact_link``
    prevents duplicate rows.
    """
    facts = load_facts()
    for f in facts:
        company_id, confidence = match_fact(store, f)
        if company_id:
            store.add_fact_link(
                company_id=company_id,
                fact_id=f["id"],
                source_url=f["source_url"],
                confidence=confidence,
            )

if __name__ == "__main__":
    # Simple manual execution for debugging.
    s = SqliteStore()
    link_facts(s)
    print(f"Linked {len(s.db.execute('SELECT COUNT(*) FROM company_facts').fetchone())} facts")
