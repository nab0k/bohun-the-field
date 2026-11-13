"""C4-04 one-off: cross-check signal claims against our news base and company registry. Read-only."""
import json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from collectors.companies.pg_store import connect_params

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
GROQ = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
MAX_REQUESTS, GAP, WAIT, MIN_QUOTE = 40, 13, 30, 15

SYSTEM = """You cross-check ONE claim about the defence market against dated rows from our news base and company registry. Use only the rows; nothing from memory.
Answer CONTRADICTED only if one row directly states something that cannot be true at the same time as the claim (different number, opposite outcome, different status at that date). A row that is silent, older, or about a different object is NOT a contradiction.
Answer CONSISTENT if at least one row supports the claim and none contradicts it. Otherwise UNKNOWN.
The quote must be copied exactly from the text of the row you cite.
Answer JSON only: {"verdict":"CONTRADICTED|CONSISTENT|UNKNOWN","row_id":<id or null>,"quote":"exact words from that row or empty","reason":"one sentence"}"""


def post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "bohun-crosscheck/1.0", **headers})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def gemini(key, user):
    reply = post(GEMINI, {"x-goog-api-key": key}, {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                 "contents": [{"role": "user", "parts": [{"text": user}]}],
                 "generationConfig": {"temperature": 0.1, "maxOutputTokens": 800, "responseMimeType": "application/json"}})
    return "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])


def groq(key, user):
    reply = post(GROQ, {"Authorization": "Bearer " + key}, {"model": GROQ_MODEL, "temperature": 0.1, "max_tokens": 800,
                 "response_format": {"type": "json_object"},
                 "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]})
    return reply["choices"][0]["message"]["content"]


def norm(text):
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def judge(answer, rows):
    """Code accepts a contradiction only with a real quote from the cited row."""
    if not isinstance(answer, dict) or answer.get("verdict") not in ("CONTRADICTED", "CONSISTENT", "UNKNOWN"):
        return "UNKNOWN", "no_valid_answer"
    verdict = answer["verdict"]
    if verdict == "UNKNOWN":
        return verdict, "model"
    row = rows.get(answer.get("row_id"))
    quote = norm(answer.get("quote"))
    if row is None or len(quote) < MIN_QUOTE or quote not in norm(row["text"]):
        return "UNKNOWN", "quote_not_in_cited_row"
    return verdict, "quote_verified"


def main():
    import psycopg
    from psycopg.rows import dict_row
    keys = {"gemini": os.environ["GEMINI_FREE_API_KEY"], "groq": os.environ["GROQ_API_KEY"]}
    claims = json.load(open(Path(__file__).with_name("claims_public.json")))["claims"]
    used, log, out = 0, [], []
    with psycopg.connect(**connect_params(), row_factory=dict_row) as conn:
        conn.execute("set default_transaction_read_only = on")
        for claim in claims:
            rows = conn.execute("""select e.occurred_on::text d, coalesce(e.commitment,'') s,
                       regexp_replace(e.what,'^\\d{4}-\\d{2}-\\d{2} \\(\\w+\\): ','') w, coalesce(e.quote,'') q,
                       regexp_replace(split_part(split_part(coalesce(e.source_url,''),'://',2),'/',1),'^www\\.','') h
                from knowledge.edge e where coalesce(e.src_node_id,e.dst_node_id) = any(%s) and e.status='verified'
                order by e.occurred_on nulls first, e.id""", (claim["node"],)).fetchall()
            table = {n: {"date": r["d"], "status": r["s"], "source": r["h"], "text": (r["w"] + (" | " + r["q"] if r["q"] else ""))[:700]}
                     for n, r in enumerate(rows, 1)}
            names = sorted({m for m in re.findall(r"\b[A-Z][A-Za-z0-9&.-]+(?: [A-Z][A-Za-z0-9&.-]+)*", claim["claim"]) if len(m) > 3})
            registry = conn.execute("""select c.name, c.country, c.supply_role, c.status from companies.company c
                where c.name_norm = any(%s) or c.id in (select company_id from companies.company_alias where alias_norm = any(%s)) limit 5""",
                                    ([n.casefold() for n in names], [n.casefold() for n in names])).fetchall()
            for reg in registry:
                n = len(table) + 1
                table[n] = {"date": None, "status": "registry", "source": "company registry",
                            "text": f"{reg['name']}: country {reg['country']}, role {reg['supply_role']}, status {reg['status']}"}
            user = json.dumps({"claim": claim["claim"], "rows": [{"id": k, **v} for k, v in table.items()]}, ensure_ascii=False)
            answer, by = None, None
            for name, call, key in (("gemini", gemini, keys["gemini"]), ("groq", groq, keys["groq"])):
                for _ in range(2):
                    if answer is not None or used >= MAX_REQUESTS:
                        break
                    used += 1
                    try:
                        answer, by = json.loads(call(key, user)), name
                        log.append(name + ":ok")
                    except urllib.error.HTTPError as exc:
                        log.append(f"{name}:{exc.code}")
                        time.sleep(WAIT)
                        if exc.code not in (429, 500, 502, 503, 504):
                            break
                    except Exception as exc:
                        log.append(f"{name}:{type(exc).__name__}")
            verdict, why = judge(answer, table)
            out.append({"id": claim["id"], "rows": len(table), "registry_rows": len(registry), "model": by,
                        "answer": answer, "verdict": verdict, "why": why})
            print(claim["id"], len(table), verdict, why)
            time.sleep(GAP)
    Path(sys.argv[1]).write_text(json.dumps({"requests": used, "log": log, "results": out}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
