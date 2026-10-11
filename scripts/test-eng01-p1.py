#!/usr/bin/env python3
"""ENG-01 P1 negative fixtures, no credentials, no API calls."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from eng01_gemini_pilot import select_rows, qa, canonical
def row(i,url,title):
    return {"id":str(i),"url":url,"title":title,"excerpts":[]}
rows=[
 row(11357,"https://example.org/army-kaizen?utm_source=rss","US Army Kaizen contract"),
 row(11356,"https://example.org/army-kaizen","US Army Kaizen contract (copy)"),
 row(12247,"https://example.org/stamp","Commemorative postage stamp"),
 row(10759,"https://example.org/kc10","KC-10A return to service?"),
 row(12847,"https://example.org/bmt","BMT Canada training"),
]
chosen,excluded=select_rows(rows,limit=5)
assert [x["id"] for x in chosen]==["12847","11357"]
assert {x["id"] for x in excluded}=={"12247","11356","10759"}
assert canonical("https://www.Example.org/x/?utm_campaign=a")=="https://example.org/x"
item={"url":"https://example.org/ua","title":"Source says event","excerpts":[]}
r=qa(item,{"event_type":"contract","headline":"Source","takeaway":"Event","evidence_quote":"Source",
 "country_iso2":"UA","claims":[{"classification":"OBSERVED","quote":"not in source",
 "source_url":item["url"]}]})
assert r["state"]=="PENDING_QA_08" and not r["public_release"]
assert "ukraine_location_requires_independent_QA" in r["automated_flags"]
assert "claim_quote_missing" in r["automated_flags"]
print("ENG-01 P1: 6 negative checks PASS")
