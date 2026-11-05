"""Auto‑record new companies (C2‑05)

This module provides a single public function ``auto_record_new_companies`` that
receives a :class:`~collectors.companies.store.SqliteStore` instance and a list of
observations.  Each observation is a mapping with at least the keys ``name`` and
optionally ``country``, ``city`` and ``website``.  The function creates a company
record for every observation that is not already present in the store –
idempotent handling of duplicates is guaranteed by checking the normalized name
and the country.

The implementation re‑uses the existing ``SqliteStore.create_company`` helper
and the ``name_norm`` normaliser from ``collectors.companies.normalize``.
"""

from __future__ import annotations

from typing import Iterable, Mapping, List

from .store import SqliteStore
from .normalize import name_norm


def _company_exists(store: SqliteStore, name: str, country: str | None) -> bool:
    """Return ``True`` if a company with the given *name* (normalized) and *country*
    already exists in the store.

    The underlying ``companies_by_name`` query matches both the canonical name
    and any existing aliases.
    """
    norm = name_norm(name)
    return bool(store.companies_by_name(norm, country))


def auto_record_new_companies(
    store: SqliteStore,
    observations: Iterable[Mapping[str, object]],
) -> List[str]:
    """Create missing companies from *observations*.

    Parameters
    ----------
    store:
        The SQLite store used by the pipeline.
    observations:
        An iterable of dictionaries.  Expected keys are ``name`` (required) and
        optional ``country``, ``city`` and ``website``.  Additional keys are
        ignored.

    Returns
    -------
    list[str]
        A list of company IDs that were newly created (order of creation).
    """
    created_ids: List[str] = []
    for obs in observations:
        name = obs.get("name")
        if not isinstance(name, str) or not name:
            # Skip malformed entries – they are not part of the AC.
            continue
        country = obs.get("country")
        city = obs.get("city")
        website = obs.get("website")
        if _company_exists(store, name, country):
            # Duplicate – nothing to do (idempotent).
            continue
        cid = store.create_company(
            name=name,
            country=country,
            city=city,
            website=website,
        )
        created_ids.append(cid)
    return created_ids
"""
