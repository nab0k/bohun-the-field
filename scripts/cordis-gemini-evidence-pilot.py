#!/usr/bin/env python3
"""Issue #51: bounded CORDIS original XML -> exactly one Gemini candidate.
Default is offline, no paid request, no Supabase writes, and always QA HOLD.
Explicit --call-gemini requires BOHUN_APPROVE_ONE_GEMINI_CALL=YES.
"""
import argparse, hashlib, json, os, sys, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from intelligence.gemini_evidence_validator import validate_candidate
from scripts.intelligence_live_pilot import capture, exact_locator

SOURCE_URL="https://cordis.europa.eu/project/id/101289409?format=xml"
MODEL=os.getenv("GEMINI_MODEL","gemini-2.5-flash-lite")
OUT=ROOT/"output/cordis-gemini-pilot"

def resolve_financial_element(root, xpath):
    """Resolve the canonical XML locator using the CORDIS namespace, not string XPath guessing."""
    if xpath!="/project/ecMaxContribution":
        raise ValueError("UNSUPPORTED_FINANCIAL_XPATH")
    ns="http://cordis.europa.eu"
    if root.tag!="{"+ns+"}project":
        raise ValueError("UNEXPECTED_CORDIS_NAMESPACE")
    matches=root.findall("{"+ns+"}ecMaxContribution")
    if len(matches)!=1 or not matches[0].text:
        raise ValueError("MISSING_OR_AMBIGUOUS_FINANCIAL_ELEMENT")
    return matches[0].text.strip()

def validate_financial_claim(claim, expected_amount):
    if claim.get("predicate")!="reported_ec_max_contribution":
        raise ValueError("UNSUPPORTED_FINANCIAL_PREDICATE")
    obj=claim.get("object")
    if not isinstance(obj,dict) or obj.get("value")!=expected_amount or obj.get("unit")!="EUR":
        raise ValueError("FINANCIAL_AMOUNT_OR_UNIT_MISMATCH")
    if obj.get("datatype")!="decimal":
        raise ValueError("FINANCIAL_DATATYPE_MISMATCH")
    return True

def make_packet(xml_bytes):
    text=xml_bytes.decode("utf-8")
    root=ET.fromstring(xml_bytes)
    ns={"c":"http://cordis.europa.eu"}
    if root.findtext("c:id",default="",namespaces=ns)!="101289409":
        raise ValueError("PROJECT_ID_MISMATCH")
    value=resolve_financial_element(root,"/project/ecMaxContribution")
    if not value: raise ValueError("NO_SOURCE_FACT")
    excerpt,locator=exact_locator(text,"ecMaxContribution",value)
    if resolve_financial_element(root,locator["xpath"])!=value:
        raise ValueError("NAMESPACE_AWARE_LOCATOR_MISMATCH")
    locator["namespace_uri"]="http://cordis.europa.eu"
    doc_hash=hashlib.sha256(xml_bytes).hexdigest()
    evidence_id="evidence:cordis:101289409:ecMaxContribution:"+doc_hash[:12]
    packet={"evidence":[{"evidence_id":evidence_id,"exact_excerpt":excerpt}]}
    return packet,{"source_url":SOURCE_URL,"original_sha256":doc_hash,"source_kind":"official_xml",
                   "rights":"UNREVIEWED","qa_status":"HOLD","public_release":False,
                   "exact_excerpt":excerpt,"locator":locator,"evidence_id":evidence_id,"financial_value":value}

def model_request(packet,meta):
    prompt={"instruction":"Produce EXACTLY one conservative candidate: predicate reported_ec_max_contribution, decimal value from official XML and unit EUR. This is MAXIMUM EU contribution to project, NOT funds paid, expenditure, contract award or total budget. Use exact contiguous span. qa_status MUST be HOLD. JSON only.",
     "source_url":meta["source_url"],"evidence_id":meta["evidence_id"],
     "evidence_text":meta["exact_excerpt"],"financial_value":meta["financial_value"],
     "required_shape":{"qa_status":"HOLD","candidate_claims":[{"claim_id":"string","subject":"string","predicate":"string","object":{"value":meta["financial_value"],"datatype":"decimal","unit":"EUR"},"evidence_ids":[meta["evidence_id"]],"exact_spans":[meta["exact_excerpt"]],"confidence":0.0}],"candidate_event":{"type":"source_update","claim_ids":["same claim_id"]},"uncertainties":[]}}
    return {"contents":[{"parts":[{"text":json.dumps(prompt,ensure_ascii=False)}]}],
            "generationConfig":{"responseMimeType":"application/json","maxOutputTokens":500,"temperature":0}}

