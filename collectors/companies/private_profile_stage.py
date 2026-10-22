"""G2 closed fact manifest validator. Offline only, NO Supabase writes.

The reviewed SQL migration 004 is separate. Never add source packs or
private profile manifests to Git. This module accepts owner-local inputs.
"""
import hashlib
import json
from urllib.parse import urlsplit

COHORT20 = {1,2,3,4,5,6,8,9,24,25,46,52,55,59,167,203,1203,1913,229,734}
PILOT_ID = "co_20261018130520ef6c8e22d3"
ASSESSMENTS = {"registry_supported","cross_source","company_claim","inferred_needs_review"}

def digest(x):
    s=json.dumps(x,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(s.encode()).hexdigest()

def safe_source(url):
    if not isinstance(url,str): return False
    p=urlsplit(url)
    return p.scheme=="https" and bool(p.hostname) and not p.username and not p.password

def compile_claims(profile):
    if profile.get("company_id") != PILOT_ID:
        raise ValueError("Unexpected pilot company ID")
    pub=profile.get("publication",{})
    if pub.get("status")!="HOLD" or pub.get("qa_approved") is not False or pub.get("public_release") is not False:
        raise ValueError("Publication gate failed")
    srcs=profile.get("sources",[])
    ids={s["id"] for s in srcs}
    if len(ids)!=len(srcs) or not ids or any(not safe_source(s.get("url")) or s.get("original_bytes_stored") is not False for s in srcs):
        raise ValueError("Invalid source registry")
    claims=[];seen=set()
    for c in profile.get("evidence",[]):
        srefs=c.get("source_ids")
        if not c.get("id") or c["id"] in seen:
            raise ValueError("Duplicate claim")
        seen.add(c["id"])
        if not isinstance(srefs,list) or not srefs or len(set(srefs))!=len(srefs) or not set(srefs)<=ids:
            raise ValueError("Unattributed claim")
        if c.get("assessment") not in ASSESSMENTS or not str(c.get("path","")).startswith("/") or c.get("value") is None:
            raise ValueError("Malformed or unreviewed claim")
        payload={"path":c["path"],"value":c["value"],"assessment":c["assessment"],
                 "source_ids":sorted(srefs),"note":c.get("note") or ""}
        claims.append({"company_id":PILOT_ID,"claim_ref":c["id"],"field_path":c["path"],
          "claim_value":c["value"],"assessment":c["assessment"],"review_note":payload["note"],
          "content_sha256":digest(payload),"source_refs":srefs,"qa_status":"HOLD",
          "public_release":False})
    if len(claims)!=23 or len(srcs)!=6:
        raise ValueError("Frozen Accurision baseline differs")
    return {"company_id":PILOT_ID,"sources":srcs,"claims":claims,"qa_status":"HOLD","public_release":False}

def compile_cases(research,cohort):
    byid={r["id"]:r for r in cohort.get("records",[])}
    if set(byid)!=COHORT20 or len(cohort.get("records",[]))!=20:
        raise ValueError("Unexpected frozen Supabase cohort")
    out=[];seen=set()
    for r in research.get("records",[]):
        i=r.get("candidate_id")
        if i not in byid or i in seen: raise ValueError("Unknown or duplicate case")
        seen.add(i);c=byid[i]
        if (r.get("exhibitor_name")!=c["name"] or
            r.get("source_reported_country_only")!=c["reported_country"] or
            r.get("discovery_status")!=c["status"]):
            raise ValueError("Candidate source drift")
        if (r.get("qa_status")!="HOLD" or r.get("identity_binding")!="NOT_APPROVED" or
            r.get("public_release") is not False): raise ValueError("Unsafe case")
        if any(not safe_source(s.get("url")) or s.get("original_bytes_archived") is not False for s in r.get("source_leads",[])):
            raise ValueError("Unsafe source lead")
        out.append({"candidate_id":i,"research_payload":r,"content_sha256":digest(r),
                    "qa_status":"HOLD","public_release":False})
    if len(out)!=20:raise ValueError("Incomplete research pilot")
    return sorted(out,key=lambda v:v["candidate_id"])

def compile_manifest(profile,research,cohort):
    p=compile_claims(profile)
    cases=compile_cases(research,cohort)
    return {"schema":"bohun-private-profile-stage.v1","pilot_company_id":PILOT_ID,
       "profiles":[p],"research_cases":cases,"qa_status":"HOLD","public_release":False,
       "migration":"004_private_profile_claims.sql (NOT APPLIED)"}
