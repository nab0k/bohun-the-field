"""Guards are installed BEFORE importing new code; all HTTP below is fake."""
import contextlib
import copy
import http.client
import importlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
GUARDS = contextlib.ExitStack()
BLOCKED = []


def forbidden(*args, **kwargs):
    BLOCKED.append("external_boundary")
    raise AssertionError("REAL_EXTERNAL_IO_FORBIDDEN")


for owner, names in [(socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname")),
                     (sqlite3, ("connect",)),
                     (subprocess, ("Popen", "run", "check_output", "call", "check_call")),
                     (os, ("system", "popen", "posix_spawn", "posix_spawnp")),
                     (urllib.request, ("urlopen", "getproxies")),
                     (http.client.HTTPConnection, ("connect",)),
                     (http.client.HTTPSConnection, ("connect",))]:
    for name in names:
        if hasattr(owner, name): GUARDS.enter_context(mock.patch.object(owner, name, forbidden))

from collectors.opportunities import grants_live as live, grants_intake as intake
from collectors.source_registry.onboarding.contracts import ContractError, canonical, sha256

SECRET = "DO_NOT_RETAIN_SESSION_OR_CONTACT_123"


def hit(ident=900001):
    return {"id": str(ident), "number": "TEST-001", "title": "Synthetic funding opportunity",
            "agencyCode": "TEST", "agencyName": "Synthetic agency", "oppStatus": "posted",
            "openDate": "10/01/2026", "closeDate": "11/01/2026", "privateExtra": SECRET}


def detail(ident=900001):
    return {"id": ident, "revision": 0, "opportunityNumber": "TEST-001",
            "opportunityTitle": "Synthetic funding opportunity", "owningAgencyCode": "TEST",
            "draftMode": "N", "listed": "L", "publisherUid": SECRET,
            "synopsis": {"agencyName": "Synthetic agency", "agencyContactName": SECRET,
                         "agencyContactEmail": SECRET, "agencyContactPhone": SECRET,
                         "synopsisDesc": "<p>O'Reilly research. user@example.invalid, 202-555-0199.</p>"
                                         "<script>" + SECRET + "</script>",
                         "postingDate": "Oct 1, 2026 12:00:00 AM EDT", "responseDateDesc": "See notice",
                         "awardFloor": "100", "awardCeiling": "1000",
                         "fundingInstruments": [{"id": "G", "description": "Grant", "extra": SECRET}],
                         "applicantTypes": [{"id": "99", "description": "Others"}],
                         "fundingActivityCategories": []}, "errorMessages": [],
            "synopsisAttachmentFolders": [{"privateExtra": SECRET}]}


def response(data):
    return canonical({"errorcode": 0, "token": SECRET, "data": data})


def search(hits=None):
    hits = [hit()] if hits is None else hits
    return {"oppHits": hits, "hitCount": max(50, len(hits)), "startRecord": 0,
            "searchParams": {**live.QUERY, "token": SECRET}, "errorMsgs": []}


class FakeTransport:
    def __init__(self, hits=None, details=None, search_data=None):
        self.hits = [hit()] if hits is None else hits
        self.details = details or {}
        self.search_data = search_data
        self.calls = []

    def post(self, path, body, budget):
        budget.begin()
        self.calls.append((path, body))
        if path == live.SEARCH_PATH:
            data = response(search(self.hits) if self.search_data is None else self.search_data)
        else:
            value = self.details.get(body["opportunityId"], detail(body["opportunityId"]))
            if isinstance(value, Exception): raise value
            data = value if isinstance(value, bytes) else response(value)
        budget.count(len(data))
        return data


@contextlib.contextmanager
def temporary():
    with tempfile.TemporaryDirectory(dir="/private/tmp") as d: yield Path(d).resolve()


class FakeResponse:
    def __init__(self, data, *, path=live.SEARCH_PATH, status=200, headers=None, tick=None):
        self.data, self.offset, self.path, self.status, self.tick = data, 0, path, status, tick
        self.headers = {"Content-Type": "application/json", "Content-Length": str(len(data))} if headers is None else headers

    def getcode(self): return self.status
    def geturl(self): return live.ORIGIN + self.path
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read1(self, size):
        if self.tick: self.tick()
        result = self.data[self.offset:self.offset+size]; self.offset += len(result)
        return result


class LiveTests(unittest.TestCase):
    def test_real_flow_shape_with_five_fake_opportunities(self):
        transport = FakeTransport([hit(900001+i) for i in range(5)])
        result = live.collect(transport)
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(len(result["rows"]), 5)
        self.assertEqual(result["budget"]["requests"], 6)
        self.assertEqual(transport.calls[0], (live.SEARCH_PATH, live.QUERY))
        self.assertEqual([b["opportunityId"] for _, b in transport.calls[1:]], list(range(900001, 900006)))
        text = canonical(result).decode()
        for secret in (SECRET, "user@example.invalid", "202-555-0199"):
            self.assertNotIn(secret, text)
        self.assertIn("[REDACTED_EMAIL]", text)
        self.assertEqual(result["scope"]["hit_count"], 50)
        self.assertEqual(result["scope"]["coverage"], "UNVERIFIED")

    def test_empty_is_one_request_without_substitution(self):
        t = FakeTransport([]); result = live.collect(t)
        self.assertEqual(result["status"], "EMPTY")
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["budget"]["requests"], 1)

    def test_duplicate_search_hit_does_not_fetch_twice(self):
        result = live.collect(FakeTransport([hit(), hit()]))
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["scope"]["duplicate_search_occurrences"], 1)
        self.assertEqual(result["budget"]["requests"], 2)

    def test_search_conflict_schema_and_scope_mismatch_stop_before_details(self):
        for mutation in ("collision", "count", "page", "query", "too_many", "bool_id"):
            with self.subTest(mutation=mutation):
                s = search()
                if mutation == "collision":
                    h = hit(); h["title"] = "Different"; s["oppHits"].append(h)
                if mutation == "count": s["hitCount"] = "50"
                if mutation == "page": s["startRecord"] = 5
                if mutation == "query": s["searchParams"]["keyword"] = "other"
                if mutation == "too_many": s["oppHits"] = [hit(i) for i in range(900001, 900007)]
                if mutation == "bool_id": s["oppHits"][0]["id"] = True
                result = live.collect(FakeTransport(search_data=s))
                self.assertEqual(result["status"], "FAILED")
                self.assertEqual(result["budget"]["requests"], 1)

    def test_detail_id_draft_and_invalid_fields_never_admitted(self):
        for mutation in ("id", "draft", "listed", "title", "description_type", "errors"):
            with self.subTest(mutation=mutation):
                d = detail()
                if mutation == "id": d["id"] = 900002
                if mutation == "draft": d["draftMode"] = "Y"
                if mutation == "listed": d["listed"] = "U"
                if mutation == "title": del d["opportunityTitle"]
                if mutation == "description_type": d["synopsis"]["synopsisDesc"] = {"x": SECRET}
                if mutation == "errors": d["errorMessages"] = [SECRET]
                result = live.collect(FakeTransport(details={900001: d}))
                self.assertEqual(result["status"], "FAILED")
                self.assertFalse(result["rows"])
                self.assertNotIn(SECRET, canonical(result).decode())

    def test_untrusted_wrong_container_and_status_are_safe_stops(self):
        d = detail(); d["synopsis"] = ["wrong type"]
        result = live.collect(FakeTransport(details={900001: d}))
        self.assertEqual(result["reason"], "DETAIL_CONTAINER")
        h = hit(); h["oppStatus"] = {"malicious": "object"}
        result = live.collect(FakeTransport([h]))
        self.assertEqual(result["reason"], "NONPUBLIC_STATUS")

    def test_api_failure_and_partial_prefix_have_no_retry(self):
        t = FakeTransport([hit(), hit(900002)], {900002: live.LiveError("TRANSPORT_FAILURE")})
        result = live.collect(t)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["budget"]["requests"], 3)
        for raw in (b'{"errorcode":1,"msg":"secret"}', b'{"a":1,"a":2}', b'no-json',
                    b'{"errorcode":0,"data":' + b'9'*5000 + b'}'):
            result = live.collect(FakeTransport(details={900001: raw}))
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["budget"]["requests"], 2)

    def test_default_cli_performs_no_io(self):
        with mock.patch.object(live, "_reserve", side_effect=AssertionError("RESERVE")), \
             mock.patch.object(live.PublicTransport, "post", side_effect=AssertionError("NETWORK")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(live.main([]), 0)

    def test_real_cli_interface_fake_transport_marker_and_no_overwrite(self):
        with temporary() as root:
            output = root / "runs/first-real-20261024-01"; t = FakeTransport()
            args = ["--fetch", "--approval-ref", live.APPROVAL, "--output", str(output)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(live.main(args, transport=t, root=root), 0)
                self.assertEqual(live.main(args, transport=t, root=root), 2)
            self.assertEqual(len(t.calls), 2)
            manifest = json.loads((output/"run_manifest.json").read_text())
            for name, h in manifest["files"].items(): self.assertEqual(sha256((output/name).read_bytes()), h)
            self.assertTrue((root/"authorizations"/(live.APPROVAL+".json")).is_file())
            self.assertNotIn(SECRET, "".join(p.read_text() for p in output.iterdir()))
            self.assertIn("BLOCKED_CONSUMER_CHECK", (output/"write_plan.json").read_text())

    def test_failed_first_attempt_still_consumes_marker(self):
        with temporary() as root:
            out = root / "runs/first-real-20261024-01"
            t = FakeTransport(details={900001: live.LiveError("TRANSPORT_FAILURE")})
            args = ["--fetch", "--approval-ref", live.APPROVAL, "--output", str(out)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(live.main(args, transport=t, root=root), 2)
                self.assertEqual(live.main(args, transport=FakeTransport(), root=root), 2)
            self.assertEqual(json.loads((out/"run_manifest.json").read_text())["source_status"], "FAILED")

    def test_path_approval_symlink_and_existing_output_guards(self):
        with temporary() as root:
            for approval,out in (("wrong",root/"runs/first-real-20261024-01"),
                                  (live.APPROVAL,root/"elsewhere"),
                                  (live.APPROVAL,root/".."/root.name/"runs/first-real-20261024-01")):
                with self.assertRaises(ContractError): live._reserve(root,out,approval,{})
            real = root/"real"; real.mkdir(); link = root/"alias"; link.symlink_to(real,target_is_directory=True)
            with self.assertRaises(ContractError): live._reserve(link,link/"runs/first-real-20261024-01",live.APPROVAL,{})

    def test_transport_uses_verified_tls_no_proxy_discovery_and_fixed_endpoint(self):
        data = b'{"errorcode":0,"data":{}}'; response_ = FakeResponse(data)
        opener = mock.Mock(); opener.open.return_value = response_
        with mock.patch.object(live.urllib.request, "build_opener", return_value=opener) as build:
            got = live.PublicTransport().post(live.SEARCH_PATH,live.QUERY,live.Budget())
            self.assertEqual(got,data)
            proxy,redirect,https = build.call_args.args
            self.assertEqual(proxy.proxies,{})
            self.assertIsInstance(redirect,live.NoRedirect)
            self.assertTrue(https._context.check_hostname)
            self.assertEqual(https._context.verify_mode,live.ssl.CERT_REQUIRED)
            req = opener.open.call_args.args[0]
            self.assertEqual(req.get_method(),"POST")
            self.assertFalse(req.has_header("Authorization"))
        with self.assertRaises(ContractError): live.PublicTransport().post("https://evil.invalid",{},live.Budget())
        with self.assertRaises(live.LiveError): live.NoRedirect().redirect_request(None,None,302,"",{},"https://evil.invalid")

    def test_transport_status_content_and_size_errors(self):
        cases=[FakeResponse(b'x',status=403),FakeResponse(b'x',headers={"Content-Type":"text/html"}),
               FakeResponse(b'x',headers={"Content-Type":"application/json","Content-Encoding":"gzip"}),
               FakeResponse(b'x',headers={"Content-Type":"application/json","Content-Length":str(live.PER_RESPONSE+1)}),
               FakeResponse(b'x',headers={"Content-Type":"application/json","Content-Length":"5"})]
        for resp in cases:
            with self.subTest(headers=resp.headers), mock.patch.object(live.urllib.request,"build_opener") as build:
                build.return_value.open.return_value=resp
                with self.assertRaises(live.LiveError): live.PublicTransport().post(live.SEARCH_PATH,live.QUERY,live.Budget())

    def test_dripping_body_deadline_and_byte_budget_never_overread(self):
        now=[0.0]; budget=live.Budget(lambda:now[0])
        def tick(): now[0]+=4
        resp=FakeResponse(b'x'*50000,headers={"Content-Type":"application/json"},tick=tick)
        with mock.patch.object(live.urllib.request,"build_opener") as b:
            b.return_value.open.return_value=resp
            with self.assertRaisesRegex(live.LiveError,"REQUEST_DEADLINE"):
                live.PublicTransport().post(live.SEARCH_PATH,live.QUERY,budget)
        budget=live.Budget();budget.response_bytes=live.TOTAL_BYTES-10
        resp=FakeResponse(b'x'*20,headers={"Content-Type":"application/json"})
        with mock.patch.object(live.urllib.request,"build_opener") as b:
            b.return_value.open.return_value=resp
            with self.assertRaises(live.LiveError):live.PublicTransport().post(live.SEARCH_PATH,live.QUERY,budget)
        self.assertEqual(resp.offset,10);self.assertEqual(budget.response_bytes,live.TOTAL_BYTES)

    def test_budget_and_alarm_restore(self):
        now=[0.0];b=live.Budget(lambda:now[0]);now[0]=46
        with self.assertRaises(live.LiveError):b.begin()
        b=live.Budget();b.requests=6
        with self.assertRaises(live.LiveError):b.begin()
        previous=signal.getsignal(signal.SIGALRM)
        with self.assertRaisesRegex(live.LiveError,"REQUEST_DEADLINE"):
            with live.request_deadline(1):signal.getsignal(signal.SIGALRM)(signal.SIGALRM,None)
        self.assertEqual(signal.getsignal(signal.SIGALRM),previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))
