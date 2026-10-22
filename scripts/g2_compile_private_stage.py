#!/usr/bin/env python3
"""Generate private HOLD-only staging manifest; never opens a database."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collectors.companies.private_profile_stage import compile_manifest,digest

def main():
    p=argparse.ArgumentParser()
    for key in ("profile","research","cohort","private-output"):
        p.add_argument("--"+key,type=Path,required=True)
    a=p.parse_args()
    project=Path(__file__).resolve().parents[1]
    target=a.private_output.resolve()
    if target.is_relative_to(project) or target.exists():
        p.error("Output must be outside the Git checkout and must not overwrite")
    docs=[json.loads(x.read_text(encoding="utf-8")) for x in (a.profile,a.research,a.cohort)]
    compiled=compile_manifest(*docs)
    compiled["manifest_sha256"]=digest(compiled)
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    target.write_text(json.dumps(compiled,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    target.chmod(0o600)
    print(json.dumps({"claims":len(compiled["profiles"][0]["claims"]),
                      "sources":len(compiled["profiles"][0]["sources"]),
                      "research_cases":len(compiled["research_cases"]),
                      "db_writes":0,"public_releases":0,
                      "digest":compiled["manifest_sha256"]}))
if __name__=="__main__":
    main()
