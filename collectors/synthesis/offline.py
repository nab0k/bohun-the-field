"""Private A-06 replay. CLI accepts offline only and never constructs live adapters."""
import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import socket
from unittest.mock import patch

from .contracts import Document, Resources, canonical, sha
from .route import run
from .store import MemoryStore
from .writer import FixtureWriter


def load_inputs(selection, corpus):
    gold = json.loads(Path(selection).read_text(encoding="utf-8"))
    raw = json.loads(Path(corpus).read_text(encoding="utf-8"))
    by_id = {d["id"]: d for d in raw}
    if len(by_id) != len(raw):
        raise ValueError("CORPUS_DUPLICATE_ID")
    docs = []
    seen = set()
    for event in gold["events"]:
        for ref in event["documents"]:
            row = by_id[ref["id"]]
            if row["url"] != ref["url"] or sha(row["text"]) != ref["text_sha256"]:
                raise ValueError("CONTROL_INPUT_PIN_MISMATCH")
            if ref["id"] in seen:
                continue
            seen.add(ref["id"])
            stamp = row["published"]
            if isinstance(stamp, (int, float)):
                stamp = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
            # FULL is a capture completeness label, never a QA/source class.
            docs.append(Document(row["id"], row["title"], row["text"], row["url"], stamp,
                                 row["feed"], row.get("source_class", "unknown")))
    return gold, docs


def evaluate(gold, result):
    # Only this evaluator sees expected event types/decisions/groups. The route
    # has already finished; no expected labels were passed to it.
    rows = []
    for g in gold["events"]:
        ids = {d["id"] for d in g["documents"]}
        found = [r for r in result["events"] if ids & set(r["document_ids"])]
        rows.append({"event": g["event"], "expected_decision": g["expected_decision"],
                     "expected_type": g["event_type"], "types": sorted(set(x["type"] for x in found)),
                     "key_facts_complete": any(x["key_facts_complete"] and x["type"] == g["event_type"] for x in found),
                     "decisions": sorted(set(x["admission"]["decision"] for x in found)),
                     "codes": sorted(set(c for x in found for c in x["admission"]["reason_codes"])),
                     "route_groups": len(found), "missing": sorted(set(k for x in found for k in x["missing"]))})
    publishable = [r for r in rows if r["expected_decision"] == "PUBLISH"]
    rejected = next(r for r in rows if r["event"] == "E10")
    return {"rows": rows, "key_facts_complete": sum(r["key_facts_complete"] for r in publishable),
            "denominator": len(publishable), "E10_rejected": rejected["decisions"] == ["REJECT"],
            "AC1_pass": sum(r["key_facts_complete"] for r in publishable) >= 7 and rejected["decisions"] == ["REJECT"],
            "writer_quality": "NOT_RUN", "live_acceptance": "NOT_RUN"}


def private_output(path):
    resolved = Path(path).resolve()
    repo = Path(__file__).resolve().parents[2]
    if resolved == repo or repo in resolved.parents:
        raise ValueError("PRIVATE_OUTPUT_MUST_BE_OUTSIDE_REPOSITORY")
    if any(part in {"public", "dist", ".git"} for part in resolved.parts):
        raise ValueError("PRIVATE_OUTPUT_REQUIRED")
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline"], default="offline")
    for name in ("selection", "corpus", "topics", "dictionaries", "prompt", "output"):
        parser.add_argument("--"+name, required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--writer-fixtures")
    parser.add_argument("--context")
    parser.add_argument("--ner-predictions")
    args = parser.parse_args(argv)
    out = private_output(args.output)
    # Accidental transitive networking fails even if a supplied fixture changes.
    with patch.object(socket, "socket", side_effect=RuntimeError("OFFLINE_NETWORK_DISABLED")), \
         patch.object(socket, "create_connection", side_effect=RuntimeError("OFFLINE_NETWORK_DISABLED")):
        gold, docs = load_inputs(args.selection, args.corpus)
        resources = Resources.load(args.topics, args.dictionaries, args.prompt)
        catalog = json.loads(Path(args.catalog).read_text()) if args.catalog else []
        context = json.loads(Path(args.context).read_text()) if args.context else {}
        predictions = json.loads(Path(args.ner_predictions).read_text()) if args.ner_predictions else {}
        writer = FixtureWriter(json.loads(Path(args.writer_fixtures).read_text())) if args.writer_fixtures else None
        memory = MemoryStore()
        result = run(docs, resources, catalog=catalog, context=context, writer=writer, store=memory, ner_predictions=predictions)
        repeat = run(docs, resources, catalog=catalog, context=context, writer=writer, store=memory, ner_predictions=predictions)
        assert canonical(result) == canonical(repeat), "NONDETERMINISTIC_OFFLINE_RESULT"
        assert all(v["version"] == 1 for v in memory.events.values()), "REPLAY_CREATED_VERSION"
        result["evaluation"] = evaluate(gold, result)
        result["deterministic"] = True
        result["provenance"] = {n: sha(Path(getattr(args, n)).read_text()) for n in
                                ("selection", "corpus", "topics", "dictionaries", "prompt")}
        for n in ("catalog", "writer_fixtures", "context", "ner_predictions"):
            if getattr(args, n):
                result["provenance"][n] = sha(Path(getattr(args, n)).read_text())
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = out / "result.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    target.chmod(0o600)
    rows = result["evaluation"]["rows"]
    table = "".join("<tr>"+"".join("<td>"+html.escape(str(r[k]))+"</td>" for k in
                          ("event", "key_facts_complete", "types", "decisions", "codes", "missing"))+"</tr>" for r in rows)
    page = out / "review.html"
    page.write_text('<!doctype html><meta charset="utf-8"><title>A-07 offline</title>'
                   '<style>body{font:16px system-ui;max-width:1200px;margin:40px auto}td,th{padding:12px;border:1px solid #ddd}table{border-collapse:collapse}</style>'
                   '<h1>A-07 — offline A-06</h1><p>Private local review. No live writer, database writes or publication.</p>'
                   '<p>Key extraction: '+str(result["evaluation"]["key_facts_complete"])+'/'+str(result["evaluation"]["denominator"])+
                   '. Deterministic replay: PASS. Raw source excerpts remain in private result.json.</p>'
                   '<table><tr><th>Control</th><th>Key facts</th><th>Types</th><th>Offline decision</th><th>Codes</th><th>Missing</th></tr>'+table+'</table>')
    page.chmod(0o600)
    print(canonical({"AC1": result["evaluation"]["AC1_pass"], "key_facts": result["evaluation"]["key_facts_complete"],
                     "denominator": result["evaluation"]["denominator"], "E10_rejected": result["evaluation"]["E10_rejected"],
                     "network_calls": 0, "model_calls": 0, "db_writes": 0, "cost_micro_usd": 0}))
    return 0 if result["evaluation"]["AC1_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
