#!/usr/bin/env python3
"""Offline, read-only QA report for Grants.gov collector JSON. No DB writes."""
import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path

def audit(report, today):
    items = report.get("items", [])
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    ids = [str(i.get("external_id") or "").strip() for i in items]
    if not ids or any(not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("missing or duplicate opportunity IDs")
    result = {"as_of": today.isoformat(), "total": len(items),
              "statuses": dict(Counter(str(i.get("status") or "UNKNOWN") for i in items)),
              "missing_title": 0, "missing_agency_name": 0, "missing_agency_code": 0,
              "missing_close_date": 0, "invalid_close_date": 0,
              "close_date_before_today": 0, "forecasted": 0,
              "potentially_current_posted": 0, "held": 0}
    for i in items:
        result["missing_title"] += not bool(i.get("title"))
        result["missing_agency_name"] += not bool(i.get("agency"))
        result["missing_agency_code"] += not bool(i.get("agency_code"))
        status = str(i.get("status") or "").lower()
        result["forecasted"] += status == "forecasted"
        raw = str(i.get("close_date") or "").strip()
        if not raw:
            result["missing_close_date"] += 1
            result["held"] += 1
            continue
        try:
            close = dt.datetime.strptime(raw, "%m/%d/%Y").date()
        except ValueError:
            result["invalid_close_date"] += 1
            result["held"] += 1
            continue
        if close < today:
            result["close_date_before_today"] += 1
        if status == "posted" and close >= today and i.get("title") and i.get("agency_code"):
            result["potentially_current_posted"] += 1
        else:
            result["held"] += 1
    result["publication_approved"] = 0
    result["note"] = ("Potentially current is only a screening candidate. "
                      "Check official detail, eligibility, opportunity status, dates, "
                      "source URL and QA before database promotion or publication.")
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--as-of", default=dt.date.today().isoformat())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(json.loads(args.input.read_text(encoding="utf-8")),
                   dt.date.fromisoformat(args.as_of))
    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")

if __name__ == "__main__":
    main()
