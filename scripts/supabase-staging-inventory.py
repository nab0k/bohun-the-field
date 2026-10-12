#!/usr/bin/env python3
"""Read-only Supabase inventory for an isolated Bohun staging design.

Outputs schema metadata and privileges only. It never selects business rows and
forces PostgreSQL's default_transaction_read_only setting for the connection.
"""
import json,os
from pathlib import Path
import psycopg

OUT=Path("output/supabase-staging")
OUT.mkdir(parents=True,exist_ok=True)

with psycopg.connect(
    host=os.getenv("SUPABASE_DB_HOST","aws-1-eu-central-1.pooler.supabase.com"),
    port=int(os.getenv("SUPABASE_DB_PORT","5432")),
    dbname="postgres",
    user=os.getenv("SUPABASE_DB_USER","postgres.tecrucawzukxqtchpxrh"),
    password=os.environ["SUPABASE_DB_PASSWORD"],
    sslmode="require",connect_timeout=20,
    options="-c default_transaction_read_only=on",
) as db:
    with db.cursor() as cur:
        cur.execute("""SELECT table_schema,table_name
          FROM information_schema.tables
          WHERE table_type='BASE TABLE'
            AND table_schema NOT IN ('pg_catalog','information_schema')
          ORDER BY table_schema,table_name""")
        tables=[{"schema":a,"table":b} for a,b in cur.fetchall()]
        cur.execute("""SELECT table_schema,table_name,column_name,data_type,is_nullable
          FROM information_schema.columns
          WHERE table_schema NOT IN ('pg_catalog','information_schema')
          ORDER BY table_schema,table_name,ordinal_position""")
        columns=[{"schema":a,"table":b,"column":c,"type":d,"nullable":e=="YES"}
                 for a,b,c,d,e in cur.fetchall()]
        cur.execute("""SELECT current_user,current_database(),
          has_database_privilege(current_user,current_database(),'CREATE'),
          has_schema_privilege(current_user,'intelligence','USAGE'),
          has_schema_privilege(current_user,'intelligence','CREATE'),
          EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name='bohun_staging')""")
        user,database,db_create,intel_usage,intel_create,staging_exists=cur.fetchone()

report={
  "mode":"READ_ONLY_METADATA_INVENTORY",
  "current_user":user,"database":database,
  "privileges":{"database_create":db_create,"intelligence_usage":intel_usage,"intelligence_create":intel_create},
  "bohun_staging_schema_exists":staging_exists,
  "tables":tables,"columns":columns,
  "business_rows_read":0,"writes":0,
}
(OUT/"inventory.json").write_text(json.dumps(report,indent=2))
print(json.dumps({"mode":report["mode"],"tables":len(tables),"columns":len(columns),
                  "bohun_staging_schema_exists":staging_exists,
                  "privileges":report["privileges"],"business_rows_read":0,"writes":0}))
