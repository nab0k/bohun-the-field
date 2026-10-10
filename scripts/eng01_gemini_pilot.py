#!/usr/bin/env python3
"""ENG-01 manual internal pilot: 10 existing Supabase records -> Gemini -> QA artifact.
Read-only source; no publishing, no production writes, no new tables.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

OUT = Path("output/eng01")
OUT.mkdir(parents=True, exist_ok=True)
def emit(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def eligible_url(s):
    return isinstance(s, str) and s.startswith(("https://", "http://"))

def read_input():
    import psycopg
    conn = psycopg.connect(
        host=os.environ.get("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
        user=os.environ.get("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        dbname="postgres", password=os.environ["SUPABASE_DB_PASSWORD"],
        sslmode="require", connect_timeout=20)
    try:
        conn.execute("BEGIN READ ONLY")
        q = """SELECT i.id, i.external_id, i.title, i.url, i.item_type,
                      i.published_at, i.raw_data::text, i.source_id, s.name AS source_name
               FROM intelligence.items i
               JOIN intelligence.sources s ON s.id=i.source_id
               WHERE i.title IS NOT NULL AND i.url LIKE 'http%'
               ORDER BY CASE WHEN lower(i.item_type)='tender' THEN 1 ELSE 0 END,
                        i.last_seen_at DESC NULLS LAST, i.id DESC
               LIMIT 30"""
        with conn.cursor() as cur:
            cur.execute(q)
            rows = cur.fetchall()
        conn.rollback()
    finally:
        conn.close()
    dedup, result = set(), []
    for id_, external, title, url, kind, pub, raw, sid, source in rows:
        key = (str(sid), str(external))
        if key in dedup or not eligible_url(url):
            continue
        dedup.add(key)
        try:
            rawval = json.loads(raw or "{}")
            body = json.dumps(rawval, ensure_ascii=False)[:2400]
        except (ValueError, TypeError):
            body = str(raw or "")[:2400]
        result.append(dict(id=str(id_), external_id=str(external), title=str(title)[:400],
                           url=url, kind=kind, published_at=str(pub) if pub else None,
                           source_id=str(sid), source_name=source, excerpt=body))
        if len(result) == 10: break
    if len(result) != 10:
        raise RuntimeError(f"Only {len(result)} usable unique input items (need 10)")
    return result

def gemini(item):
    prompt = {
        "instruction": "Produce one internally reviewable Bohundefence English analytical card from source ONLY. No speculation. Return strict JSON object with keys: headline, takeaway, why_it_matters, event_type, country_iso2, sector, evidence_quote, uncertainty. If article is not eligible or evidence weak set event_type='unresolved'. The evidence_quote must be a literal substring of the supplied title or source excerpt. No operational military locations or movements, no private individuals, no Ukrainian facility location or addresses. Distinguish publisher claims from independently verified outcomes.",
        "source": item
    }
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{os.environ.get('GEMINI_MODEL','gemini-2.5-flash')}:generateContent",
        data=json.dumps({"contents":[{"parts":[{"text":json.dumps(prompt,ensure_ascii=False)}]}],
                         "generationConfig":{"temperature":0.1,"responseMimeType":"application/json"}}).encode(),
        headers={"Content-Type":"application/json","x-goog-api-key":os.environ["GEMINI_API_KEY"]},
        method="POST")
    with urllib.request.urlopen(req,timeout=90) as resp:
        body=json.load(resp)
    raw=body["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw)

ALLOWED = {"contract","funding","opportunity","production","partnership","policy","aid_ukraine","event","technology"}
def qa(item, card):
    reasons=[]
    evidence=str(card.get("evidence_quote") or "").strip()
    if not evidence or (evidence not in item["title"] and evidence not in item["excerpt"]):
        reasons.append("evidence_not_literal")
    if card.get("event_type") not in ALLOWED:
        reasons.append("unresolved_or_disallowed_type")
    if not card.get("headline") or not card.get("takeaway"):
        reasons.append("missing_analysis")
    if card.get("country_iso2") == "UA":
        reasons.append("ukraine_location_requires_independent_QA")
    # No model response can self-approve release. QA-08 must sign off.
    return {"state":"PENDING_QA_08", "automated_flags":reasons, "public_release":False}

def main():
    audit={"utc":dt.datetime.now(dt.timezone.utc).isoformat(),"run":"ENG-01",
           "source":"existing intelligence.items", "read_only":True, "publication":False,
           "model":os.environ.get("GEMINI_MODEL","gemini-2.5-flash"),"inputs":0,"outputs":0,"errors":[]}
    cards=[]
    try:
        inp=read_input()
        audit["inputs"]=len(inp)
        emit("input_manifest.json",[{k:v for k,v in i.items() if k!="excerpt"} for i in inp])
        for item in inp:
            try:
                result=gemini(item)
                gate=qa(item,result)
                cards.append({"source_item_id":item["id"],"external_id":item["external_id"],
                              "source_url":item["url"],"source_id":item["source_id"],
                              "model":audit["model"],"prompt_version":"eng01-v1",
                              "card":result,"gate":gate})
            except urllib.error.HTTPError as e:
                # Never persist provider response body, prompts or credentials.
                audit["errors"].append({"item_id":item["id"],"error_type":"GeminiHTTPError","http_status":e.code})
                if e.code in (400, 401, 403, 404, 429):
                    audit["halted_after_first_provider_error"]=True
                    break
            except Exception as e:
                audit["errors"].append({"item_id":item["id"],"error_type":type(e).__name__})
        audit["outputs"]=len(cards)
        if len(cards)!=10: raise RuntimeError("Gemini did not produce 10 valid JSON responses")
    except Exception as e:
        audit["errors"].append({"stage":"pilot","error_type":type(e).__name__,"message":str(e)[:180]})
    finally:
        emit("qa_cards.json",cards)
        emit("run_summary.json",audit)
    print(f"ENG-01: inputs={audit['inputs']} outputs={audit['outputs']} errors={len(audit['errors'])}; NO PUBLISH")
    if len(cards)!=10 or audit["errors"]:
        sys.exit(1)
if __name__=="__main__":
    main()
