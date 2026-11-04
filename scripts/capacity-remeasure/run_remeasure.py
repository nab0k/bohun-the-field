"""CAPACITY second measure: free extractor, local rules, second free reader. Writes only encrypted checkpoints."""
import base64, gzip, hashlib, io, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

from collectors.kb_pilot.free import FreeModel, GeminiFree, OpenAICompatible, USER_AGENT
from collectors.kb_pilot.gemini import Gemini, Ledger
from collectors.kb_pilot.prompts import VERIFY_SCHEMA
from collectors.synthesis.live import GEMINI_URL

sys.path.insert(0, str(Path(__file__).parent))
from observation import (CAPACITY_EXTRACT_SYSTEM, CAPACITY_SCHEMA, CAPACITY_VERIFY_SYSTEM, apply_verdicts, check_evidence,
                         normalized, verify_packet)

PACKET_SHA = "d8db5c1f8e018e86c3c1620d750456b37ee1e50d1128e670688d9ecf7295e1ae"
CODE_PINS = {"prompts.py": "ac44fbae4087ec90dceb4c12efccdd3e337f197e0871655198d63963cd2722f6", "free.py": "1d419fb0d4dd693a456ba9e4538951498ea8035bc70d77ae29b468ec95c683f9", "gemini.py": "3967b1d469c186584d54637d9ab07fc43d5683f625f97ef36719b6342967ad1a"}
GEMINI_MODEL = "gemini-3.5-flash-lite"
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
LIMITS = {"extract": {"gap": 6.0, "tokens_per_minute": 200000, "estimate": 4000},
          "verify": {"gap": 12.0, "tokens_per_minute": 6000, "estimate": 2500}}
RETRY_AFTER_CAP = 120.0
RETRY_DEFAULT = 65.0
TIMEOUT = 120
HTTP_LIMIT = 512 * 1024
KEEP_HEADERS = ("retry-after", "x-ratelimit-limit-requests", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-requests",
                "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def load_packet(encoded, expected=None):
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded, validate=True))) as stream:
        raw = stream.read(1024 * 1024)
    if digest(raw) != (expected or PACKET_SHA):
        raise ValueError("packet integrity")
    return json.loads(raw)


