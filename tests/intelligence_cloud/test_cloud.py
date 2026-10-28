"""Synthetic fault injection for real adapters. NOT PostgreSQL/live acceptance."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from collectors.intelligence_pipeline.cloud import CONTRACT, MODEL, cost
from collectors.intelligence_pipeline.cloud.gemini import GeminiModel, request_for
from collectors.intelligence_pipeline.cloud.http import CloudError, JsonHTTP, Reply
from collectors.intelligence_pipeline.cloud.notion import NotionDelivery, render
from collectors.intelligence_pipeline.cloud.postgres import PostgresRPC, PostgresStore, PostgresQuota
from collectors.intelligence_pipeline.cloud.runtime import run, collect_sources, reconcile
from collectors.intelligence_pipeline.contracts import Document, JobSpec, Usage, digest, json_text
from collectors.intelligence_pipeline.gemini import FreeApproval
import hashlib
from collectors.intelligence_pipeline.ports import LeaseLost, QuotaExceeded
from collectors.intelligence_pipeline.testing import MemoryStore
from collectors.intelligence_pipeline.worker import Worker

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"


def document(**kw):
    return replace(Document("synthetic1", "https://example.org/synthetic", "Synthetic public notice",
        "The notice announced planned output of 20 units.", "2026-10-19T10:00:00Z",
        "2026-10-19T11:00:00Z", "announced", True, True, False), **kw)


def profile(kind="inoreader"):
    from collectors.intelligence_pipeline.source_adapters import POLICY_VERSION
    return {"version":POLICY_VERSION,"reference":"fixture-"+kind,"source_id":"inoreader:2" if kind=="inoreader" else "web:fixture",
        "source_db_id":2 if kind=="inoreader" else 3,"kind":kind,"origin":"https://example.org","path_prefix":"/synthetic",
        "extractor":"htmlparser-text-v1" if kind=="inoreader" else "trafilatura-2.3.1",
        "start_marker":"<article>","end_marker":"</article>","blocked_markers":["subscribe to continue","sensitive facility"],
        "min_text_bytes":10,"max_text_bytes":4096,"rights_reference":"SYNTHETIC-rights","safety_reference":"SYNTHETIC-safe",
        "source_stage":"announced","enabled":True}


def approval(key="key",scope="trial"):
    return FreeApproval(scope,"fixture-free-proof",hashlib.sha256(key.encode()).hexdigest(),"2026-10-20T00:00:00+00:00")


def model(key,http,scope="trial"):
    from urllib.parse import urlsplit
    def transport(method,url,headers,body):
        reply=http.request(method,urlsplit(url).path,headers,body)
        if reply.status in (401,403,429):
            from collectors.intelligence_pipeline.gemini import ModelAccessStop
            raise ModelAccessStop("FREE_QUOTA_STOP")
        if reply.status!=200:raise RuntimeError("HTTP_RESULT_UNCERTAIN")
        return reply.payload
    return GeminiModel(key,approval(key,scope),enabled=True,transport=transport,clock=lambda:NOW)


def dossier():
    return {"methods":["ChangeIntelligence","ACH"],"objects":["Example Works"],
       "change":{"before":"UNKNOWN","after":"Announced plan","demand_type":"unknown"},
       "observations":[{"statement":"A plan is reported.","claim_ids":["f1"],"context_refs":[]}],
       "hypotheses":[{"statement":"Capacity may change.","claim_ids":["f1"],"context_refs":[],
         "counterhypothesis":"Replacement, not added capacity.","confirmation":"Output report.","refutation":"Cancellation.","review_at":"2026-12-31T00:00:00Z"}],
       "forecasts":[],"gaps":[{"question":"Baseline?","scope":"supplied documents","status":"NOT_SEARCHED"}]}


def outputs(doc=None):
    doc = doc or document()
    claim = {"id": "f1", "text": "Planned output is 20 units.", "quote": doc.text,
             "stage": "announced", "entity_id": None, "entity_status": "not_applicable"}
    draft = {"title": "Synthetic candidate", "body": "[f1] Объявлен план. Реализация пока неизвестна.",
             "why_it_matters": "При реализации предложение может вырасти; это интерпретация.",
             "alternatives": ["План может быть отложен."], "unknowns": ["Сроки неизвестны."], "claim_ids": ["f1"]}
    return [{"document_hash": doc.text_hash, "claims": [claim]}, {"draft":draft,"dossier":dossier()},
            {"draft_hash": digest({"draft":draft,"dossier":dossier()}), "issues": [], "comment": "Only an editorial proposal."}]


def packet():
    doc = document()
    return {"material_id": digest([doc.identity, CONTRACT]), "editorial_status": "PROPOSED",
            "draft": outputs()[1]["draft"], "dossier":dossier(), "context_manifest":[], "coverage":{}, "claims": outputs()[0]["claims"], "sources": [{"url": doc.url,
            "published_at": doc.published_at, "fetched_at": doc.fetched_at, "text_hash": doc.text_hash}],
            "warnings": ["CONTEXT_NOT_RETRIEVED"], "qa": {"decision": "HOLD", "public_release": False}}


def provider(payload, **changes):
    value = {"modelVersion": MODEL, "serviceTier": "standard", "usageMetadata": {
        "promptTokenCount": 100, "candidatesTokenCount": 11, "thoughtsTokenCount": 3, "totalTokenCount": 114},
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json_text(payload)}]}}]}
    value.update(changes)
    return value


class ModelHTTP:
    def __init__(self, values):
        self.values, self.calls = list(values), []

    def request(self, *args):
        self.calls.append(args)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, Reply) else Reply(200, value)


class NotionHTTP:
    def __init__(self, fault=None):
        self.pages, self.calls, self.fault = {}, [], fault

    def request(self, method, path, headers, body=None):
        self.calls.append((method, path, body))
        if method == "POST":
            pid=CHILD if not self.pages else "33333333-3333-4333-8333-333333333333"
            self.pages[pid] = deepcopy(body)
            if self.fault == "timeout_after_commit":
                raise CloudError("HTTP_RESULT_UNCERTAIN")
            if self.fault == "503_after_commit":
                return Reply(503, {"additional_data": {"committed_resource_id": pid}})
            return Reply(200, {"id": pid})
        if path.startswith(f"/v1/blocks/{PARENT}/children"):
            return Reply(200, {"has_more": False, "results": [{"id": pid, "type": "child_page",
                "child_page": {"title": p["properties"]["title"]["title"][0]["text"]["content"]}}
                for pid, p in self.pages.items()]})
        pid = path.split('/')[3]
        if path.startswith("/v1/pages/"):
            return Reply(200, {"parent": {"type": "page_id", "page_id": PARENT}})
        return Reply(200, {"has_more": False, "results": self.pages[pid]["children"]})


class RPCServer:
    """In-memory fake RPC server for Python wiring ONLY; never emulates SQL proof."""
    def __init__(self):
        self.store, self.usage, self.outbox = MemoryStore(), {}, {}
        self.calls = []
        self.disabled = False
        self.snapshots = {}; self.profiles={"fixture-inoreader":profile(),"fixture-web":profile("web")}; self.context_records=[]

    def __call__(self, action, p):
        self.calls.append((action, deepcopy(p)))
        if action=="profile":return deepcopy(self.profiles[p["reference"]])
        if action=="context":return deepcopy(self.context_records[:p["limit"]])
        if action=="receive":
            from collectors.intelligence_pipeline.source_adapters import restore_source,admit_source
            core=json.loads(p['payload']);value=core|{'receipt':p['receipt']};source=restore_source(value)
            pol=self.profiles[core['assessment_ref']]
            checked,machine=admit_source(source,pol,p['raw_html'],kind=core['kind'],provenance=p['admission']['provenance'])
            if checked!=source or machine!=p['admission']:raise CloudError('ADMISSION_MISMATCH')
            self.snapshots.setdefault(source.identity,value)
            return {'source_key':source.identity,'accepted':machine['accepted']}
        if action == "intake_read":
            return deepcopy(self.intake_value)
        if action == "budget":
            return {"contract": CONTRACT, "model": MODEL, "cap_micro_usd": 1_000_000,
                "calls": len(self.usage), "charged_micro_usd": sum(v["charged"] for v in self.usage.values()),
                "billing_mode":"FREE_ONLY","free_approval_ref":approval().reference,"key_sha256":approval().key_sha256,
                "free_expires_at":approval().expires_at,"frozen": False, "disabled": self.disabled, "expires_at": (NOW + timedelta(hours=1)).isoformat()}
        if action == "submit":
            d = json.loads(p["document_json"]); d["verified_entity_ids"] = tuple(d["verified_entity_ids"])
            for row in self.store._jobs.values():
                if row['spec'].document_key==p['document_key'] and row['spec'].stage=='extract' and row['spec'].pipeline_version==p['pipeline_version']:
                    return {'job_id':row['spec'].job_id}
            return {"job_id": self.store._enqueue(JobSpec(p["document_key"],"extract",p["input_json"],p["pipeline_version"]))}
        if action == "claim":
            lease = self.store.claim(p["worker"], NOW, p["seconds"])
            if lease is None:
                return None
            self.last_lease = lease
            return {"job_id": lease.spec.job_id, "document_key": lease.spec.document_key,
                "stage": lease.spec.stage, "input_json": lease.spec.input_json,
                "pipeline_version": lease.spec.pipeline_version, "token": lease.token,
                "attempt": lease.attempt, "expires_at": lease.expires_at.isoformat()}
        if action == "finish":
            from collectors.intelligence_pipeline.contracts import StageResult
            lease = self.last_lease
            assert p["token"] == lease.token
            nxt = p["successor"]
            successor = None if nxt is None else JobSpec(lease.spec.document_key,nxt["stage"],nxt["input_json"],lease.spec.pipeline_version)
            self.store.finish(lease, NOW, StageResult(p["status"],p["output_json"],tuple(p["reasons"])),successor)
            if p["outbox_key"]:
                self.outbox.setdefault(p["outbox_key"], {"key": p["outbox_key"], "packet": json.loads(p["output_json"]), "state": "PENDING", "token": "delivery1"})
            return {}
        if action == "reserve":
            if sum(x["charged"] for x in self.usage.values()) + p["reserved"] > 1_000_000 or p["request_id"] in self.usage:
                raise QuotaExceeded("TRIAL_BUDGET_BLOCKED")
            self.usage[p["request_id"]] = {"charged": p["reserved"], "state": "RESERVED"}
            return {}
        if action in {"dispatch", "unknown", "settle"}:
            row = self.usage[p["request_id"]]
            row["state"] = {"dispatch": "DISPATCHED", "unknown": "UNKNOWN", "settle": "SETTLED"}[action]
            if action == "settle":
                row["charged"] = p["actual"]
                return {"over": False}
            return {}
        if action == "delivery_claim":
            for row in self.outbox.values():
                if row["state"] == "PENDING":
                    row["state"] = "DELIVERING"
                    return deepcopy(row)
            return None
        if action == "delivery_finish":
            row = self.outbox[p["key"]]
            row["state"], row["page_id"] = p["status"], p["page_id"]
            return {}
        if action == "delivery_read":
            return deepcopy(self.outbox.get(p["key"]))
        if action == "delivery_reconciled":
            row=self.outbox[p["key"]]
            assert row["state"]=="UNKNOWN"
            row["state"],row["page_id"]=p["status"],p["page_id"]
            return {}
        if action == "defer":
            self.store.defer(self.last_lease, NOW, datetime.fromisoformat(p["due"]),p["reason"])
            return {}
        raise AssertionError(action)


class Offline(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.create_connection", side_effect=AssertionError("NETWORK_FORBIDDEN"))
        guard.start(); self.addCleanup(guard.stop)


class GeminiTests(Offline):
    def test_exact_endpoint_full_request_budget_and_thought_charge(self):
        http = ModelHTTP([provider(outputs()[0])]); instance = model("PRIVATE_KEY_SENTINEL", http)
        p = {"document": document().to_dict()}
        estimate = instance.estimate("extract", p)
        self.assertEqual(cost(estimate), 329573)
        reply = instance.execute("extract", p, "request1", estimate)
        self.assertEqual(reply.usage, Usage(100, 14, 1))
        self.assertEqual(len(http.calls), 1)
        method, path, headers, body = http.calls[0]
        self.assertNotIn("PRIVATE_KEY_SENTINEL", path)
        self.assertEqual(path, f"/v1beta/models/{MODEL}:generateContent")
        self.assertEqual(body["generationConfig"]["candidateCount"],1)
        self.assertNotIn("tools", body)
        self.assertEqual(body["serviceTier"], "standard")

    def test_ineligible_and_oversize_rejected_before_http(self):
        http = ModelHTTP([]); instance = model("key", http)
        for p in ({"document":document(complete=False).to_dict()},
                  {"document":document(sensitive=True).to_dict(),"requires_dossier":True},
                  {"document":document().to_dict(),"context":"x"*600000}):
            with self.assertRaises((ValueError,RuntimeError)): instance.estimate("extract",p)
        self.assertFalse(http.calls)

    def test_unknown_usage_truncation_wrong_tier_and_tools_never_retry(self):
        base = provider(outputs()[0])
        bad = []
        for key in ("usageMetadata", "modelVersion"):
            p = deepcopy(base); del p[key]; bad.append(p)
        p=deepcopy(base); p["usageMetadata"]["totalTokenCount"]=999; bad.append(p)
        p=deepcopy(base); p["candidates"][0]["finishReason"]="MAX_TOKENS"; bad.append(p)
        p=deepcopy(base); p["serviceTier"]="priority"; bad.append(p)
        p=deepcopy(base); p["candidates"][0]["content"]["parts"]=[{"functionCall":{"name":"x"}}]; bad.append(p)
        for p in bad:
            http=ModelHTTP([p]); instance=model("secret",http); data={"document":document().to_dict()}
            with self.assertRaises(RuntimeError):
                instance.execute("extract",data,"r",instance.estimate("extract",data))
            self.assertEqual(len(http.calls),1)

    def test_missing_zero_thoughts_proved_by_total(self):
        p=provider(outputs()[0]); del p["usageMetadata"]["thoughtsTokenCount"]; p["usageMetadata"]["totalTokenCount"]=111
        instance=model("key",ModelHTTP([p])); data={"document":document().to_dict()}
        self.assertEqual(instance.execute("extract",data,"r",instance.estimate("extract",data)).usage.output_tokens,11)


class NotionTests(Offline):
    def test_create_then_readback_then_duplicate_no_second_post(self):
        http=NotionHTTP(); adapter=NotionDelivery("secret",PARENT,http)
        self.assertEqual(adapter.deliver(packet()).status,"DELIVERED")
        self.assertEqual(adapter.deliver(packet()).status,"DELIVERED")
        self.assertEqual(sum(c[0]=="POST" for c in http.calls),1)

    def test_committed_timeout_reconciles_by_read_only(self):
        http=NotionHTTP("timeout_after_commit"); adapter=NotionDelivery("secret",PARENT,http)
        self.assertEqual(adapter.deliver(packet()).status,"UNKNOWN")
        self.assertEqual(adapter.deliver(packet(),reconcile_only=True).status,"DELIVERED")
        self.assertEqual(sum(c[0]=="POST" for c in http.calls),1)

    def test_503_committed_id_is_read_not_repeated(self):
        http=NotionHTTP("503_after_commit")
        self.assertEqual(NotionDelivery("secret",PARENT,http).deliver(packet()).status,"DELIVERED")
        self.assertEqual(sum(c[0]=="POST" for c in http.calls),1)

    def test_human_edit_conflict_never_overwrites(self):
        http=NotionHTTP(); adapter=NotionDelivery("secret",PARENT,http); adapter.deliver(packet())
        http.pages[CHILD]["children"][3]["paragraph"]["rich_text"][0]["text"]["content"]="HUMAN EDIT"
        before=deepcopy(http.pages)
        self.assertEqual(adapter.deliver(packet()).status,"CONFLICT")
        self.assertEqual(http.pages,before)
        self.assertEqual(sum(c[0]=="POST" for c in http.calls),1)

    def test_uncertain_absence_never_creates_and_public_packet_refused(self):
        http=NotionHTTP(); adapter=NotionDelivery("secret",PARENT,http)
        self.assertEqual(adapter.deliver(packet(),reconcile_only=True).status,"UNKNOWN")
        p=packet(); p["qa"]["public_release"]=True
        with self.assertRaises(CloudError): adapter.deliver(p)
        self.assertFalse(any(c[0]=="POST" for c in http.calls))

    def test_split_blocks_and_large_output_fail_before_network(self):
        p=packet(); p["draft"]["body"]="я"*8000
        _,texts,_=render(p); self.assertTrue(all(len(t)<=2000 for t in texts))
        p["draft"]["body"]="x"*180000
        http=NotionHTTP()
        with self.assertRaises(CloudError): NotionDelivery("secret",PARENT,http).deliver(p)
        self.assertFalse(http.calls)


class IntegrationTests(Offline):
    def test_real_worker_model_and_notion_ports_full_three_stage_flow(self):
        rpc=RPCServer(); PostgresStore(rpc,"trial").submit(document(),CONTRACT)
        http=ModelHTTP([provider(p) for p in outputs()]); notionhttp=NotionHTTP()
        result=run(rpc,model("key",http),NotionDelivery("token",PARENT,notionhttp),"trial",sources=[],clock=lambda:NOW)
        self.assertEqual(result["steps"],["DONE","DONE","EDITORIAL_READY"])
        self.assertEqual(result["delivery"],["DELIVERED"])
        self.assertFalse(result["public_release"])
        self.assertEqual(result["calls"],3)
        # Recreate ALL client adapters; retained server state prevents duplicate paid calls/pages.
        PostgresStore(rpc,"trial").submit(document(),CONTRACT)
        result2=run(rpc,model("key",http),NotionDelivery("token",PARENT,notionhttp),"trial",sources=[],clock=lambda:NOW)
        self.assertEqual(result2["calls"],3)
        self.assertEqual(len(http.calls),3)
        self.assertEqual(sum(c[0]=="POST" for c in notionhttp.calls),1)

    def test_literal_quote_failure_holds_before_analysis_or_notion(self):
        rpc=RPCServer(); PostgresStore(rpc,"trial").submit(document(),CONTRACT)
        p=outputs()[0]; p["claims"][0]["quote"]="not in the text"
        http=ModelHTTP([provider(p)]); nh=NotionHTTP()
        result=run(rpc,model("key",http),NotionDelivery("token",PARENT,nh),"trial",sources=[],clock=lambda:NOW)
        self.assertEqual(result["steps"],["HOLD"]); self.assertEqual(len(http.calls),1); self.assertFalse(nh.calls)

    def test_unknown_model_response_retains_full_charge_and_does_not_retry(self):
        rpc=RPCServer(); PostgresStore(rpc,"trial").submit(document(),CONTRACT)
        http=ModelHTTP([CloudError("HTTP_RESULT_UNCERTAIN")]); nh=NotionHTTP()
        result=run(rpc,model("key",http),NotionDelivery("token",PARENT,nh),"trial",sources=[],clock=lambda:NOW)
        self.assertEqual(result["steps"],["HOLD"])
        self.assertEqual(result["charged_reference_micro_usd"],329573)
        self.assertEqual(next(iter(rpc.usage.values()))["state"],"UNKNOWN")
        self.assertEqual(len(http.calls),1); self.assertFalse(nh.calls)

    def test_unverified_source_never_reaches_model(self):
        rpc=RPCServer(); PostgresStore(rpc,"trial").submit(document(complete=False,rights_verified=False),CONTRACT)
        http=ModelHTTP([]); nh=NotionHTTP()
        result=run(rpc,model("key",http),NotionDelivery("token",PARENT,nh),"trial",sources=[],clock=lambda:NOW)
        self.assertEqual(result["steps"],["HOLD"]); self.assertEqual(result["calls"],0)
        self.assertFalse(http.calls); self.assertFalse(nh.calls)

    def test_expired_or_disabled_scope_prevents_all_external_work(self):
        rpc=RPCServer(); rpc.disabled=True; http=ModelHTTP([]); nh=NotionHTTP()
        with self.assertRaises(CloudError):
            run(rpc,model("key",http),NotionDelivery("token",PARENT,nh),"trial",sources=[],clock=lambda:NOW)
        self.assertFalse(http.calls); self.assertFalse(nh.calls)

    def test_budget_is_cumulative_across_adapter_instances(self):
        rpc=RPCServer(); q=PostgresQuota(rpc,"trial"); estimate=Usage(1048576,6000,1)
        for i in range(3):
            q.reserve(str(i),NOW,estimate); q.dispatch(str(i)); q.unknown(str(i))
        with self.assertRaises(QuotaExceeded): PostgresQuota(rpc,"trial").reserve("four",NOW+timedelta(days=1),estimate)
        self.assertEqual(PostgresQuota(rpc,"trial").verify()["charged_micro_usd"],988719)

    def test_outbox_recovery_records_only_verified_existing_page(self):
        rpc=RPCServer(); p=packet(); key=digest(p)
        rpc.outbox[key]={"key":key,"packet":p,"state":"UNKNOWN","token":None}
        nh=NotionHTTP("timeout_after_commit"); adapter=NotionDelivery("token",PARENT,nh)
        self.assertEqual(adapter.deliver(p).status,"UNKNOWN")
        self.assertEqual(reconcile(rpc,adapter,"trial",key)["delivery"],"DELIVERED")
        self.assertEqual(rpc.outbox[key]["state"],"DELIVERED")
        self.assertEqual(sum(c[0]=="POST" for c in nh.calls),1)
        self.assertFalse(rpc.usage)


class IntakeTests(Offline):
    def prepared(self):
        from collectors.inoreader.sync import normalize
        from collectors.inoreader.intake_contract import prepare_batch, validate_batch
        raw={"id":"synthetic-body","title":"Synthetic","published":1792411200,
             "canonical":[{"href":"https://example.org/synthetic"}],
             "origin":{"title":"Synthetic publisher","streamId":"feed/synthetic"},
             "summary":{"content":"<article><p>The notice announced planned output of 20 units.</p></article>"}}
        value=prepare_batch([normalize(raw,"BOHUN_Test",captured_at=NOW)],batch_id=PARENT,
                            producer_sha="a"*40,pipeline_version="intake-v1",collected_at=NOW)
        manifest=validate_batch(value)
        receipt={"batch_id":PARENT,"manifest_json":value.manifest_json,"manifest_sha256":value.manifest_sha256,
                 "items":[{"ordinal":0,"artifact_id":CHILD,"job_id":CHILD,
                           "payload_sha256":manifest["items"][0]["payload_sha256"]}]}
        return {"receipt":receipt,"payloads":list(value.payloads)},manifest

    def config(self):
        return {"run_scope":"trial","expected_sha":"a"*40,"enabled_actions":["capture"],
                "sources":[{"kind":"inoreader","batch_id":PARENT,"policy_ref":"fixture-inoreader","fetch_new":False}]}

    def test_body_without_profile_container_remains_unknown(self):
        rpc=RPCServer();rpc.intake_value,_=self.prepared()
        rpc.profiles['fixture-inoreader']['end_marker']='NOT_PRESENT_END'
        sources=collect_sources(self.config(),rpc)
        self.assertFalse(sources[0]['admission']['accepted'])
        self.assertEqual(sources[0]['value']['source']['completeness'],'UNKNOWN')
        http=ModelHTTP([]);nh=NotionHTTP()
        result=run(rpc,model('key',http),NotionDelivery('token',PARENT,nh),'trial',sources=sources,clock=lambda:NOW)
        self.assertTrue(result['held']);self.assertEqual(http.calls,[]);self.assertEqual(nh.calls,[])

    def test_profile_admits_new_text_without_per_hash_manual_flags(self):
        rpc=RPCServer();rpc.intake_value,_=self.prepared()
        one=collect_sources(self.config(),rpc)[0]
        self.assertTrue(one['admission']['accepted'])
        self.assertEqual(one['admission']['policy_ref'],'fixture-inoreader')
        self.assertNotIn('eligibility',one['admission'])
        rpc.profiles['fixture-inoreader']['enabled']=False
        with self.assertRaises(ValueError):collect_sources(self.config(),rpc)

    def test_tampered_body_never_becomes_an_admitted_source(self):
        rpc=RPCServer();rpc.intake_value,_=self.prepared()
        rpc.intake_value['payloads'][0]=rpc.intake_value['payloads'][0].replace('20 units','90 units')
        with self.assertRaises(ValueError):collect_sources(self.config(),rpc)
        self.assertFalse(rpc.snapshots)

    def test_retained_intake_full_dossier_through_actual_adapters(self):
        from collectors.intelligence_pipeline.source_adapters import restore_source
        rpc=RPCServer();rpc.intake_value,_=self.prepared();sources=collect_sources(self.config(),rpc)
        doc=restore_source(sources[0]['value']).to_document()
        http=ModelHTTP([provider(p) for p in outputs(doc)]);nh=NotionHTTP()
        result=run(rpc,model('key',http),NotionDelivery('token',PARENT,nh),'trial',sources=sources,clock=lambda:NOW)
        self.assertEqual(result['submitted'],1);self.assertEqual(result['delivery'],['DELIVERED'])
        stored=next(iter(rpc.outbox.values()))['packet']
        self.assertEqual(stored['qa'],{'decision':'HOLD','public_release':False})
        self.assertEqual(stored['context_status'],'NO_MATCH_IN_SCOPE')
        self.assertTrue(stored['dossier']['hypotheses'][0]['refutation'])
        self.assertEqual(len(http.calls),3)


class HTTPTests(Offline):
    def test_response_bound_and_sanitized_transport_failure(self):
        class Response(io.BytesIO): code=200
        client=JsonHTTP("api.notion.com",max_response=10)
        client.opener=unittest.mock.Mock()
        client.opener.open.return_value=Response(b'{"text":"PRIVATE_SENTINEL"}')
        with self.assertRaisesRegex(CloudError,"^HTTP_RESPONSE_TOO_LARGE$"):
            client.request("GET","/v1/pages/id",{"Authorization":"SECRET"})
        client.opener.open.side_effect=OSError("PRIVATE_SENTINEL_WITH_CREDENTIAL")
        with self.assertRaisesRegex(CloudError,"^HTTP_RESULT_UNCERTAIN$"):
            client.request("GET","/v1/pages/id",{"Authorization":"SECRET"})
        self.assertEqual(client.opener.open.call_count,2)

    def test_cross_host_redirect_method_and_request_size_guards(self):
        from collectors.intelligence_pipeline.cloud.http import NoRedirect
        client=JsonHTTP("api.notion.com"); client.opener=unittest.mock.Mock()
        for method,path in (("GET","//evil.test/"),("DELETE","/v1/pages/id"),("GET","/path#fragment")):
            with self.assertRaises(CloudError): client.request(method,path,{})
        with self.assertRaises(CloudError): client.request("POST","/v1/pages",{}, {"body":"x"*600000})
        client.opener.open.assert_not_called()
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,"",{},"https://evil.test/"))


class TransactionTests(Offline):
    def connection(self, *, fail_commit=False, role="bohun_analysis", sqlstate=None):
        class Connection:
            autocommit=False
            def __init__(self): self.queries=[]; self.commits=0; self.rollbacks=0; self.closed=False
            def cursor(self): return self
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,sql,params=None):
                self.queries.append((sql,params))
                if sqlstate and "cloud_rpc_v2" in sql:
                    exc=RuntimeError("PRIVATE SQL ERROR SENTINEL"); exc.sqlstate=sqlstate; raise exc
            def fetchone(self):
                return (role,role) if self.queries[-1][0]=="SELECT current_user, session_user" else ({"ok":True},)
            def commit(self):
                self.commits+=1
                if fail_commit: raise OSError("PRIVATE COMMIT SENTINEL")
            def rollback(self): self.rollbacks+=1
            def close(self): self.closed=True
        return Connection()

    def test_commit_loss_never_replays_rpc_or_leaks_error(self):
        conn=self.connection(fail_commit=True)
        with self.assertRaisesRegex(CloudError,"^DB_RESULT_UNCERTAIN$"):
            PostgresRPC(lambda:conn)("reserve",{"scope":"private"})
        self.assertEqual(sum("cloud_rpc_v2" in q[0] for q in conn.queries),1)
        self.assertEqual(conn.commits,1); self.assertTrue(conn.closed)

    def test_role_check_precedes_rpc(self):
        conn=self.connection(role="postgres")
        with self.assertRaises(CloudError): PostgresRPC(lambda:conn)("claim",{})
        self.assertFalse(any("cloud_rpc_v2" in q[0] for q in conn.queries))

    def test_parameterized_rpc_and_sqlstate_mapping(self):
        conn=self.connection(); PostgresRPC(lambda:conn)("submit",{"text":"' ; DROP TABLE not_a_query"})
        self.assertNotIn("DROP TABLE",conn.queries[-1][0]); self.assertIn("DROP TABLE",conn.queries[-1][1][1])
        for code,error in (("P2701",QuotaExceeded),("P2702",LeaseLost),("P2703",CloudError)):
            with self.assertRaises(error): PostgresRPC(lambda:self.connection(sqlstate=code))("claim",{})


class CLITests(Offline):
    def test_live_flag_missing_refuses_before_any_constructor(self):
        spec=importlib.util.spec_from_file_location("intelligence_cloud_cli",ROOT/"scripts/intelligence-cloud.py")
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(module,"open_rpc",side_effect=AssertionError("NO_DB")), patch("sys.stderr",io.StringIO()):
            self.assertEqual(module.main(["--expected-sha","0"*40,"--config","/private/tmp/never-read-config"]),2)


if __name__ == "__main__": unittest.main()
