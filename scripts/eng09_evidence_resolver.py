#!/usr/bin/env python3
"""ENG09 deterministic evidence resolver for existing Frequentis/AU-F35/TED pilot.
No network, model calls, database writes or publication. All fixtures HOLD by design.
"""
import hashlib,json,re,sys
from pathlib import Path
def evidence_id(source_id,version,locator,text):
    raw="\x1f".join((source_id,version,locator, text))
    return "EV-"+hashlib.sha256(raw.encode()).hexdigest()[:20]
def build(source_id,version,fragments):
    assert source_id and re.fullmatch(r"[0-9a-f]{64}",version)
    out=[]
    for f in fragments:
        assert f["locator"] and isinstance(f["text"],str) and f["text"].strip()
        out.append({"evidence_id":evidence_id(source_id,version,f["locator"],f["text"]),
                    "source_id":source_id,"document_sha256":version,
                    "locator":f["locator"],"exact_text":f["text"]})
    assert len({x["evidence_id"] for x in out})==len(out)
    return out
def resolve(card,evidence):
    byid={e["evidence_id"]:e for e in evidence}
    issues=[];resolved=[]
    for i,claim in enumerate(card.get("claims",[])):
        eid=claim.get("evidence_id")
        e=byid.get(eid)
        if not e:issues.append({"index":i,"code":"UNKNOWN_EVIDENCE_ID"});continue
        if claim.get("source_id")!=e["source_id"]:issues.append({"index":i,"code":"SOURCE_MISMATCH"});continue
        if claim.get("support") not in ("SUPPORTS","REFUTES","NOT_ENOUGH_INFO"):
            issues.append({"index":i,"code":"INVALID_SUPPORT"});continue
        quote=claim.get("exact_quote")
        if quote and quote!=e["exact_text"]:
            issues.append({"index":i,"code":"QUOTE_NOT_SOURCE_ORIGINAL"});continue
        resolved.append({**claim,"exact_quote":e["exact_text"],
           "document_sha256":e["document_sha256"],"locator":e["locator"]})
    # Exact text and ID are NOT proof of semantic entailment, rights, entity identity or date.
    return {"claims":resolved,"issues":issues,
            "qa_decision":"HOLD","reasons":["INDEPENDENT_SEMANTIC_QA_PENDING","SOURCE_RIGHTS_AND_DATES_PENDING",
             "SECOND_VALIDATOR_NOT_OPERATIONAL"],"public_release":False}
def main():
    out=Path("output/eng09-evidence");out.mkdir(parents=True,exist_ok=True)
    f=Path("intelligence/fixtures/eng09-evidence-fixtures.json")
    fixtures=json.loads(f.read_text(encoding="utf8"))["fixtures"]
    report=[]
    for item in fixtures:
        source=item["source_id"]
        version=item.get("document_sha256") or hashlib.sha256(item["source_snapshot"].encode()).hexdigest()
        evidence=build(source,version,[{"locator":x["locator"],"text":x["text"]} for x in item["spans"]])
        card={"claims":[{"statement":item["test_statement"],"kind":"PUBLISHER_REPORTED",
              "source_id":source,"evidence_id":evidence[0]["evidence_id"],
              "exact_quote":"","support":"NOT_ENOUGH_INFO"}]}
        good=resolve(card,evidence)
        assert len(good["claims"])==1 and good["claims"][0]["exact_quote"]==item["spans"][0]["text"]
        assert not good["public_release"]
        wrong=resolve({"claims":[{**card["claims"][0],"evidence_id":"EV-NOT-IN-ORIGINAL"}]},evidence)
        assert wrong["issues"][0]["code"]=="UNKNOWN_EVIDENCE_ID"
        fake=resolve({"claims":[{**card["claims"][0],"exact_quote":"Fabricated quotation"}]},evidence)
        assert fake["issues"][0]["code"]=="QUOTE_NOT_SOURCE_ORIGINAL"
        report.append({"fixture":item["fixture"],"source_id":source,"document_sha256":version,
          "span_count":len(evidence),"positive_id_resolution":"PASS",
          "unknown_id_rejection":"PASS","fabricated_quote_rejection":"PASS",
          "qa_decision":"HOLD","source_capture_kind":("ORIGINAL_PUBLISHER_HTML_EXCERPT" if item.get("evidence_kind")=="REAL_PUBLISHER_CAPTURED_HTML_2026-10-11" else "TEST_SENTINEL_NOT_ORIGINAL")})
    (out/"audit.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
if __name__=="__main__":main()
