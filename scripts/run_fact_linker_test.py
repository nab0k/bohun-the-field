# -*- coding: utf-8 -*-
"""Run fact linking sanity checks without external pytest.
This script mimics the test suite defined in
`tests/collectors/companies/test_fact_linker.py` but executes the checks
directly using plain assertions. It is safe to run in the sandbox and
does not require any third‑party packages.
"""

import sys
import pathlib
# Add repository root to sys.path so that imports from 'collectors' work when the script is executed directly.
repo_root = pathlib.Path(__file__).resolve().parents[1]

sys.path.append(str(repo_root))

from collectors.companies.store import SqliteStore
from collectors.companies.fact_linker import load_facts, match_fact, link_facts



def _setup_store_with_companies():
    store = SqliteStore()
    store.create_company(name="Unseenlabs", country="FR", city="Rennes")
    store.create_company(name="SomeOtherCo", country="US", city="New York")
    return store


def test_load_facts_structure():
    facts = load_facts()
    assert isinstance(facts, list), "load_facts should return a list"
    assert facts, "undersea.json must contain at least one fact"
    for f in facts:
        assert "id" in f and "name" in f and "source_url" in f, "missing keys"


def test_match_fact_exact():
    store = _setup_store_with_companies()
    fact = {"id": "fact_test", "name": "Unseenlabs", "country": "FR", "source_url": "https://example.com"}
    company_id, confidence = match_fact(store, fact)
    assert company_id is not None, "Exact match should find a company"
    assert confidence == 1.0, "Exact match confidence must be 1.0"


def test_match_fact_fuzzy():
    store = _setup_store_with_companies()
    fact = {"id": "fact_test", "name": "Unseelabs", "country": "FR", "source_url": "https://example.com"}
    company_id, confidence = match_fact(store, fact)
    assert company_id is not None, "Fuzzy match should find a company"
    assert 0.6 < confidence < 1.0, "Fuzzy confidence range"


def test_link_facts_idempotent():
    store = SqliteStore()
    first_fact = load_facts()[0]
    store.create_company(name=first_fact["name"], country=first_fact.get("country"))
    link_facts(store)
    cnt1 = store.db.execute("SELECT COUNT(*) FROM company_facts").fetchone()[0]
    assert cnt1 > 0, "At least one link should be created"
    link_facts(store)
    cnt2 = store.db.execute("SELECT COUNT(*) FROM company_facts").fetchone()[0]
    assert cnt2 == cnt1, "Links must be idempotent"


if __name__ == "__main__":
    test_load_facts_structure()
    test_match_fact_exact()
    test_match_fact_fuzzy()
    test_link_facts_idempotent()
    print("All fact linking checks passed.")
