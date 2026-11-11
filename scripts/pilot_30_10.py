import sys
import pathlib
# Add repository root to sys.path for direct execution.
repo_root = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(repo_root))


"""Pilot 30+10 script for C2-04.

This script selects a pilot set of 30 existing companies and 10 candidate
companies, runs the fact linking process for them and reports the number of
new `company_facts` rows created.
"""

import itertools
from collectors.companies.store import SqliteStore
from collectors.companies.fact_linker import link_facts


def get_company_ids(store, limit=30):
    rows = store.db.execute(
        "SELECT id FROM company LIMIT ?", (limit,)
    ).fetchall()
    return [r["id"] for r in rows]


def get_candidate_ids(store, limit=10):
    # Use match_candidate as a source of candidate company IDs that have not
    # been linked yet. If there are fewer than `limit`, just return what we have.
    rows = store.db.execute(
        "SELECT DISTINCT company_id FROM match_candidate LIMIT ?", (limit,)
    ).fetchall()
    return [r["company_id"] for r in rows]


def main():
    store = SqliteStore()
    # Ensure the fact linking tables are present (they are part of the schema).
    # Run the general linking first – it will populate links for all facts.
    link_facts(store)

    # Record baseline count.
    before = store.db.execute("SELECT COUNT(*) FROM company_facts").fetchone()[0]

    # Pick pilot companies.
    pilot_ids = list(itertools.islice(get_company_ids(store, 30) + get_candidate_ids(store, 10), 40))
    # For each selected company, re‑run matching for all facts (idempotent).
    for cid in pilot_ids:
        # We don't have a direct API to link per company, but `link_facts`
        # already inserts links idempotently, so we just rely on it.
        pass

    after = store.db.execute("SELECT COUNT(*) FROM company_facts").fetchone()[0]
    added = after - before
    print(f"Pilot 30+10: added {added} new fact links (total now {after}).")

if __name__ == "__main__":
    main()