def verify_code():
    import collectors.kb_pilot.free as module
    directory = Path(module.__file__).parent
    for name, expected in CODE_PINS.items():
        if digest((directory / name).read_bytes()) != expected:
            raise ValueError("code integrity")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_post(url, headers, payload):
    request = urllib.request.Request(url, data=json_bytes(payload), method="POST",
                                     headers={**headers, "Content-Type": "application/json", "User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=TIMEOUT) as response:
        raw = response.read(HTTP_LIMIT + 1)
        seen = {k: response.headers.get(k) for k in KEEP_HEADERS if response.headers.get(k)}
    if len(raw) > HTTP_LIMIT:
        raise ValueError("response size")
    return json.loads(raw), seen


class Pacer:
    """Waits before a request so one stage stays under its gap and its tokens per rolling minute."""

    def __init__(self, limits, clock=time.monotonic, sleep=time.sleep):
        self.limits, self.clock, self.sleep = limits, clock, sleep
        self.spent, self.last = [], None

    def before(self):
        now = self.clock()
        wait = 0.0 if self.last is None else max(0.0, self.last + self.limits["gap"] - now)
        recent = [(at, n) for at, n in self.spent if at > now - 60]
        total = sum(n for _, n in recent) + self.limits["estimate"]
        for at, n in recent:
            if total <= self.limits["tokens_per_minute"]:
                break
            wait = max(wait, at + 60 - now)
            total -= n
        if wait:
            self.sleep(wait)
        self.last = self.clock()

    def after(self, tokens):
        self.spent.append((self.clock(), int(tokens or 0)))


class Trial:
    def __init__(self, gemini_key, groq_key, doc_count, post=http_post, clock=time.monotonic, sleep=time.sleep):
        if not gemini_key or not groq_key:
            raise ValueError("missing provider credential")
        self.post, self.sleep = post, sleep
        self.cap = doc_count + 3
        self.counts = {"extract": 0, "verify": 0}
        self.unknown = {"extract": 0, "verify": 0}
        self.stopped, self.receipts, self.headers = {}, {}, {}
        self.pacers = {stage: Pacer(LIMITS[stage], clock, sleep) for stage in LIMITS}
        builder = Gemini("request-builder-only", Ledger(0), transport=self.forbidden, getter=self.forbidden)
        self.providers = {"extract": (GeminiFree(gemini_key, GEMINI_MODEL), GEMINI_URL.format(GEMINI_MODEL)),
                          "verify": (OpenAICompatible("groq", GROQ_URL, groq_key, GROQ_MODEL), GROQ_URL)}
        self.routers = {stage: FreeModel(builder, providers=[provider], transport=self.transport_for(stage, endpoint))
                        for stage, (provider, endpoint) in self.providers.items()}
        for router in self.routers.values():
            router._pace = lambda name: None  # this trial paces itself

    @staticmethod
    def forbidden(*args, **kwargs):
        raise RuntimeError("unexpected paid or search path")

    def send(self, stage, url, headers, payload):
        if self.counts[stage] >= self.cap:
            self.stopped[stage] = "request_cap"
            raise RuntimeError("request cap")
        self.pacers[stage].before()
        self.counts[stage] += 1
        reply, seen = self.post(url, headers, payload)
        self.headers[stage] = seen
        usage = reply.get("usage") or {}
        meta = reply.get("usageMetadata") or {}
        self.pacers[stage].after(usage.get("total_tokens") or meta.get("totalTokenCount"))
        return reply

    def transport_for(self, stage, endpoint):
        def transport(url, headers, payload):
            receipt = self.receipts[stage]
            if url != endpoint or "tools" in payload:
                raise RuntimeError("request outside trial")
            receipt["status"] = "request_started"
            try:
                try:
                    reply = self.send(stage, url, headers, payload)
                except urllib.error.HTTPError as exc:
                    if exc.code != 429:
                        raise
                    seen = {k: exc.headers.get(k) for k in KEEP_HEADERS if exc.headers.get(k)}
                    receipt["first_429_headers"] = seen
                    exc.close()
                    try:
                        delay = min(float(seen.get("retry-after", RETRY_DEFAULT)), RETRY_AFTER_CAP)
                    except ValueError:
                        delay = RETRY_DEFAULT
                    self.sleep(delay)
                    reply = self.send(stage, url, headers, payload)
            except urllib.error.HTTPError as exc:
                receipt.update(status="http_error", http_status=exc.code)
                if exc.code in {401, 402, 403, 429}:
                    self.stopped[stage] = "http_%d" % exc.code
                exc.close()
                raise
            except Exception as exc:
                receipt.update(status="transport_or_response_error", error=type(exc).__name__)
                self.unknown[stage] += 1
                if self.unknown[stage] >= 2:
                    self.stopped[stage] = "unknown_transport"
                raise
            receipt.update(status="wire_answer_received", raw_response=reply)
            return reply
        return transport

    def request(self, stage, data, system, schema, max_output):
        if stage in self.stopped:
            return {"status": "provider_stopped"}, None
        provider, _ = self.providers[stage]
        receipt = {"provider": provider.name, "model": provider.model, "status": "request_not_sent"}
        self.receipts[stage] = receipt
        router = self.routers[stage]
        outcome = router.free_many([router.request(system, data, schema, max_output=max_output, step=stage)])[0]
        if outcome[0] != "ok":
            if receipt["status"] == "wire_answer_received":
                receipt["status"] = "provider_answer_rejected"
            return receipt, None
        _, answer, info = outcome
        receipt.update(status="ok", answer=answer, usage=info.get("free_tokens"))
        return receipt, answer

    def run_document(self, record):
        source = {"title": record["title"], "date": record["date"], "text": record["text"]}
        entry = {"source_sha256": digest(record["text"].encode())}
        entry["extract"], extracted = self.request("extract", source, CAPACITY_EXTRACT_SYSTEM, CAPACITY_SCHEMA, 6000)
        if extracted is None:
            return entry
        items = extracted.get("observations")
        if not isinstance(items, list):
            entry["local"] = {"status": "invalid_extracted_shape"}
            return entry
        passed, dropped = [], []
        for item in items:
            if not isinstance(item, dict):
                dropped.append({"reasons": ["NOT_OBJECT"]})
                continue
            obs = normalized(item)
            reasons = check_evidence(obs, record["text"])
            if reasons:
                dropped.append({"observation": obs, "reasons": reasons})
            else:
                passed.append(obs)
        entry["local"] = {"status": "ok", "passed": passed, "dropped": dropped}
        if not passed:
            entry["verify"] = {"status": "no_observations"}
            return entry
        entry["verify"], verdict = self.request("verify", verify_packet(passed), CAPACITY_VERIFY_SYSTEM, VERIFY_SCHEMA, 2000)
        if verdict is None:
            return entry
        kept, rejected, missing = apply_verdicts(passed, verdict)
        entry["verified"] = {"kept": kept, "rejected": rejected, "missing": missing}
        return entry

    def run(self, packet, checkpoint):
        report = {"packet_sha256": PACKET_SHA, "complete": False, "attempts": self.counts, "documents": {}}
        checkpoint(report)
        for doc_id in sorted(packet):
            report["documents"][doc_id] = self.run_document(packet[doc_id])
            report["stopped"], report["headers"] = self.stopped, self.headers
            checkpoint(report)
        report["complete"] = True
        report["outcome"] = "complete" if all(e.get("verify", {}).get("status") in {"ok", "no_observations"}
                                              and not e.get("verified", {}).get("missing")
                                              for e in report["documents"].values()) else "partial"
        checkpoint(report)
        return report


def encrypted_writer(encoded_cert, expected_sha, output):
    cert = base64.b64decode(encoded_cert, validate=True)
    if digest(cert) != expected_sha or b"PRIVATE KEY" in cert:
        raise ValueError("certificate integrity")
    output = Path(output)

    def write(report):
        with tempfile.TemporaryDirectory() as directory:
            cert_path = Path(directory) / "recipient.pem"
            cert_path.write_bytes(cert)
            result = subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-cbc", "-outform", "DER", str(cert_path)],
                                    input=json_bytes(report), capture_output=True, timeout=30, env={"PATH": os.environ["PATH"]})
        if result.returncode or not result.stdout:
            raise RuntimeError("encryption failed")
        temporary = output.with_suffix(".tmp")
        temporary.write_bytes(result.stdout)
        temporary.replace(output)
    return write


def main():
    try:
        if len(sys.argv) != 2 or os.environ.get("CAPACITY_FREE_ACCOUNTS_APPROVED") != "true":
            raise ValueError("trial approval missing")
        verify_code()
        packet = load_packet(os.environ["CAPACITY_PACKET_B64"])
        checkpoint = encrypted_writer(os.environ["CAPACITY_RECIPIENT_B64"], os.environ["CAPACITY_RECIPIENT_SHA256"], sys.argv[1])
        report = Trial(os.environ["GEMINI_FREE_API_KEY"], os.environ["GROQ_API_KEY"], len(packet)).run(packet, checkpoint)
    except Exception as exc:
        print("CAPACITY stopped:", type(exc).__name__, file=sys.stderr)
        return 1
    print("CAPACITY", report["outcome"], report["attempts"], report.get("stopped"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
