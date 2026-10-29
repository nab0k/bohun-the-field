"""Offline event route with injected snapshots and writer fixtures, no live I/O."""
from datetime import timedelta
from . import PIPELINE_VERSION
from .contracts import canonical, sha
from .extraction import extract
from .editorial import (hard_bans, select_topic, source_entry, source_class, primary_candidates, event_identity,
                        event_match, date, duplicate_clusters)
from .context import context_packet
from .writer import request, validate


def run(documents, resources, *, catalog=(), context=None, writer=None, store=None, ner_predictions=None):
    docs = list(documents)
    if len({d.id for d in docs}) != len(docs):
        raise ValueError("DUPLICATE_DOCUMENT_ID")
    ner_predictions = ner_predictions or {}
    entries = []
    duplicates = []
    seen = set()
    for doc in sorted(docs, key=lambda d: (d.published_at, d.id)):
        identity = (doc.url, sha(doc.text))
        if identity in seen:
            duplicates.append(doc.id); continue
        seen.add(identity)
        ext = extract(doc, resources, catalog, ner_predictions.get(doc.id, ()))
        entries.append({"doc": doc, "extraction": ext, "identity": event_identity(ext, doc),
                        "bans": hard_bans(doc, ext, resources.dictionaries)})
    groups = []
    for entry in entries:
        group = next((g for g in groups if not entry["bans"] and not g[0]["bans"]
                      and event_match(g[0]["identity"], entry["identity"])), None)
        if group is None:
            groups.append([entry])
        else:
            group.append(entry)
    order = {"P0": 0, "P1": 1, "P2": 2, "unknown": 3}
    meta = [{"title": g[0]["doc"].title, "date": min(e["doc"].published_at for e in g),
             "banned": any(e["bans"] for e in g),
             "rank": (min(order[source_class(e["doc"], resources.dictionaries)] for e in g), -len(g),
                      min(e["doc"].published_at for e in g), g[0]["doc"].id)} for g in groups]
    representative = duplicate_clusters(meta)
    keys = [g[0]["identity"]["key"] for g in groups]
    outputs = []
    for index, group in enumerate(groups):
        first = group[0]
        bans = sorted(set(c for e in group for c in e["bans"]))
        facts = [f for e in group for f in e["extraction"]["facts"]]
        sources = [source_entry(e["doc"], resources.dictionaries) for e in group]
        missing = [] if any(not e["extraction"]["missing"] for e in group) else sorted(set(
            k for e in group for k in e["extraction"]["missing"]))
        topic = select_topic(first["doc"].title, facts, resources.topics)
        reasons = list(bans)
        if not bans:
            if missing:
                reasons.append("extraction_gap")
            if not topic["main"]:
                reasons.append("topic_missing")
            if not any(s["class"] in {"P0", "P1"} for s in sources):
                reasons.append("p2_only" if all(s["class"] == "P2" for s in sources) else "source_unassessed")
        # Do not collapse differing amounts from distinct statements inside one
        # document into a fake conflict. Compare only main-actor title evidence.
        sums = [e["identity"]["amount"] for e in group if e["identity"]["amount"]]
        if sums and any(a["currency"] != sums[0]["currency"] or
                        abs(float(a["value"])-float(sums[0]["value"]))/max(float(sums[0]["value"]), 1) > .05 for a in sums[1:]):
            reasons.append("source_conflict")
        first_date = min(d["date"] for d in sources)
        for source in sources:
            if date(source["date"]) - date(first_date) > timedelta(days=3) and source["role"] != "primary":
                source["role"] = "rewrite"
        ctx = context_packet(context or {}, topic["main"]["slug"], first_date) if topic["main"] else []
        if set(f["id"] for f in facts) & set(f["id"] for f in ctx):
            raise ValueError("CONTEXT_FACT_ID_COLLISION")
        packet = {"event": {"type": first["extraction"]["type"], "first_published_at": first_date,
                            "topic": topic["main"]["title"] if topic["main"] else None,
                            "is_rewrite": any(s["role"] == "rewrite" for s in sources), "parent_event_title": None},
                  "facts": facts+ctx, "sources": sources}
        duplicate_of = None
        if representative[index] != index:
            # Unclear duplicate: kept as its own event, but only one group per cluster reaches the writer.
            duplicate_of = keys[representative[index]]
            reasons.append("duplicate_candidate")
        checks = None
        writer_output = None
        if not reasons:
            if writer is None:
                reasons.append("writer_not_run")
            else:
                try:
                    writer_output = writer(request(packet, resources))
                    checks = validate(writer_output, packet, resources.dictionaries)
                    if not checks["ok"]:
                        reasons.append("text_mismatch")
                    if not writer_output["why_it_matters"]:
                        # writer_prompt_v2: no supported implication -> empty block, never invented.
                        reasons.append("why_unsupported")
                except (ValueError, TypeError, KeyError):
                    reasons.append("text_mismatch")
        decision = "REJECT" if bans else "HOLD" if reasons else "PUBLISH"
        result = {"event_key": first["identity"]["key"], "document_ids": [e["doc"].id for e in group],
                  "title": first["doc"].title, "procedure_ref": first["identity"]["procedure_ref"],
                  "type": first["extraction"]["type"], "topic": topic,
                  "key_facts_complete": not missing, "missing": missing,
                  "duplicate_of": duplicate_of, "packet": packet, "writer_output": writer_output, "text_checks": checks,
                  "admission": {"decision": decision, "reason_codes": sorted(set(reasons)),
                                "checker": "auto-admission", "rules_version": "synthesis-v2.1",
                                "public_release": False, "simulation_only": True},
                  "primary_candidates": {e["doc"].id: primary_candidates(e["doc"], resources.dictionaries) for e in group},
                  "primary_fetches": 0, "cost_micro_usd": 0, "mode": "offline"}
        if store is not None:
            store.save(result["event_key"], result, PIPELINE_VERSION)
        outputs.append(result)
    return {"pipeline_version": PIPELINE_VERSION, "mode": "offline", "resource_pins": resources.pins(),
            "input_documents": len(docs), "duplicate_document_ids": duplicates, "events": outputs,
            "network_calls": 0, "model_calls": 0, "db_writes": 0, "cost_micro_usd": 0,
            "limitations": ["NO_NATIVE_NER_UNLESS_SAVED_SPANS_SUPPLIED", "NO_PRIMARY_FETCH", "NO_LIVE_WRITER", "NO_DATABASE_ADAPTER",
                            "NO_PARENT_EVENT_INFERENCE", "NO_SEMANTIC_ENTAILMENT_PROOF"]}
