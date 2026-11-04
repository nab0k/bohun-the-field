"""Run the frozen CAPACITY comparison through one explicitly free Gemini project."""

import base64
import gzip
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError
import urllib.request

import compare
from collectors.kb_pilot.free import GeminiFree, check_answer
from collectors.kb_pilot.gemini import Gemini, Ledger
from collectors.synthesis.live import GEMINI_URL
from native_import import import_scorer, materialize_source, verify

MODEL = "gemini-3.5-flash-lite"
URL = GEMINI_URL.format(MODEL)
PACKET_SHA = "694ab50dcf7fc1c0c86b1b5b95427aabec1c06c57c7d37ce84e856b87f4b16f5"
CERT_SHA = "b6994361c3ecd5cbde2fb1021d614887d84c3e51a46c588de2cfabd1513770bb"
GAP = 15
CAP = 48
HTTP_LIMIT = 256 * 1024


def load_packet(encoded, root):
    root = root.resolve()
    verify(len(encoded) <= 48 * 1024, "packet too large")
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded, validate=True))) as stream:
        raw = stream.read(160 * 1024 + 1)
    verify(sha256(raw).hexdigest() == PACKET_SHA, "packet digest")
    packet = json.loads(raw)
    manifest = packet["manifest"].encode()
    verify(sha256(manifest).hexdigest() == compare.BLIND_SHA256, "manifest digest")
    paths = {row["path"] for row in json.loads(manifest)["sources"].values()}
    verify(len(paths) == 7 and set(packet["documents"]) == paths, "document set")
    for name, body in packet["documents"].items():
        path = (root / name).resolve()
        verify(path.is_relative_to(root), "document path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode())
    blind = root / "capacity-factscore-ab" / "BLIND_INPUT.json"
    blind.parent.mkdir()
    blind.write_bytes(manifest)
    compare.load_blind(blind)
    return blind


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_post(url, headers, payload):
    verify(url == URL and "tools" not in payload, "unexpected model endpoint or tool")
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    verify(len(raw) <= HTTP_LIMIT, "request too large")
    req = urllib.request.Request(url, data=raw, method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=30) as reply:
        body = reply.read(HTTP_LIMIT + 1)
    verify(len(body) <= HTTP_LIMIT, "response too large")
    return json.loads(body)


class Trial:
    def __init__(self, key, post=http_post):
        verify(bool(key), "missing Gemini free key")
        self.providers = [GeminiFree(key, MODEL)]
        self.builder = Gemini("builder-only", Ledger(0))
        self.post = post
        self.attempts = 0
        self.next_slot = 0
        self.hold = None
        self.events = []
        self.model_version = None
        self.allow_network = False

    def audit(self, event, args):
        if event in {"socket.connect", "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname",
                     "socket.gethostbyaddr", "socket.getnameinfo"}:
            verify(self.allow_network, "network outside model request")
            if event == "socket.getaddrinfo":
                verify(args[0] == "generativelanguage.googleapis.com", "unexpected model host")

    def transport(self, url, headers, payload):
        self.allow_network = True
        try:
            reply = self.post(url, headers, payload)
        finally:
            self.allow_network = False
        model_version = reply.get("modelVersion")
        if model_version:
            if self.model_version and self.model_version != model_version:
                self.hold = "MODEL_VERSION_CHANGED"
                raise RuntimeError(self.hold)
            self.model_version = model_version
        return reply

    def request(self, system, packet, schema, **options):
        return {"gemini": self.builder.request(system, packet, schema, **options), "schema": schema}

    def free_many(self, requests):
        request, = requests
        if self.hold or self.attempts >= CAP:
            return [("error", RuntimeError("run held"))]
        delay = self.next_slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.attempts += 1
        self.next_slot = time.monotonic() + GAP
        provider, = self.providers
        try:
            answer, usage = provider.call(request, self.transport)
            answer = check_answer(answer, request["schema"])
        except HTTPError as exc:
            self.hold = f"HTTP_{exc.code}"
        except RuntimeError as exc:
            if str(exc) not in {"bad_json", "not_object", "incomplete"}:
                self.hold = self.hold or "TRANSPORT_OR_CONTRACT"
            self.events.append(self.hold or "MALFORMED_RESPONSE")
            return [("error", RuntimeError("model result unavailable"))]
        except Exception:
            self.hold = "TRANSPORT_UNKNOWN"
        else:
            self.events.append("OK")
            return [("ok", answer, {"model": provider.name, "usage": usage})]
        self.events.append(self.hold)
        return [("error", RuntimeError("model run held"))]


def encrypt(result, cert, output):
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
    with output.open("xb") as encrypted:
        subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm", "-outform",
                        "DER", str(cert)], input=payload, stdout=encrypted, check=True)


def main(archive, output):
    verify(os.environ.get("CAPACITY_FREE_APPROVED") == "true", "free approval required")
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        cert_bytes = base64.b64decode(os.environ.pop("CAPACITY_RECIPIENT_B64"), validate=True)
        verify(sha256(cert_bytes).hexdigest() == CERT_SHA, "recipient digest")
        cert = root / "recipient.pem"
        cert.write_bytes(cert_bytes)
        subprocess.run(["openssl", "x509", "-in", str(cert), "-checkend", "3600", "-noout"],
                       check=True, stdout=subprocess.DEVNULL)
        trial = Trial(os.environ.pop("GEMINI_FREE_API_KEY"))
        sys.addaudithook(trial.audit)
        result = {}
        try:
            blind = load_packet(os.environ.pop("CAPACITY_PACKET_B64"), root)
            package = materialize_source(archive, root)
            sys.path.insert(0, str(root))
            scorer = import_scorer()
            import factscore.retrieval as retrieval
            for module in (scorer, retrieval):
                verify(Path(module.__file__).resolve().parent == package, "wrong module origin")
            result = compare.run(blind, trial, scorer.FactScorer, retrieval.Retrieval, root / "cache")
        except Exception as exc:
            trial.hold = trial.hold or f"RUNNER_{type(exc).__name__}"
        complete = (not trial.hold and trial.attempts == CAP
                    and len(result.get("assertions", {})) == 24)
        result.update({"status": "COMPLETE" if complete else "HOLD", "attempts": trial.attempts,
                       "hold": trial.hold, "events": trial.events, "model": MODEL,
                       "model_version": trial.model_version, "packet_sha256": PACKET_SHA,
                       "head": os.environ.get("GITHUB_SHA")})
        encrypt(result, cert, output)
        print(result["status"], f"attempts={trial.attempts}")
        return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