def call_once(request_body):
    if os.getenv("BOHUN_APPROVE_ONE_GEMINI_CALL")!="YES":
        raise RuntimeError("OWNER_APPROVAL_MISSING_NO_GEMINI_CALL")
    key=os.getenv("GEMINI_API_KEY")
    if not key:raise RuntimeError("GEMINI_API_KEY_MISSING")
    url="https://generativelanguage.googleapis.com/v1beta/models/"+MODEL+":generateContent"
    request=urllib.request.Request(url,data=json.dumps(request_body).encode(),headers={"x-goog-api-key":key,"Content-Type":"application/json"})
    with urllib.request.urlopen(request,timeout=60) as resp:data=json.load(resp)
    response=json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
    return response,data.get("usageMetadata",{})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--call-gemini",action="store_true")
    args=parser.parse_args()
    raw,ctype=capture()   # same official XML fetch previously CI-tested
    packet,meta=make_packet(raw)
    body=model_request(packet,meta)
    OUT.mkdir(parents=True,exist_ok=True)
    # Store the exact *request* for reproducibility, never mislabel it as Gemini output.
    request_json=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    request_sha256=hashlib.sha256(request_json.encode()).hexdigest()
    (OUT/"model-request.json").write_text(request_json)
    # Private CI artifact for independent QA: original bytes, immutable hash and locator.
    # Never publish or deploy this packet; rights remain UNREVIEWED.
    (OUT/"original-cordis.xml").write_bytes(raw)
    (OUT/"evidence-packet.json").write_text(json.dumps({
        "source_url": meta["source_url"],
        "original_sha256": meta["original_sha256"],
        "document_version": meta["original_sha256"],
        "evidence_id": meta["evidence_id"],
        "exact_excerpt": meta["exact_excerpt"],
        "locator": meta["locator"],
        "rights": "UNREVIEWED",
        "qa": {"decision":"HOLD","public_release":False,"independent_review":"PENDING"},
        "gemini_called": bool(args.call_gemini)
    },sort_keys=True,ensure_ascii=False,indent=2))
    if not args.call_gemini:
        expected=body["contents"][0]["parts"][0]["text"]
        instruction=json.loads(expected)
        if instruction["evidence_id"]!=meta["evidence_id"] or instruction["evidence_text"]!=meta["exact_excerpt"]:
            raise RuntimeError("OFFLINE_REQUEST_EVIDENCE_MISMATCH")
        # An intentionally local candidate checks wiring only, not the truth of a model response.
        local_claim={"claim_id":"offline:claim:cordis:101289409:amount",
          "subject":"CORDIS:101289409","predicate":"reported_ec_max_contribution",
          "object":{"value":meta["financial_value"],"datatype":"decimal","unit":"EUR"},
          "evidence_ids":[meta["evidence_id"]],"exact_spans":[meta["exact_excerpt"]]}
        local_candidate={"qa_status":"HOLD","candidate_claims":[local_claim],
          "candidate_event":{"type":"source_update","claim_ids":[local_claim["claim_id"]]}}
        validate_financial_claim(local_claim,meta["financial_value"])
        contract_validation=validate_candidate(packet,local_candidate)
        if not contract_validation["pass"] or contract_validation["qa_status"]!="HOLD":
            raise RuntimeError("OFFLINE_CONTRACT_VALIDATION_FAILED")
    if args.call_gemini:
        candidate,usage=call_once(body)
        if len(candidate.get("candidate_claims") or []) != 1:
            raise RuntimeError("EXPECTED_EXACTLY_ONE_CANDIDATE_CLAIM")
        verification=validate_candidate(packet,candidate)
        validate_financial_claim(candidate["candidate_claims"][0],meta["financial_value"])
        result={"mode":"LIVE_GEMINI_CANDIDATE","model":MODEL,"source_sha256":meta["original_sha256"],
                "validation":verification,"usage":usage,"qa_status":"HOLD","public_release":False,
                "supabase_write":False,"independent_review":"PENDING"}
    else:
        result={"mode":"OFFLINE_CONTRACT_ONLY_GEMINI_NOT_CALLED","source_sha256":meta["original_sha256"],
                "source_url":SOURCE_URL,"evidence_spans":len(packet["evidence"]),
                "evidence_locator_verified":meta["exact_excerpt"] in raw.decode("utf-8"),
                "source_bytes":len(raw),"model_request_sha256":request_sha256,"offline_contract_validation":contract_validation,"model_response":"NOT_CALLED", "qa_status":"HOLD","public_release":False,
                "supabase_write":False}
    (OUT/"result.json").write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":main()
