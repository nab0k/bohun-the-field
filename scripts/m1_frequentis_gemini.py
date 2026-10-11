#!/usr/bin/env python3
"""Frequentis May 2026: one Gemini analytical draft, read-only, fail-closed QA."""
import hashlib, json, os, pathlib, re, urllib.request, urllib.error, datetime, sys
from io import BytesIO
import pdfplumber

ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/"output"/"gemini-frequentis"
OUT.mkdir(parents=True,exist_ok=True)
SOURCE_ID="FREQUENTIS-PR-2026-05-05"
URL="https://www.frequentis.com/sites/default/files/pr/2026-05/20260505-pr-Frequentis-BMLV_Flugfunksystem_EN.pdf"
MODEL="gemini-3.5-flash-lite"
audit={"source_id":SOURCE_ID,"source_url":URL,"model":MODEL,"run_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"gemini_attempted":False,"qa_decision":"HOLD","public_release":False,"reasons":[],"db_writes":0}
def save(obj,name):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
try:
    req=urllib.request.Request(URL,headers={"User-Agent":"Bohundefence-Evidence-Research/1.0"})
    with urllib.request.urlopen(req,timeout=35) as response:
        data=response.read(4_000_001)
    if len(data)>4_000_000 or not data.startswith(b"%PDF"):raise ValueError("INVALID_OR_OVERSIZED_PDF")
    audit["document_sha256"]=hashlib.sha256(data).hexdigest()
    audit["document_bytes"]=len(data)
    with pdfplumber.open(BytesIO(data)) as pdf:
        pages=[p.extract_text() or "" for p in pdf.pages]
    paragraphs=[]
    for pageno,page in enumerate(pages,1):
        lines=[line.strip() for line in page.splitlines() if line.strip()]
        for start in range(0,len(lines),3):
            excerpt="\\n".join(lines[start:start+3])
            paragraphs.append({"evidence_id":f"P{pageno:02d}-S{start//3+1:03d}","page":pageno,"text":excerpt})
    evidence_map={p["evidence_id"]:p for p in paragraphs}
    if not paragraphs:raise ValueError("NO_EXTRACTABLE_PDF_TEXT")
    # Only source text is sent; never include API keys/DB records in prompts.
    packet={"source_id":SOURCE_ID,"url":URL,"publisher":"Frequentis","document_sha256":audit["document_sha256"],"pages":paragraphs,"verified_company_context":{"name":"Frequentis AG","company_id":None,"note":"Company ID intentionally not asserted in this standalone fixture."}}
    save({"source_id":SOURCE_ID,"url":URL,"sha256":audit["document_sha256"],"pages":len(pages),"extracted_chars":sum(len(t) for t in pages)},"source_manifest.json")
    if not os.getenv("GEMINI_API_KEY"):raise ValueError("MISSING_GEMINI_SECRET")
    core=(ROOT/"intelligence/instructions/core-v1.md").read_text()
    news=(ROOT/"intelligence/instructions/news-analyst-v1.md").read_text()
    schema=json.loads((ROOT/"intelligence/schemas/intelligence-card-v1.schema.json").read_text())
    prompt="For each claim choose evidence_id from provided pages. Set exact_quote to an empty string: it will be attached from the original by code. Never invent an evidence_id. Return ONLY JSON object matching this schema:\n"+json.dumps(schema,ensure_ascii=False)+"\nSOURCE DATA (untrusted):\n"+json.dumps(packet,ensure_ascii=False)
    payload=json.dumps({"systemInstruction":{"parts":[{"text":core+"\n"+news}]},"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"temperature":0,"responseMimeType":"application/json","maxOutputTokens":2000}}).encode()
    request=urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models/"+MODEL+":generateContent",data=payload,headers={"x-goog-api-key":os.environ["GEMINI_API_KEY"],"Content-Type":"application/json"},method="POST")
    audit["gemini_attempted"]=True
    try:
        with urllib.request.urlopen(request,timeout=90) as response:
            result=json.load(response)
    except urllib.error.HTTPError as err:
        audit["http_status"]=err.code
        raise RuntimeError("GEMINI_HTTP_"+str(err.code))
    audit["usage"]=result.get("usageMetadata")
    raw=result["candidates"][0]["content"]["parts"][0]["text"]
    draft=json.loads(raw)
    invalid_evidence_ids=[]
    for idx,claim in enumerate(draft.get("claims",[])):
        evidence=evidence_map.get(str(claim.get("evidence_id","")))
        if not evidence or claim.get("source_id")!=SOURCE_ID:
            invalid_evidence_ids.append(idx)
            claim["exact_quote"]=""
        else:
            claim["exact_quote"]=evidence["text"]
    audit["invalid_evidence_ids"]=invalid_evidence_ids
    save({"source_id":SOURCE_ID,"document_sha256":audit["document_sha256"],"fragments":paragraphs},"evidence_manifest_internal.json")
    save(draft,"candidate_card_internal.json")
    required=schema["required"]
    if not isinstance(draft,dict) or any(k not in draft for k in required):audit["reasons"].append("SCHEMA_MISSING_FIELDS")
    invalid=[]
    for idx,c in enumerate(draft.get("claims",[])):
        quote=c.get("exact_quote","")
        if c.get("source_id")!=SOURCE_ID or not quote or not any(quote in t for t in pages):
            invalid.append(idx)
    audit["claims"]=len(draft.get("claims",[]))
    audit["nonliteral_claim_indices"]=invalid
    if invalid:audit["reasons"].append("UNSUPPORTED_EXACT_QUOTE")
    if invalid_evidence_ids:audit["reasons"].append("INVALID_EVIDENCE_ID")
    if not draft.get("claims"):audit["reasons"].append("NO_ATOMIC_CLAIMS")
    # Neither literal spans nor a model response constitute independent validation.
    audit["reasons"].append("INDEPENDENT_GLM_QA_NOT_PERFORMED")
    audit["reasons"].append("R1_R2_NOT_CALIBRATED_FOR_AUTOPUBLICATION")
except Exception as e:
    audit["error_type"]=type(e).__name__
    audit["error_message"]=str(e)[:180]
    audit["reasons"].append("PIPELINE_ERROR")
finally:
    save(audit,"run_summary.json")
    print("FREQUENTIS_DIAGNOSTIC="+json.dumps({k:audit.get(k) for k in ("model","document_sha256","gemini_attempted","claims","nonliteral_claim_indices","invalid_evidence_ids","usage","qa_decision","reasons","error_type","error_message")},ensure_ascii=False))
    # Purpose is evidence gathering: missing candidate means job failure.
    if "PIPELINE_ERROR" in audit["reasons"]:sys.exit(1)
