#!/usr/bin/env python3
"""One-call Gemini preflight; no DB, no publishing, no secret logging."""
import json,os,sys,urllib.request,urllib.error
from pathlib import Path
OUT=Path("output/eng01");OUT.mkdir(parents=True,exist_ok=True)
model=os.environ.get("GEMINI_MODEL","gemini-3.5-flash-lite")
result={"model":model,"test_calls":0,"ok":False,"public_release":False}
def request(method,url,data=None):
    return urllib.request.Request(url,data=data,method=method,headers={"x-goog-api-key":os.environ["GEMINI_API_KEY"],"Content-Type":"application/json"})
try:
    # Key-scoped list verifies model availability without consuming generation quota.
    with urllib.request.urlopen(request("GET","https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"),timeout=25) as res: catalog=json.load(res)
    models=[x for x in catalog.get("models",[]) if x.get("name")==("models/"+model) and "generateContent" in x.get("supportedGenerationMethods",[])]
    result["model_listed_for_key"]=bool(models)
    if not models: raise RuntimeError("MODEL_NOT_AVAILABLE_FOR_KEY")
    payload=json.dumps({"contents":[{"parts":[{"text":"Reply exactly: OK"}]}],"generationConfig":{"maxOutputTokens":48,"temperature":0}}).encode()
    result["test_calls"]=1
    try:
        with urllib.request.urlopen(request("POST",f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",payload),timeout=50) as res: response=json.load(res)
        parts=response.get("candidates",[{}])[0].get("content",{}).get("parts",[])
        result["ok"]=bool(parts and any(str(p.get("text","")).strip() for p in parts))
        result["usage"]=response.get("usageMetadata",{})
    except urllib.error.HTTPError as e:
        result["error_type"]="GeminiHTTPError";result["http_status"]=e.code
except Exception as e:
    if "error_type" not in result:result["error_type"]=type(e).__name__;result["error_code"]=str(e)[:100] if str(e)=="MODEL_NOT_AVAILABLE_FOR_KEY" else "PREFLIGHT_FAILED"
finally:
    (OUT/"preflight_summary.json").write_text(json.dumps(result,indent=2))
    print(f"Gemini preflight: model={model}, ok={result['ok']}, http={result.get('http_status','none')}, one_call={result['test_calls']}")
if not result["ok"]:sys.exit(1)
