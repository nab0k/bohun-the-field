"""A1SD-02 cloud state: Supabase schema source_discovery <-> a run-local SQLite registry used by admission.py.

load(): state, the latest decision of every candidate, asked job keys, our company websites, known publisher hosts
and recent events of the base. save(): only what this run changed, in one transaction. Reads product tables, never
writes them (D-20261119-A1SD-CLOUD-STORE-A).
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import admission as A  # noqa: E402

EVENTS_SQL = """
with e as (
  select distinct on (lower(ev.title)) ev.id, ev.title, ev.first_published_at, t.slug topic,
    (select coalesce(json_agg(distinct lower(regexp_replace(substring(i.url from '^https?://([^/:]+)'),'^www\\.',''))), '[]')
       from knowledge.event_document d join intelligence.items i on i.id = d.item_id where d.event_id = ev.id) hosts
  from knowledge.event ev
  left join knowledge.event_topic et on et.event_id = ev.id and et.is_main
  left join knowledge.topic t on t.id = et.topic_id
  where ev.first_published_at > now() - interval '30 days' and ev.title is not null and ev.parent_event_id is null
  order by lower(ev.title), ev.id)
select id, title, topic, hosts from e order by first_published_at desc limit 300
"""
COMPANY_SQL = """
select coalesce(supply_role, '') = 'RND',
       lower(regexp_replace(substring(website from '^(?:https?://)?([^/:]+)'), '^www\\.', '')), country
