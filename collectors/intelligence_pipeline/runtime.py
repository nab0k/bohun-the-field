"""Bounded cloud coordinator. No network at import; explicit reviewed live entrypoint."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from .contracts import aware, exact_fields, json_text, nonempty
from .gemini import access_approval, GeminiModel
from .notion import NotionDelivery, page_id
from .postgres import PostgresQuota, PostgresStore, RuntimeAPI, connect_factory
from .retrieval import InternalRetriever
from .source_adapters import Assessment, envelope, inoreader_source, restore_source, web_source
from .worker import Worker

RUNTIME_VERSION = "exp009-runtime-input29-v3"


def validate_config(value):
    exact_fields(value, {"version", "approval_ref", "expected_sha", "run_scope", "enabled_actions",
                         "model_access", "notion_parent", "max_ticks", "sources"})
    if value["version"] != RUNTIME_VERSION or type(value["max_ticks"]) is not int or not 1 <= value["max_ticks"] <= 15:
        raise ValueError("RUNTIME_VERSION_OR_TICK_LIMIT")
    nonempty(value["approval_ref"]); nonempty(value["run_scope"])
    actions = value["enabled_actions"]
    if not isinstance(actions, list) or not actions or len(set(actions)) != len(actions) or not set(actions) <= {"capture", "model", "notion"}:
        raise ValueError("RUNTIME_ACTION_ALLOWLIST_REQUIRED")
    if not isinstance(value["sources"], list) or not 1 <= len(value["sources"]) <= 5:
        raise ValueError("RUNTIME_SOURCE_LIMIT")
    for source in value["sources"]:
        if not isinstance(source, dict) or source.get("kind") not in {"inoreader", "web"}:
            raise ValueError("RUNTIME_SOURCE_KIND")
    if "notion" in actions:
        page_id(value["notion_parent"])
    approval = access_approval(value["model_access"])
    if approval.account_scope != value["run_scope"]:
        raise ValueError("SHARED_ACCOUNT_QUOTA_SCOPE_REQUIRED")
    return value


class Coordinator:
    def __init__(self, store, model, quota, delivery, *, clock=None):
        self.store, self.model, self.quota, self.delivery = store, model, quota, delivery
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, sources, *, max_ticks=15, model_enabled=False, notion_enabled=False):
        if not isinstance(sources, list) or not 1 <= len(sources) <= 5 or not 1 <= max_ticks <= 15:
            raise ValueError("RUNTIME_BOUNDS")
        submitted, held = [], []
        for value in sources:
            source = restore_source(value)
            self.store.receive(value)
            if source.blockers:
                held.append({"source_key":source.identity, "reasons":list(source.blockers)})
                continue
            document = source.to_document()
            remaining = 128 * 1024 - len(document.text.encode())
            context = (InternalRetriever(self.store, max_bytes=min(65536, remaining)).retrieve(document, as_of=self.clock())
                       if remaining >= 2 else {"context_manifest":[], "context_status":"NO_CONTEXT_BYTE_BUDGET",
                                              "coverage":{"market_absence_proven":False}})
            # A lead/reference is never promoted to a verified company.
            submitted.append(self.store.submit_with_context(document, RUNTIME_VERSION, context,
                                                            category=_category(document.title)))
        statuses = []
        if model_enabled:
            worker = Worker(self.store, self.model, self.quota, clock=self.clock,
                            worker_id="exp009-runtime", max_attempts=1, quota_is_final=True)
            for _ in range(max_ticks):
                status = worker.tick(); statuses.append(status)
                if status in {"IDLE", "STOPPED"} or worker.halted_reason:
                    break
        delivered = []
        if notion_enabled:
            for packet in self.store.ready(limit=5):
                record = self.store.propose(packet)
                result = self.delivery.deliver(record)
                delivered.append({"material_id":record["material"]["material_id"],
                                  "version":record["material"]["version"], "state":result["state"]})
        return {"runtime_version":RUNTIME_VERSION, "submitted":len(set(submitted)), "held":held,
                "steps":statuses, "deliveries":delivered, "public_release":False}


def _category(title):
    t = title.lower()
    if any(s in t for s in ("contract", "tender", "контракт", "тендер")):
        return "contract"
    if any(s in t for s in ("policy", "budget", "бюджет", "закон")):
        return "policy"
    if any(s in t for s in ("product", "prototype", "продукт", "прототип")):
        return "product"
    return "change"


def collect_sources(config):
    """Only explicitly enabled capture, exact source configs, never follow model URLs."""
    if "capture" not in config["enabled_actions"]:
        raise ValueError("CAPTURE_ACTION_NOT_APPROVED")
    values = []
    for cfg in config["sources"]:
        if cfg["kind"] == "inoreader":
            exact_fields(cfg, {"kind", "batch_id", "assessments"})
            from collectors.inoreader.intake import capture_new, open_store
            store = open_store()
            capture_new(store, cfg["batch_id"], config["expected_sha"], "intake-v1",
                        max_items=1, require_body=True)
            stored = store.read(cfg["batch_id"])
            if stored is None:
                continue  # No-body strict capture wrote nothing. Never refetch here.
            for payload, member in zip(stored[0].payloads, json.loads(stored[0].manifest_json)["items"]):
                source = inoreader_source(payload, received_at=member["received_at"])
                approval = cfg["assessments"].get(source.text_hash)
                if approval:
                    source = Assessment(**approval).apply(source)
                values.append(envelope(source, kind="inoreader", source_db_id=2,
                                       assessment_ref=approval["reference"] if approval else "unassessed"))
        else:
            exact_fields(cfg, {"kind", "url", "policy", "source_id", "source_db_id", "title", "published_at", "assessment"})
            from collectors.web_monitor.content import capture, WebPolicy
            policy = dict(cfg["policy"]); policy["allowed_urls"] = tuple(policy["allowed_urls"])
            raw = capture(cfg["url"], WebPolicy(**policy))
            assessment = Assessment(**cfg["assessment"]) if cfg["assessment"] else None
            source = web_source(raw, source_id=cfg["source_id"], title=cfg["title"],
                                published_at=cfg["published_at"], assessment=assessment)
            values.append(envelope(source, kind="web", source_db_id=cfg["source_db_id"],
                                   assessment_ref=assessment.reference if assessment else "unassessed"))
    return values


def live_run(config, *, allow_live=False, expected_sha=None):
    config = validate_config(config)
    if allow_live is not True or config["expected_sha"] != expected_sha:
        raise ValueError("RUNTIME_EXPLICIT_REVISION_APPROVAL_REQUIRED")
    from collectors.inoreader.probe import verify_revision
    verify_revision(expected_sha)  # Current feature, committed tracked tree, no dirty code.
    root = Path(__file__).resolve().parents[2]
    required = [str(p.relative_to(root)) for p in (root/'collectors/intelligence_pipeline').glob('*.py')]
    required += ['scripts/intelligence-pipeline.py','collectors/web_monitor/content.py']
    try:
        subprocess.check_output(['git','ls-files','--error-unmatch',*required],cwd=root,stderr=subprocess.DEVNULL)
    except (OSError,subprocess.CalledProcessError):
        raise ValueError("RUNTIME_ALL_ENTRYPOINTS_MUST_BE_COMMITTED") from None
    actions = set(config["enabled_actions"])
    key = os.environ.get("GEMINI_API_KEY", "")
    approval = access_approval(config["model_access"])
    if "model" in actions:
        approval.check(key, datetime.now(timezone.utc))  # Before DB/API/source I/O.
    if "notion" in actions and not os.environ.get("BOHUN_NOTION_TOKEN"):
        raise ValueError("LIMITED_NOTION_TOKEN_REQUIRED")
    dsn = os.environ.get("BOHUN_RUNTIME_DSN")
    if not dsn:
        raise ValueError("LIMITED_RUNTIME_DSN_REQUIRED")
    store = PostgresStore(RuntimeAPI(connect_factory(dsn)), config["run_scope"])
    quota = PostgresQuota(store.api, config["run_scope"], approval)
    if "model" in actions:
        quota.verify()  # Binding before capture or model I/O.
    model = GeminiModel(key, approval, enabled="model" in actions)
    delivery = (NotionDelivery(store, os.environ.get("BOHUN_NOTION_TOKEN", ""), config["notion_parent"], enabled=True)
                if "notion" in actions else None)
    sources = collect_sources(config)
    if not sources:
        return {"runtime_version":RUNTIME_VERSION, "result":"NO_SOURCE_BODY", "public_release":False}
    return Coordinator(store, model, quota, delivery).run(sources, max_ticks=config["max_ticks"],
        model_enabled="model" in actions, notion_enabled="notion" in actions)
