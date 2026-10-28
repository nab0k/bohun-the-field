"""Bounded composition of existing Worker and real adapters. No schedule here."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from ..contracts import Document, aware, exact_fields, json_text, nonempty
from ..worker import Worker
from ...inoreader.intake_contract import PreparedBatch, validate_batch, uuid_string
from ...inoreader.intake_store import validate_receipt
from . import CONTRACT
from .http import CloudError
from .postgres import PostgresStore, PostgresQuota, PostgresOutbox


def verify_revision(expected_sha):
    root = Path(__file__).resolve().parents[3]
    if not isinstance(expected_sha, str) or re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None:
        raise CloudError("REVIEWED_SHA_REQUIRED")
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
        paths = ["collectors/intelligence_pipeline", "collectors/inoreader", "collectors/web_monitor", "scripts/intelligence-cloud.py", "scripts/intelligence-pipeline.py"]
        tracked = subprocess.check_output(["git", "ls-files", "--", *paths], cwd=root).decode().splitlines()
        actual = [str(p.relative_to(root)) for base in paths for p in
                  ((root / base).rglob("*.py") if (root / base).is_dir() else [root / base])]
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *paths], cwd=root).decode()
        if head != expected_sha or branch not in {"codex/exp009-cloud-pipeline", "HEAD"} or dirty or not set(actual) <= set(tracked):
            raise CloudError("REVIEWED_CLEAN_REVISION_REQUIRED")
    except (OSError, subprocess.CalledProcessError):
        raise CloudError("REVISION_CHECK_FAILED") from None


def validate_config(value):
    exact_fields(value, {"version", "approval_ref", "expected_sha", "run_scope", "enabled_actions",
                         "free_approval", "notion_parent", "max_ticks", "sources"})
    if value["version"] != CONTRACT:
        raise ValueError("OLD_RUNTIME_CONFIG_MIGRATION_REQUIRED")
    nonempty(value["approval_ref"])
    if not isinstance(value["run_scope"], str) or re.fullmatch(r"[a-zA-Z0-9_.-]{1,120}", value["run_scope"]) is None:
        raise ValueError("SCOPE_INVALID")
    if not isinstance(value["expected_sha"], str) or re.fullmatch(r"[0-9a-f]{40}",value["expected_sha"]) is None:
        raise ValueError("REVIEWED_SHA_REQUIRED")
    actions=value["enabled_actions"]
    if not isinstance(actions,list) or not actions or len(set(actions))!=len(actions) or not set(actions)<={"capture","model","notion"}:
        raise ValueError("ACTION_ALLOWLIST_REQUIRED")
    if type(value["max_ticks"]) is not int or not 1<=value["max_ticks"]<=15:
        raise ValueError("TICK_BOUND")
    if not isinstance(value["sources"],list) or not 1<=len(value["sources"])<=5:
        raise ValueError("SOURCE_BOUND")
    for source in value["sources"]:
        if not isinstance(source,dict):raise ValueError("SOURCE_CONFIG")
        if source.get("kind")=="inoreader":
            exact_fields(source,{"kind","batch_id","policy_ref","fetch_new"})
            uuid_string(source["batch_id"])
            if type(source["fetch_new"]) is not bool:raise ValueError("FETCH_FLAG")
        elif source.get("kind")=="web":
            exact_fields(source,{"kind","url","policy_ref","title","published_at"})
            from ..contracts import canonical_url
            if canonical_url(source["url"])!=source["url"]:raise ValueError("EXACT_WEB_URL_REQUIRED")
            nonempty(source["title"])
            aware(datetime.fromisoformat(source["published_at"].replace("Z","+00:00")))
        else:raise ValueError("SOURCE_KIND")
        nonempty(source["policy_ref"])
    from ..gemini import FreeApproval
    approval=FreeApproval(**value["free_approval"])
    if approval.account_scope!=value["run_scope"] or approval.mode!="FREE_ONLY":raise ValueError("FREE_ACCOUNT_SCOPE")
    if "notion" in actions:
        from .notion import page_id
        page_id(value["notion_parent"])
    return value


def collect_sources(config, rpc):
    """One bounded capture per configured source; policies are private DB data."""
    from ..source_adapters import inoreader_source, web_source, envelope, admit_source
    from ..contracts import digest
    store=PostgresStore(rpc,config["run_scope"]); result=[]
    if "capture" not in config["enabled_actions"]:raise ValueError("CAPTURE_NOT_APPROVED")
    for cfg in config["sources"]:
        profile=store.profile(cfg["policy_ref"])
        if profile["kind"]!=cfg["kind"] or not profile["enabled"]:raise ValueError("PROFILE_DISABLED_OR_KIND")
        if cfg["kind"]=="inoreader":
            if cfg["fetch_new"]:
                from ...inoreader.intake import capture_new,open_store
                capture_new(open_store(),cfg["batch_id"],config["expected_sha"],"intake-v1",max_items=1,require_body=True)
            value=rpc("intake_read",{"batch_id":cfg["batch_id"]})
            if value is None:continue  # no second fetch, ever
            manifest=validate_receipt(value["receipt"],cfg["batch_id"])
            batch=PreparedBatch(value["receipt"]["manifest_json"],value["receipt"]["manifest_sha256"],tuple(value["payloads"]))
            validate_batch(batch)
            for member,raw in zip(manifest["items"],batch.payloads):
                item=json.loads(raw)["item"]; capture=item["raw_data"]["content_capture"]
                if not item["published_at"] or not item["url"]:continue
                source=inoreader_source(raw,received_at=member["received_at"])
                html=capture.get("raw_html") or ""
                source,admission=admit_source(source,profile,html,kind="inoreader",provenance={
                    "batch_id":cfg["batch_id"],"payload_sha256":member["payload_sha256"]})
                result.append({"value":envelope(source,kind="inoreader",source_db_id=profile["source_db_id"],assessment_ref=profile["reference"]),
                               "raw_html":html,"admission":admission})
        else:
            from ...web_monitor.content import capture,download,WebPolicy
            from urllib.parse import urlsplit
            url=urlsplit(cfg["url"])
            if f"{url.scheme}://{url.netloc}"!=profile["origin"] or not url.path.startswith(profile["path_prefix"]):
                raise ValueError("WEB_PROFILE_BOUNDARY_BEFORE_FETCH")
            raw_html=[]
            def fetch(url,limit,**kwargs):
                value=download(url,limit,**kwargs)
                if kwargs.get("html"):raw_html.append(value)
                return value
            captured=capture(cfg["url"],WebPolicy((cfg["url"],),profile["rights_reference"],True),fetch=fetch)
            source=web_source(captured,source_id=profile["source_id"],title=cfg["title"],published_at=cfg["published_at"])
            html=raw_html[0]
            source,admission=admit_source(source,profile,html,kind="web",provenance={"capture_hash":digest(captured),"robots":"ALLOWED"})
            result.append({"value":envelope(source,kind="web",source_db_id=profile["source_db_id"],assessment_ref=profile["reference"]),
                           "raw_html":html,"admission":admission})
    if len(result)>5:raise ValueError("SOURCE_BATCH_BOUND")
    return result


def run(rpc, model, notion, scope, *, sources=None, ticks=15, clock=None):
    """The single coordinator; pure fixture transports can be supplied in tests."""
    from ..source_adapters import restore_source
    from ..retrieval import InternalRetriever
    if type(ticks) is not int or not 1<=ticks<=15 or not isinstance(sources,list) or len(sources)>5:
        raise ValueError("BOUNDED_RUNTIME_INPUT_REQUIRED")
    clock=clock or (lambda:datetime.now(timezone.utc))
    if model is not None:model._check()  # free proof before any RPC/source/model IO
    store,quota,outbox=PostgresStore(rpc,scope),PostgresQuota(rpc,scope),PostgresOutbox(rpc,scope)
    budget=quota.verify(model.approval if model is not None else None)
    if budget["disabled"] or budget["frozen"] or datetime.fromisoformat(budget["expires_at"].replace("Z","+00:00"))<=clock():
        raise CloudError("TRIAL_DISABLED_EXPIRED_OR_FROZEN")
    held=[];submitted=[]
    for item in sources:
        exact_fields(item,{"value","raw_html","admission"})
        source=restore_source(item["value"])
        receipt=store.receive(item["value"],item["raw_html"],item["admission"])
        if source.blockers:
            held.append({"source_key":source.identity,"reasons":list(source.blockers)});continue
        document=source.to_document();remaining=131072-len(document.text.encode())
        context=(InternalRetriever(store,max_bytes=min(65536,remaining)).retrieve(document,as_of=clock()) if remaining>=2 else
                 {"context_manifest":[],"context_status":"NO_CONTEXT_BYTE_BUDGET","coverage":{"market_absence_proven":False}})
        title=document.title.lower()
        category=next((name for name,terms in (("contract",("contract","tender","контракт","тендер")),("policy",("policy","budget","бюджет","закон")),("product",("product","prototype","продукт","прототип"))) if any(t in title for t in terms)),"change")
        submitted.append(store.submit(document,CONTRACT,evidence_receipt={"source_key":source.identity},context=context,category=category))
    steps=[]
    if model is not None:
        worker=Worker(store,model,quota,clock=clock,worker_id="auto09-unified",lease_seconds=120,max_attempts=1,quota_is_final=True)
        for _ in range(ticks):
            status=worker.tick()
            if status=="IDLE":break
            steps.append(status)
            if worker.halted_reason or status in {"STALE","STOPPED"}:break
    delivery=[]
    if notion is not None:
        for _ in range(5):
            row=outbox.claim()
            if row is None:break
            try:result=notion.deliver(row["packet"])
            except Exception:
                outbox.finish(row,"UNKNOWN");raise CloudError("DELIVERY_UNCERTAIN_READ_ONLY_RECOVERY") from None
            outbox.finish(row,result.status,result.page_id);delivery.append(result.status)
    budget=quota.verify(model.approval if model is not None else None)
    return {"contract":CONTRACT,"submitted":len(set(submitted)),"held":held,"steps":steps,"delivery":delivery,
            "calls":budget["calls"],"charged_reference_micro_usd":budget["charged_micro_usd"],"public_release":False}


def reconcile(rpc, notion, scope, key):
    if not isinstance(key,str) or re.fullmatch(r"[0-9a-f]{64}",key) is None:raise CloudError("OUTBOX_KEY_INVALID")
    outbox=PostgresOutbox(rpc,scope);row=outbox.uncertain(key)
    if row is None or row["state"]!="UNKNOWN":raise CloudError("OUTBOX_NOT_UNKNOWN")
    result=notion.deliver(row["packet"],reconcile_only=True)
    if result.status in {"DELIVERED","CONFLICT"}:outbox.reconcile(key,result.status,result.page_id)
    return {"delivery":result.status,"model_calls":0,"notion_posts":0}


def live_run(config, *, allow_live=False, expected_sha=None):
    import os
    from ..gemini import FreeApproval
    from .gemini import GeminiModel
    from .notion import NotionDelivery
    from .postgres import open_rpc
    config=validate_config(config)
    if allow_live is not True or config["expected_sha"]!=expected_sha:raise ValueError("EXPLICIT_REVIEWED_LIVE_REQUIRED")
    verify_revision(expected_sha)
    actions=set(config["enabled_actions"]);key=os.environ.get("GEMINI_API_KEY","")
    approval=FreeApproval(**config["free_approval"])
    if "model" in actions:approval.check(key,datetime.now(timezone.utc))
    if "notion" in actions and not os.environ.get("BOHUN_NOTION_TOKEN"):raise ValueError("LIMITED_NOTION_TOKEN_REQUIRED")
    rpc=open_rpc(os.environ["BOHUN_ANALYSIS_DSN"])
    model=GeminiModel(key,approval,enabled=True) if "model" in actions else None
    # Persisted approval is checked BEFORE capture/OAuth/web, as well as before run.
    budget=PostgresQuota(rpc,config["run_scope"]).verify(approval if model is not None else None)
    if budget['disabled'] or budget['frozen'] or datetime.fromisoformat(budget['expires_at'].replace('Z','+00:00'))<=datetime.now(timezone.utc):
        raise CloudError('TRIAL_DISABLED_EXPIRED_OR_FROZEN')
    sources=collect_sources(config,rpc)
    notion=NotionDelivery(os.environ["BOHUN_NOTION_TOKEN"],config["notion_parent"]) if "notion" in actions else None
    return run(rpc,model,notion,config["run_scope"],sources=sources,ticks=config["max_ticks"])
