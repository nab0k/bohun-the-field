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

TARGET_HOLD_IDS = ("12848","12847","12249","12248","11357","10760","10758")
REJECT_REGRESSION_IDS = {"12247","11356","10759"}
BLOCKED_KEYWORDS = ("postage stamp","commemorative stamp","kc-10a return","might return to service")
def canonical(url):
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    p=urlsplit(url.strip())
    q=urlencode(sorted((k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in ("fbclid","gclid")))
    return urlunsplit((p.scheme.lower(),p.netloc.lower().removeprefix("www."),p.path.rstrip("/") or "/",q,""))
def event_key(row):
    # Canonical URL is a conservative same-event key. Ambiguous coverage remains QA HOLD.
    return canonical(row["url"])
def select_rows(rows, limit=10):
    unique=set(); selected=[]; excluded=[]
    by_id={r["id"]:r for r in rows}
    priority=[by_id[x] for x in TARGET_HOLD_IDS if x in by_id]
    for r in priority+[x for x in rows if x["id"] not in TARGET_HOLD_IDS]:
        reason=None
        if r["id"] in REJECT_REGRESSION_IDS:reason="qa_reject_regression"
        elif any(x in r["title"].lower() for x in BLOCKED_KEYWORDS):reason="out_of_scope_or_speculation"
        elif event_key(r) in unique:reason="duplicate_event_url"
        if reason:excluded.append({"id":r["id"],"reason":reason});continue
        unique.add(event_key(r));selected.append(r)
        if len(selected)>=limit:break
    return selected,excluded
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
                 AND (i.id::text = ANY(%s) OR i.last_seen_at IS NOT NULL)
               ORDER BY CASE WHEN i.id::text = ANY(%s) THEN 0 ELSE 1 END,
                        i.last_seen_at DESC NULLS LAST, i.id DESC
               LIMIT 300"""
        with conn.cursor() as cur:
            cur.execute(q,(list(TARGET_HOLD_IDS),list(TARGET_HOLD_IDS)))
            rows=cur.fetchall()
        conn.rollback()
    finally:conn.close()
    result=[]
    for id_,external,title,url,kind,pub,raw,sid,source in rows:
        if not eligible_url(url):continue
        try:
            data=json.loads(raw or "{}")
            if not isinstance(data,dict):data={"payload":data}
        except (ValueError,TypeError):data={"unparsed":str(raw or "")[:4000]}
        # Only text actually supplied by existing raw record, no synthesized full article.
        fragments=[]
        for key in ("description","summary","content","body","text","excerpt"):
            value=data.get(key)
            if isinstance(value,str) and value.strip():fragments.append({"field":key,"text":value[:3500]})
        result.append(dict(id=str(id_),external_id=str(external),title=str(title)[:400],url=url,
                  canonical_url=canonical(url),kind=kind,published_at=str(pub) if pub else None,
                  source_id=str(sid),source_name=source,excerpts=fragments,
                  evidence_scope="stored_raw_text_only_no_full_text_assumed"))
    selected,excluded=select_rows(result)
    emit("selection_audit.json",{"selected_ids":[r["id"] for r in selected],"excluded":excluded,
         "missing_hold_ids":[x for x in TARGET_HOLD_IDS if x not in {r["id"] for r in selected}],
         "candidate_pool":len(result)})
    if len(selected)!=10:raise RuntimeError(f"Only {len(selected)} distinct eligible inputs (need 10)")
    return selected

def gemini(item):
    prompt = {
        "instruction": "Internal Bohundefence reviewer draft ONLY, never approval. Use only the given title and stored raw excerpts, do not assume full source access. Return JSON with headline,takeaway,why_it_matters,event_type,country_iso2,sector,evidence_quote,uncertainty,claims (array). Each claim: text,classification OBSERVED|PUBLISHER_CLAIM|ANALYSIS|UNKNOWN,source_url,quote,source_field,geo_role (company_hq|event_venue|buyer_jurisdiction|source_jurisdiction|unknown). Claim OBSERVED only for directly corroborated source wording; claims of contracts, approvals and exports stay PUBLISHER_CLAIM until primary decision evidence. Unknown is UNKNOWN. No speculative awards, no military operational locations, no UA facility city/address, no private individuals. If primary evidence is insufficient signal must stay unresolved. Evidence quote must occur verbatim in title or supplied raw excerpts.",
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
    if not evidence or (evidence not in item["title"] and not any(evidence in x["text"] for x in item["excerpts"])):
        reasons.append("evidence_not_literal")
    if card.get("event_type") not in ALLOWED:
        reasons.append("unresolved_or_disallowed_type")
    if not card.get("headline") or not card.get("takeaway"):
        reasons.append("missing_analysis")
    if card.get("country_iso2") == "UA":
        reasons.append("ukraine_location_requires_independent_QA")
    for claim in card.get("claims",[]):
        if claim.get("classification") not in ("OBSERVED","PUBLISHER_CLAIM","ANALYSIS","UNKNOWN"):reasons.append("invalid_claim_class")
        if claim.get("source_url")!=item["url"]:reasons.append("claim_source_mismatch")
        quote=str(claim.get("quote") or "")
        if not quote or (quote not in item["title"] and not any(quote in x["text"] for x in item["excerpts"])):reasons.append("claim_quote_missing")
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
                              "source_url":item["url"],"canonical_url":item["canonical_url"],"source_id":item["source_id"],
                              "model":audit["model"],"prompt_version":"eng01-p1-v2",
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
