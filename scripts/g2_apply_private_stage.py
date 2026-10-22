#!/usr/bin/env python3
"""G2 HOLD-only PostgreSQL writer. DEFAULT: dry-run, NO database connection.

No schedule, no data in Git. --apply requires two exact approved digests
and BOHUN_G2_ADMIN_DSN, supplied privately. Migration 004 must preexist.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collectors.companies.private_profile_stage import compile_manifest,digest

SCHEMA=("profile_source","profile_claim","profile_claim_source","profile_research_case")
COMPANY="co_20261018130520ef6c8e22d3"

def insert_hold(conn,payload,cohort):
    profile=payload["profiles"][0]
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout='5s'")
        cur.execute("SET LOCAL statement_timeout='45s'")
        cur.execute("SELECT name,country FROM companies.company WHERE id=%s FOR KEY SHARE",(COMPANY,))
        c=cur.fetchone()
        if c is None or tuple(c)!=("Accurision GmbH","AT"):
            raise RuntimeError("Company record drift")
        expected=sorted((x["id"],x["name"],x["reported_country"],x["status"]) for x in cohort["records"])
        cur.execute("""SELECT id,name,reported_country,status FROM companies.discovery_candidate
                   WHERE id=ANY(%s) ORDER BY id FOR KEY SHARE""",([x[0] for x in expected],))
        if [tuple(x) for x in cur.fetchall()]!=expected:raise RuntimeError("Frozen cohort drift")
        for table in SCHEMA:
            cur.execute("SELECT to_regclass(%s)",(f"companies.{table}",))
            if cur.fetchone()[0] is None:raise RuntimeError("Migration 004 not applied")
            for role in ("anon","authenticated","service_role"):
                cur.execute("SELECT has_table_privilege(%s,%s,'SELECT')",(role,f"companies.{table}"))
                if cur.fetchone()[0]:raise PermissionError("Private table exposed to client role")
        ids={}
        for source in profile["sources"]:
            cur.execute("""INSERT INTO companies.profile_source
                 (company_id,source_ref,title,source_url,source_kind,as_of,original_bytes_archived)
                 VALUES(%s,%s,%s,%s,%s,%s,false) ON CONFLICT DO NOTHING""",
                 (COMPANY,source["id"],source["title"],source["url"],source["kind"],source["as_of"]))
            cur.execute("""SELECT id FROM companies.profile_source
                 WHERE company_id=%s AND source_ref=%s AND source_url=%s""",
                 (COMPANY,source["id"],source["url"]))
            ids[source["id"]]=cur.fetchone()[0]
        for claim in profile["claims"]:
            cur.execute("""INSERT INTO companies.profile_claim
                 (company_id,claim_ref,field_path,claim_value,assessment,review_note,content_sha256,qa_status,public_release)
                 VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s,'HOLD',false) ON CONFLICT DO NOTHING""",
                 (COMPANY,claim["claim_ref"],claim["field_path"],
                  json.dumps(claim["claim_value"],sort_keys=True,ensure_ascii=False),
                  claim["assessment"],claim["review_note"],claim["content_sha256"]))
            cur.execute("""SELECT id FROM companies.profile_claim
                 WHERE company_id=%s AND claim_ref=%s AND content_sha256=%s""",
                 (COMPANY,claim["claim_ref"],claim["content_sha256"]))
            claim_id=cur.fetchone()[0]
            for s in claim["source_refs"]:
                cur.execute("""INSERT INTO companies.profile_claim_source(company_id,claim_id,source_id)
                       VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""",(COMPANY,claim_id,ids[s]))
        for case in payload["research_cases"]:
            cur.execute("""INSERT INTO companies.profile_research_case
                     (candidate_id,research_payload,content_sha256,qa_status,public_release)
                     VALUES(%s,%s::jsonb,%s,'HOLD',false) ON CONFLICT DO NOTHING""",
                     (case["candidate_id"],json.dumps(case["research_payload"],sort_keys=True,ensure_ascii=False),case["content_sha256"]))
        # Fail transaction rather than silently return partial data.
        for table,expected_count,where,args in [
             ("profile_source",6,"company_id=%s",(COMPANY,)),
             ("profile_claim",23,"company_id=%s",(COMPANY,)),
             ("profile_claim_source",38,"company_id=%s",(COMPANY,)),
             ("profile_research_case",20,"candidate_id=ANY(%s)",([x["id"] for x in cohort["records"]],))
        ]:
            cur.execute(f"SELECT count(*) FROM companies.{table} WHERE {where}",args)
            if cur.fetchone()[0]<expected_count:raise RuntimeError("Readback incomplete: "+table)

def main():
    a=argparse.ArgumentParser()
    for key in ("profile","research","cohort","migration-file"):
        a.add_argument("--"+key,type=Path,required=True)
    a.add_argument("--apply",action="store_true")
    a.add_argument("--approved-manifest-sha")
    a.add_argument("--approved-migration-sha")
    x=a.parse_args()
    inputs=[json.loads(p.read_text(encoding="utf-8")) for p in (x.profile,x.research,x.cohort)]
    payload=compile_manifest(*inputs)
    manifest_sha=digest(payload)
    migration_sha=hashlib.sha256(x.migration_file.read_bytes()).hexdigest()
    if not x.apply:
        print(json.dumps({"mode":"DRY_RUN","sources":6,"claims":23,"cases":20,
                         "manifest_sha256":manifest_sha,"migration_sha256":migration_sha,"writes":0}))
        return
    if x.approved_manifest_sha!=manifest_sha or x.approved_migration_sha!=migration_sha:
        a.error("Need exact owner-approved manifest and SQL SHA-256")
    dsn=os.environ.get("BOHUN_G2_ADMIN_DSN")
    if not dsn:a.error("Set DB admin DSN privately; never commit credentials")
    import psycopg
    with psycopg.connect(dsn,autocommit=False) as conn:
        with conn.transaction():
            insert_hold(conn,payload,inputs[2])
    print("Committed private HOLD-only G2 pilot; no publication")

if __name__=="__main__":main()