from companies.company where website is not null and status is distinct from 'excluded'
"""
KNOWN_SQL = """
select distinct lower(regexp_replace(substring(url from '^https?://([^/:]+)'), '^www\\.', '')) from intelligence.items
where url is not null
union
select distinct lower(regexp_replace(substring(url from '^https?://([^/:]+)'), '^www\\.', '')) from intelligence.sources
where url is not null
"""


def connect():
    import psycopg
    return psycopg.connect(
        host=os.environ.get("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")), dbname="postgres",
        user=os.environ.get("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        password=os.environ["SUPABASE_DB_PASSWORD"], sslmode="require", connect_timeout=20)


def load(pg, sqlite_path):
    """Fill a fresh SQLite registry from the cloud state; return (conn, context)."""
    conn = A.open_db(str(sqlite_path))
    cur = pg.cursor()
    cur.execute("select channel_id, canonical_url, status, type, delivery, country, language, since, rules_version "
                "from source_discovery.source_state")
    rows = cur.fetchall()
    with A.transaction(conn):
        conn.executemany("INSERT INTO source_state VALUES (?,?,?,?,?,?,?,?,?)",
                         [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7].isoformat(), r[8]) for r in rows])
        cur.execute("select candidate_url, last_decision, decided_at, transient, channel_id "
                    "from source_discovery.candidate_seen")
        conn.executemany("INSERT INTO candidate_seen VALUES (?,?,?,?,?)",
                         [(r[0], r[1], r[2].isoformat(), r[3], r[4]) for r in cur.fetchall()])
        cur.execute("select distinct on (candidate_url) candidate_url, channel_id, decision, reasons, record, "
                    "rules_version, decided_at, found_via from source_discovery.admission_decision "
                    "order by candidate_url, decision_id desc")
        conn.executemany("INSERT INTO admission_decision(candidate_url, channel_id, decision, reasons_json, record_json,"
                         " rules_version, decided_at, found_via) VALUES (?,?,?,?,?,?,?,?)",
                         [(r[0], r[1], r[2], json.dumps(r[3]), json.dumps(r[4], ensure_ascii=False), r[5],
                           r[6].isoformat(), json.dumps(r[7], ensure_ascii=False)) for r in cur.fetchall()])
    loaded_max = conn.execute("SELECT coalesce(max(decision_id), 0) FROM admission_decision").fetchone()[0]
    cur.execute("select coalesce(jsonb_agg(distinct j), '[]') from source_discovery.cycle_run, "
                "jsonb_array_elements_text(done_jobs) j")
    done = cur.fetchone()[0] or []
    cur.execute(COMPANY_SQL)
    company, rnd, company_country = set(), set(), {}
    for is_rnd, host, country in cur.fetchall():
        if host:
            (rnd if is_rnd else company).add(host)
            company_country.setdefault(host, (country or "").strip().upper() or None)
    cur.execute(KNOWN_SQL)
    known = {h for (h,) in cur.fetchall() if h}
    # sites already seen by the Mac shadow cycles 1-5 (seed row), so the cloud does not issue them again
    cur.execute("select coalesce(jsonb_agg(distinct h), '[]') from source_discovery.cycle_run, "
                "jsonb_array_elements_text(coalesce(plan->'seed_known_hosts', '[]'::jsonb)) h")
    row = cur.fetchone()
    known |= set(row[0] if row and row[0] else [])
    # A1SD-03: what earlier runs learned, their outcome, and accepted sources by country and type (gaps first)
    cur.execute("select to_regclass('source_discovery.learning') is not null")
    learning = []
    if cur.fetchone()[0]:
        cur.execute("select at, layer, country, type_hint, new_sites, accepted from source_discovery.learning "
                    "where at > now() - interval '30 days'")
        learning = [{"at": r[0], "layer": r[1], "country": r[2], "type_hint": r[3], "new_sites": r[4],
                     "accepted": r[5]} for r in cur.fetchall()]
    cur.execute("select started_at, (measure->>'a1sd_cycle_ok')::boolean, (measure->>'accepted_share_search')::float "
                "from source_discovery.cycle_run where measure ? 'a1sd_cycle_ok' order by started_at desc limit 14")
    runs = [{"at": r[0], "ok": bool(r[1]), "share": r[2]} for r in cur.fetchall()]
    cur.execute("select country, type, count(*) from source_discovery.source_state where status='accepted' group by 1, 2")
    by_country, by_type = {}, {}
    for country, stype, n in cur.fetchall():
        by_country[country] = by_country.get(country, 0) + n
        by_type[stype] = by_type.get(stype, 0) + n
    cur.execute(EVENTS_SQL)
    events = [{"id": r[0], "title": r[1], "topic": r[2], "known_hosts": r[3] or []} for r in cur.fetchall()]
    return conn, {"done": list(done), "company": company, "rnd": rnd, "company_country": company_country,
                  "known": known, "events": events, "learning": learning, "runs": runs,
                  "accepted_by_country": by_country, "accepted_by_type": by_type,
                  "loaded_max_decision": loaded_max, "state_rows": len(rows)}


def save(pg, conn, ctx, run):
    """Write this run's decisions, changed state and the run row; one transaction."""
    from psycopg.types.json import Jsonb
    start = run["started_at"]
    new = conn.execute("SELECT candidate_url, channel_id, decision, reasons_json, record_json, rules_version, decided_at,"
                       " found_via FROM admission_decision WHERE decision_id > ?", (ctx["loaded_max_decision"],)).fetchall()
    states = conn.execute("SELECT * FROM source_state WHERE since >= ?", (start,)).fetchall()
    seen = conn.execute("SELECT * FROM candidate_seen WHERE decided_at >= ?", (start,)).fetchall()
    with pg.transaction():
        cur = pg.cursor()
        for r in new:
            cur.execute("insert into source_discovery.admission_decision(candidate_url, channel_id, decision, reasons,"
                        " record, rules_version, decided_at, found_via, run_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (r[0], r[1], r[2], Jsonb(json.loads(r[3])), Jsonb(json.loads(r[4])), r[5], r[6],
                         Jsonb(json.loads(r[7] or "null")), run["run_id"]))
        for r in states:
            cur.execute("insert into source_discovery.source_state values (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "on conflict (channel_id) do update set status=excluded.status, since=excluded.since, "
                        "rules_version=excluded.rules_version, type=excluded.type, delivery=excluded.delivery, "
                        "country=excluded.country, language=excluded.language "
                        "where source_discovery.source_state.status in ('candidate', 'on_hold') "
                        "or source_discovery.source_state.channel_id = any(%s)", (*r, run.get("audited_channels", [])))
        for r in seen:
            cur.execute("insert into source_discovery.candidate_seen values (%s,%s,%s,%s,%s) on conflict (candidate_url) "
                        "do update set last_decision=excluded.last_decision, decided_at=excluded.decided_at, "
                        "transient=excluded.transient, channel_id=excluded.channel_id", r)
        for r in run.get("learning", []):
            cur.execute("insert into source_discovery.learning(run_id, job_key, layer, engine, country, type_hint, results,"
                        " new_sites, accepted, disputed, general, held, rejected) values "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (r["run_id"], r["job_key"], r["layer"], r["engine"], r["country"], r["type_hint"], r["results"],
                         r["new_sites"], r["accepted"], r["disputed"], r["general"], r["held"], r["rejected"]))
        cur.execute("insert into source_discovery.cycle_run(run_id, started_at, finished_at, where_run, plan, trace,"
                    " measure, done_jobs) values (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run["run_id"], run["started_at"], run["finished_at"], run["where_run"], Jsonb(run["plan"]),
                     Jsonb(run["trace"]), Jsonb(run["measure"]), Jsonb(run["done_jobs"])))
    return {"decisions": len(new), "states": len(states), "seen": len(seen), "learning": len(run.get("learning", []))}
