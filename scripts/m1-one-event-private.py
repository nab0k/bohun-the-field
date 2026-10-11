#!/usr/bin/env python3
"""M1 one-event independent AI evaluation. PRIVATE, fail closed, no publication.
Executes only after owner verifies remaining FREE provider quota and authorizes one
bounded request to each provider. No Supabase writes, no Cloudflare deployment.
"""
import datetime as dt, hashlib, json, os, sys, urllib.request, urllib.error
from pathlib import Path

OUT=Path("output/m1/one-record");OUT.mkdir(parents=True,exist_ok=True)
MODEL=os.getenv("GEMINI_MODEL","gemini-3.5-flash-lite")
CF_MODEL="@cf/zai-org/glm-4.7-flash"
SOURCE="https://www.frequentis.com/sites/default/files/pr/2026-05/20260505-pr-Frequentis-BMLV_Flugfunksystem_EN.pdf"
audit={"case":"Frequentis Austria issuer document","source_url":SOURCE,
       "started_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
       "context_version":"eng04-frequentis-at-v1","gemini_model":MODEL,
       "validator_model":CF_MODEL,"provider_calls":{"gemini":0,"cloudflare_glm":0},
       "gemini_usage":None,"cloudflare_usage":None,"qa_rule":"v1.1",
       "claim_outcome":"NOT_ENOUGH_INFO","decision":"HOLD","public_release":False,
       "reasons":[]}
def save(): (OUT/"audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2))
def ask(req,timeout=55):
    with urllib.request.urlopen(req,timeout=timeout) as resp:return json.load(resp)
def failure(reason):
    audit["reasons"].append(reason);save()
    print("M1 one-event: HOLD, reason="+reason+", publish=false")
    sys.exit(1)
if os.getenv("GEMINI_FREE_QUOTA_VERIFIED")!="1" or os.getenv("CF_FREE_QUOTA_VERIFIED")!="1":
    failure("PROVIDER_FREE_QUOTA_NOT_VERIFIED")
for name in ("GEMINI_API_KEY","CF_ACCOUNT_ID","CF_AI_TOKEN"):
    if not os.getenv(name):failure("MISSING_PROVIDER_AUTH_"+name)
try:
    from pypdf import PdfReader
    from io import BytesIO
    req=urllib.request.Request(SOURCE,headers={"User-Agent":"Bohundefence-M1-Evidence-Audit/1"})
    with urllib.request.urlopen(req,timeout=25) as r:pdf=r.read(3_000_000)
    if not pdf.startswith(b"%PDF"):failure("SOURCE_NOT_PDF")
    audit["source_pdf_sha256"]=hashlib.sha256(pdf).hexdigest()
    text=" ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(BytesIO(pdf)).pages)[:14000]
    if len(text)<100:failure("NO_EXTRACTABLE_PRIMARY_TEXT")
    audit["evidence_excerpt"]=text
except Exception as e:
    audit["source_error_type"]=type(e).__name__;failure("SOURCE_FETCH_OR_EXTRACT_FAIL")
company=Path("data/eng04/frequentis-context-v1.json")
if not company.exists():failure("CONTEXT_VERSION_UNAVAILABLE")
context=json.loads(company.read_text())
audit["company_context"]={k:context["company"].get(k) for k in ("name","research_id","public_city","sector","ownership_group")}
instruction="""Extract EXACTLY ONE minimal attributed claim from the issuer PDF evidence. Reply JSON object:
{"claim":"...","source_quote":"exact contiguous text from the evidence","publisher_attribution":true,"geo_role":"country_or_public_hq_city","risk_tier":"R2"}.
Say 'Frequentis says' and never assert verified award, amount, deliveries or ownership as independent facts.
No classified/operational location. Use only provided document text and bounded company context."""
input_text=json.dumps({"instruction":instruction,"evidence_text":text,"company":audit["company_context"]},ensure_ascii=False)
audit["gemini_input"]=input_text
try:
    payload=json.dumps({"contents":[{"parts":[{"text":input_text}]}],
        "generationConfig":{"responseMimeType":"application/json","maxOutputTokens":500,"temperature":0}}).encode()
    request=urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
       data=payload,headers={"x-goog-api-key":os.environ["GEMINI_API_KEY"],"Content-Type":"application/json"})
    audit["provider_calls"]["gemini"]=1
    response=ask(request)
    audit["gemini_usage"]=response.get("usageMetadata")
    raw=response["candidates"][0]["content"]["parts"][0]["text"]
    audit["gemini_raw_response"]=raw
    claim=json.loads(raw)
except urllib.error.HTTPError as e:
    audit["gemini_http_status"]=e.code;failure("GEMINI_HTTP_ERROR")
except Exception as e:
    audit["gemini_error_type"]=type(e).__name__;failure("GEMINI_FAILED_OR_UNPARSEABLE")
quote=str(claim.get("source_quote") or "")
if not quote or quote not in text:failure("CLAIM_NOT_LITERAL_IN_SOURCE")
if claim.get("publisher_attribution") is not True or claim.get("risk_tier")!="R2":
    failure("UNSAFE_CLAIM_OR_RISK_LABEL")
# Independent review sees source and atomic claim, NOT the Gemini verdict or reasoning.
review=json.dumps({"task":"Check if quoted primary issuer passage supports the ATTRIBUTED claim only. Answer JSON verdict SUPPORTS, REFUTES, or NOT_ENOUGH_INFO with exact_evidence_quote.",
 "source_excerpt":text[:12000],"source_quote":quote,"claim":claim["claim"]},ensure_ascii=False)
audit["glm_input"]=review
try:
    url=f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CF_ACCOUNT_ID']}/ai/run/{CF_MODEL}"
    request=urllib.request.Request(url,data=json.dumps({"messages":[{"role":"user","content":review}],
      "max_tokens":240,"temperature":0}).encode(),headers={"Authorization":"Bearer "+os.environ["CF_AI_TOKEN"],"Content-Type":"application/json"})
    audit["provider_calls"]["cloudflare_glm"]=1
    cf=ask(request)
    audit["cloudflare_usage"]=cf.get("usage") or cf.get("result",{}).get("usage")
    if not cf.get("success",False):failure("GLM_RESPONSE_UNSUCCESSFUL")
    review_result=cf["result"]
    audit["glm_raw_response"]=review_result
    content=review_result.get("response") or review_result.get("output") or ""
    verdict=json.loads(content) if isinstance(content,str) else content
except urllib.error.HTTPError as e:
    audit["glm_http_status"]=e.code;failure("GLM_HTTP_ERROR")
except Exception as e:
    audit["glm_error_type"]=type(e).__name__;failure("GLM_FAILED_OR_UNPARSEABLE")
label=verdict.get("verdict")
audit["claim_outcome"]=label if label in ("SUPPORTS","REFUTES","NOT_ENOUGH_INFO") else "NOT_ENOUGH_INFO"
audit["independent_quote"]=verdict.get("exact_evidence_quote")
if label!="SUPPORTS" or verdict.get("exact_evidence_quote") not in text:
    audit["reasons"].append("SECOND_VALIDATOR_DID_NOT_SUPPORT")
# R2 claims on contracts are not calibrated or whitelisted for auto-release.
audit["reasons"].append("R2_AUTOPUBLISH_NOT_CERTIFIED")
audit["decision"]="HOLD"
save()
print("M1 real one-event evaluation recorded as QA HOLD, public_release=false; source/model usage saved privately")
