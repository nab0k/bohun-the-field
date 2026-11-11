"""C4-11 one-off backtest of the signal method v1. Read-only database access; models see masked text only."""
import json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from collectors.companies.pg_store import connect_params

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
GROQ = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
MAX_REQUESTS, GAP, WAIT = 60, 13, 30
STRONG = {"contract", "approval", "funded", "delivered"}

WRITER = """You apply a defence-market signal method to ONE hypothesis. You see only dated evidence rows published before a cutoff. Names are masked; do not guess them and use nothing from memory.
Steps: (1) restate the hypothesis about one object; (2) compare like with like only; (3) find a baseline in the rows: the earlier state or value; (4) state the change and its direction; (5) separate events from repeated reports of one event; (6) check alternatives: normal cycle, one-off event, reporting artefact, interested source, echo, plan presented as fact; (7) say what later event would confirm or refute it.
Status rules: QUALIFIED only if the rows already show the change (not just plans, intentions or one announcement) AND either one strong dated event (contract, approval, funding, delivery) that itself is the change, or at least two independent events. MONITORING if plausible but not yet shown. REJECTED if the rows contradict it or show no change.
Answer JSON only: {"status":"QUALIFIED|MONITORING|REJECTED","route":"A|B|none","baseline":"text or null","change":"text or null","evidence_ids":[row ids],"independent_events":<int>,"alternatives_open":[numbers 1-6 still not excluded],"confirm_if":"text","refute_if":"text","reason":"two sentences at most"}"""

CHECKER = """You check a proposed defence-market signal strictly against the rows given. Names are masked; use nothing from memory. Answer JSON only:
{"supported":true or false,"overstated":true or false,"plan_as_fact":true or false,"independent_events":<count of separate real events in the cited rows>,"reason":"at most two sentences"}"""


def post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "bohun-backtest/1.0", **headers})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def gemini(key, system, user):
    reply = post(GEMINI, {"x-goog-api-key": key}, {"systemInstruction": {"parts": [{"text": system}]},
                 "contents": [{"role": "user", "parts": [{"text": user}]}],
                 "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2000, "responseMimeType": "application/json"}})
    return "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])


def groq(key, system, user):
    reply = post(GROQ, {"Authorization": "Bearer " + key}, {"model": GROQ_MODEL, "temperature": 0.1, "max_tokens": 1500,
                 "response_format": {"type": "json_object"},
                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    return reply["choices"][0]["message"]["content"]


class Budget:
    def __init__(self, keys):
        self.keys, self.used, self.log = keys, 0, []

    def ask(self, order, system, user):
        for name in order:
            for _ in range(2):
                if self.used >= MAX_REQUESTS:
                    return None, "budget"
                self.used += 1
                try:
                    out = json.loads((gemini if name == "gemini" else groq)(self.keys[name], system, user))
                    self.log.append(name + ":ok")
                    time.sleep(GAP)
                    return out, name
                except urllib.error.HTTPError as exc:
                    self.log.append(f"{name}:{exc.code}")
                    time.sleep(WAIT if exc.code in (429, 500, 502, 503, 504) else 1)
                    if exc.code not in (429, 500, 502, 503, 504):
                        break
                except Exception as exc:
                    self.log.append(f"{name}:{type(exc).__name__}")
        return None, "failed"


def mask(text, mapping):
    for key in sorted(mapping, key=len, reverse=True):
        text = re.sub(r"(?<![\w-])" + re.escape(key) + r"(?![\w-])", mapping[key], text)
    return text


def rows_for(conn, case):
    sql = """select e.id, e.occurred_on::text d, coalesce(e.commitment,'') s,
                    regexp_replace(e.what,'^\\d{4}-\\d{2}-\\d{2} \\(\\w+\\): ','') w,
                    regexp_replace(split_part(split_part(coalesce(e.source_url,''),'://',2),'/',1),'^www\\.','') h
             from knowledge.edge e
             where coalesce(e.src_node_id,e.dst_node_id) = any(%s) and e.status='verified'
               and e.occurred_on is not null and e.occurred_on < %s::date
             order by e.occurred_on, e.id"""
    return conn.execute(sql, (case["node"], case["cutoff"])).fetchall()


def decide(writer, checker, rows_by_id):
    """Code sets the final status; models only propose and check."""
    if not isinstance(writer, dict) or writer.get("status") not in ("QUALIFIED", "MONITORING", "REJECTED"):
        return "MONITORING", "writer_unavailable"
    if writer["status"] != "QUALIFIED":
        return writer["status"], "writer"
    ids = [i for i in writer.get("evidence_ids") or [] if i in rows_by_id]
    if not ids:
        return "MONITORING", "no_valid_evidence_ids"
    if not isinstance(checker, dict):
        return "MONITORING", "checker_unavailable"
    if not checker.get("supported") or checker.get("overstated") or checker.get("plan_as_fact"):
        return "MONITORING", "checker_rejected"
    strong = [i for i in ids if rows_by_id[i]["s"] in STRONG]
    events = {(rows_by_id[i]["d"], rows_by_id[i]["s"]) for i in ids}
    if writer.get("route") == "A" and strong:
        return "QUALIFIED", "route_A"
    if len(events) >= 2 and int(checker.get("independent_events") or 0) >= 2:
        return "QUALIFIED", "route_B"
    return "MONITORING", "route_not_met"


def main():
    import psycopg
    from psycopg.rows import dict_row
    keys = {"gemini": os.environ["GEMINI_FREE_API_KEY"], "groq": os.environ["GROQ_API_KEY"]}
    cases = json.load(open(Path(__file__).with_name("cases_public.json")))["cases"]
    budget, out = Budget(keys), []
    with psycopg.connect(**connect_params(), row_factory=dict_row) as conn:
        conn.execute("set default_transaction_read_only = on")
        conn.execute("set transaction read only")
        for case in cases:
            rows = rows_for(conn, case)
            hosts, rows_by_id, lines = {}, {}, []
            for n, r in enumerate(rows, 1):
                hid = hosts.setdefault(r["h"], f"source-{len(hosts) + 1}")
                rows_by_id[n] = {"d": r["d"], "s": r["s"]}
                lines.append({"id": n, "date": r["d"], "status": r["s"], "source": hid, "text": mask(r["w"][:400], case["mask"])})
            hyp = mask(case["hypothesis"], case["mask"])
            packet = json.dumps({"hypothesis": hyp, "cutoff": case["cutoff"], "rows": lines}, ensure_ascii=False)
            writer, wby = budget.ask(["gemini", "groq"], WRITER, packet)
            checker, cby = None, None
            if isinstance(writer, dict) and writer.get("status") == "QUALIFIED":
                cited = [l for l in lines if l["id"] in (writer.get("evidence_ids") or [])]
                checker, cby = budget.ask(["groq", "gemini"], CHECKER,
                                          json.dumps({"hypothesis": hyp, "proposal": writer, "rows": cited}, ensure_ascii=False))
            status, why = decide(writer, checker, rows_by_id)
            out.append({"id": case["id"], "rows": len(rows), "masked_hypothesis": hyp, "writer_by": wby, "writer": writer,
                        "checker_by": cby, "checker": checker, "status": status, "why": why,
                        "leak_check": [k for k in case["mask"] if any(k in l["text"] for l in lines)]})
            print(case["id"], len(rows), status, why)
    Path(sys.argv[1]).write_text(json.dumps({"requests": budget.used, "log": budget.log, "results": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("requests", budget.used)


if __name__ == "__main__":
    main()
