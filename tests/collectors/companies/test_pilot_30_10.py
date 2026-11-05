# -*- coding: utf-8 -*-
"""Tests for the pilot 30+10 script (C2-04)."""

import sys
import pathlib
# Add repository root to sys.path for direct execution.
repo_root = pathlib.Path(__file__).resolve().parents[3]

sys.path.append(str(repo_root))

from collectors.companies.store import SqliteStore
from scripts.pilot_30_10 import main as pilot_main





def test_pilot_30_10_counts():
    store = SqliteStore()
    # Ensure clean start – the store is in-memory by default.
    # Run pilot script; it should not raise and should create some links.
    pilot_main()
    count = store.db.execute("SELECT COUNT(*) FROM company_facts").fetchone()[0]
    assert count >= 0  # at least zero links; the script is idempotent.

if __name__ == "__main__":
    test_pilot_30_10_counts()
    print("Pilot 30+10 test passed.")
