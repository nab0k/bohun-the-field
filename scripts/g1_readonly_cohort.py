#!/usr/bin/env python3
"""G1: read 20 frozen Eurosatory candidates using existing EXP-012 PgStore.
Read-only PostgreSQL transaction; no imports, upserts, release, or uploads.
Output is private and must be kept outside the repository.
"""
import argparse
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from collectors.companies.pg_store import PgStore

IDS = [1,2,3,4,5,6,8,9,24,25,46,52,55,59,167,203,1203,1913,229,734]
SQL = """SELECT d.id, d.name, d.reported_country, d.status,
 d.matched_company_id, o.source
 FROM companies.discovery_candidate d
 JOIN companies.observation o ON o.id=d.observation_id
 WHERE d.id=ANY(%s) ORDER BY d.id"""

def read_cohort(db):
    db.execute("SET TRANSACTION READ ONLY")
    rows=[dict(r) for r in db.execute(SQL, (IDS,)).fetchall()]
    if [r["id"] for r in rows] != sorted(IDS):
        raise RuntimeError("Cohort drift; expected exactly 20 source records")
    if any(r["source"] != "eurosatory_2024_exhibitors" for r in rows):
        raise RuntimeError("Source drift; fail closed")
    expected={"discovered":12,"candidate_existing":6,"hold":2}
    counts={k:sum(r["status"]==k for r in rows) for k in expected}
    if counts != expected:
        raise RuntimeError("Status drift; manual review required")
    return {"schema":"bohun-g1-cohort20-readonly-v1",
            "source_event":"eurosatory_2024_exhibitors",
            "records":[{k:r[k] for k in ("id","name","reported_country","status","matched_company_id")}
                       for r in rows],
            "public_release":False,"qa":"HOLD"}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-out", type=pathlib.Path, required=True)
    args=parser.parse_args()
    destination=args.private_out.expanduser().resolve()
    repo=pathlib.Path(__file__).resolve().parents[1]
    if destination.is_relative_to(repo):
        parser.error("Output must be outside Git checkout")
    if destination.exists():
        parser.error("Refusing to overwrite existing snapshot")
    store=PgStore()
    try:
        data=read_cohort(store.db)
    finally:
        store.db.rollback()
        store.close()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    destination.chmod(0o600)
    print("G1 private SELECT: 20 records; 0 writes; HOLD")

if __name__=="__main__":
    main()
