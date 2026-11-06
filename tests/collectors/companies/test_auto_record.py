"""Tests for auto_record_new_companies (C2‑05).

The test creates an in‑memory store, feeds a few mock observations and checks that
new companies are inserted while duplicates are ignored.
"""

from typing import List, Mapping

import pytest

from collectors.companies.store import SqliteStore
from collectors.companies.auto_record import auto_record_new_companies

@pytest.fixture
def store() -> SqliteStore:
    return SqliteStore()


def test_auto_record_basic(store: SqliteStore) -> None:
    observations: List[Mapping[str, object]] = [
        {"name": "Acme Corp", "country": "US", "website": "https://acme.example"},
        {"name": "Beta Ltd", "country": "UK"},
        # Duplicate of the first company – should be ignored.
        {"name": "Acme Corp", "country": "US"},
    ]

    created = auto_record_new_companies(store, observations)
    # Two distinct companies should be created.
    assert len(created) == 2

    # Verify they exist via lookup.
    ids = store.companies_by_name("acme corp", "US")
    assert len(ids) == 1
    ids2 = store.companies_by_name("beta ltd", "UK")
    assert len(ids2) == 1

    # Adding the same observations again must not create new rows.
    created_again = auto_record_new_companies(store, observations)
    assert created_again == []
