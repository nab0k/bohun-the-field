"""Run the pinned kb_pilot baseline and write only encrypted checkpoints."""

import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from collectors.kb_pilot.extract import apply_verdicts, checked, edge_row, verify_packet
from collectors.kb_pilot.free import FreeModel, GeminiFree, OpenAICompatible, USER_AGENT
from collectors.kb_pilot.gemini import Gemini, Ledger
from collectors.kb_pilot.prompts import EXTRACT_SCHEMA, EXTRACT_SYSTEM, VERIFY_SCHEMA, VERIFY_SYSTEM
from collectors.synthesis.live import GEMINI_URL


PACKET_SHA = "c2bfa0460ff87ad3bebe5e7f3540771d352004fb875fb1c5d6ecc758bda3bc7c"
DOC_IDS = tuple("D01 D02 D05 D06 D07 D08 D09 D16 D17 D18 D19 D23 D24 D25 D28".split())
CODE_PINS = {
    "extract.py": "176b7da10659d394f1c69f1eab70331e7489c0038ab971a9a7ee424db65460da",
    "prompts.py": "ac44fbae4087ec90dceb4c12efccdd3e337f197e0871655198d63963cd2722f6",
    "free.py": "1d419fb0d4dd693a456ba9e4538951498ea8035bc70d77ae29b468ec95c683f9",
    "gemini.py": "3967b1d469c186584d54637d9ab07fc43d5683f625f97ef36719b6342967ad1a",
}
GEMINI_MODEL = "gemini-3.5-flash-lite"
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PACKET_LIMIT = 96 * 1024
HTTP_LIMIT = 256 * 1024
STAGES = ("extract", "verify")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def load_packet(encoded):
    if len(encoded) > 48 * 1024:
        raise ValueError("packet size")
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded, validate=True))) as stream:
        raw = stream.read(PACKET_LIMIT + 1)
    if len(raw) > PACKET_LIMIT or digest(raw) != PACKET_SHA:
        raise ValueError("packet integrity")
    packet = json.loads(raw)
    if set(packet) != set(DOC_IDS):
        raise ValueError("document set")
    return packet


def verify_code():
    import collectors.kb_pilot.extract as extract_module

    directory = Path(extract_module.__file__).parent
    for name, expected in CODE_PINS.items():
        if digest((directory / name).read_bytes()) != expected:
            raise ValueError("code integrity")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_post(url, headers, payload):
    body = json_bytes(payload)
    if len(body) > HTTP_LIMIT:
        raise ValueError("request size")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={**headers, "Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=45) as response:
        raw = response.read(HTTP_LIMIT + 1)
    if len(raw) > HTTP_LIMIT:
        raise ValueError("response size")
    return json.loads(raw)


class Trial:
    def __init__(self, gemini_key, groq_key, post=http_post):
        if not gemini_key or not groq_key:
            raise ValueError("missing provider credential")
        self.post = post
        self.counts = dict.fromkeys(STAGES, 0)
        self.stopped = set()
        builder = Gemini("request-builder-only", Ledger(0), transport=self.forbidden, getter=self.forbidden)
        self.providers = {
            "extract": (GeminiFree(gemini_key, GEMINI_MODEL), GEMINI_URL.format(GEMINI_MODEL)),
            "verify": (OpenAICompatible("groq", GROQ_URL, groq_key, GROQ_MODEL), GROQ_URL),
        }
        self.calls = set()
        self.receipts = {}
        self.routers = {
            stage: FreeModel(builder, providers=[provider], transport=self.transport_for(stage, endpoint))
            for stage, (provider, endpoint) in self.providers.items()
        }

    @staticmethod
    def forbidden(*args, **kwargs):
        raise RuntimeError("unexpected paid or search path")

    def transport_for(self, stage, endpoint):
        def transport(url, headers, payload):
            receipt = self.receipts[stage]
            if url != endpoint or "tools" in payload or self.counts[stage] >= len(DOC_IDS):
                raise RuntimeError("request outside trial")
            self.counts[stage] += 1
            receipt["status"] = "request_started"
            try:
                reply = self.post(url, headers, payload)
                json_bytes(reply)
            except urllib.error.HTTPError as exc:
                receipt.update(status="http_error", http_status=exc.code)
                if exc.code in {401, 402, 403, 429}:
                    self.stopped.add(stage)
                exc.close()
                raise
            except Exception:
                receipt["status"] = "transport_or_response_error"
                raise
            receipt.update(status="wire_answer_received", raw_response=reply)
            return reply

        return transport

    def request(self, stage, doc_id, data, system, schema, max_output):
        key = (stage, doc_id)
        if doc_id not in DOC_IDS or key in self.calls:
            raise ValueError("document stage replay")
        self.calls.add(key)
        if stage in self.stopped:
            return {"status": "provider_stopped"}, None

        provider, _ = self.providers[stage]
        receipt = {"provider": provider.name, "model": provider.model, "status": "request_not_sent"}

        self.receipts[stage] = receipt
        router = self.routers[stage]
        request = router.request(system, data, schema, max_output=max_output, step=stage)
        outcome = router.free_many([request])[0]
        if outcome[0] != "ok":
            if receipt["status"] == "wire_answer_received":
                receipt["status"] = "provider_answer_rejected"
            return receipt, None
        _, answer, info = outcome
        receipt.update(status="ok", answer=answer, usage=info.get("free_tokens"))
        return receipt, answer

    def run_document(self, doc_id, record):
        source = record["input"]
        entry = {"source_sha256": record["source_sha256"]}
        entry["extract"], extracted = self.request(
            "extract", doc_id, source, EXTRACT_SYSTEM, EXTRACT_SCHEMA, 4000,
        )
        if extracted is None:
            return entry
        try:
            entities, kept, dropped = checked(extracted, source["text"], source["date"] or None)
            rows = [edge_row(relation, entities, {"origin": doc_id}) for relation in kept]
        except (AttributeError, KeyError, TypeError, ValueError):
            entry["local"] = {"status": "invalid_extracted_shape"}
            return entry
        entry["local"] = {"status": "ok", "entities": entities, "rows": rows, "dropped": dict(dropped)}
        if not rows:
            entry["verify"] = {"status": "no_relations"}
            return entry
        entry["verify"], verdict = self.request(
            "verify", doc_id, verify_packet(rows), VERIFY_SYSTEM, VERIFY_SCHEMA, 2000,
        )
        if verdict is None:
            return entry
        verdicts = verdict.get("verdicts")
        if not isinstance(verdicts, list) or any(
            not isinstance(item, dict) or type(item.get("n")) is not int or type(item.get("ok")) is not bool
            for item in verdicts
        ):
            entry["verify"]["status"] = "invalid_verdict_shape"
            return entry
        verified, rejected, missing = apply_verdicts(rows, verdict)
        entry["verified"] = {"rows": verified, "rejected": rejected, "missing": missing}
        return entry

    def run(self, packet, checkpoint):
        if set(packet) != set(DOC_IDS):
            raise ValueError("document set")
        report = {"packet_sha256": PACKET_SHA, "complete": False, "attempts": self.counts, "documents": {}}
        checkpoint(report)
        for doc_id in DOC_IDS:
            report["documents"][doc_id] = self.run_document(doc_id, packet[doc_id])
            checkpoint(report)
        report["complete"] = True
        report["outcome"] = "complete" if all(
            entry.get("verify", {}).get("status") in {"ok", "no_relations"}
            and not entry.get("verified", {}).get("missing")
            for entry in report["documents"].values()
        ) else "partial"
        checkpoint(report)
        return report


def encrypted_writer(encoded_cert, expected_sha, output):
    if len(encoded_cert) > 12 * 1024:
        raise ValueError("certificate size")
    cert = base64.b64decode(encoded_cert, validate=True)
    if digest(cert) != expected_sha or b"PRIVATE KEY" in cert:
        raise ValueError("certificate integrity")
    output = Path(output)

    def write(report):
        with tempfile.TemporaryDirectory() as directory:
            cert_path = Path(directory) / "recipient.pem"
            cert_path.write_bytes(cert)
            result = subprocess.run(
                ["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm", "-outform", "DER", str(cert_path)],
                input=json_bytes(report), capture_output=True, timeout=30,
                env={"PATH": os.environ["PATH"]},
            )
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
        checkpoint = encrypted_writer(
            os.environ["CAPACITY_RECIPIENT_B64"], os.environ["CAPACITY_RECIPIENT_SHA256"], sys.argv[1],
        )
        trial = Trial(os.environ["GEMINI_FREE_API_KEY"], os.environ["GROQ_API_KEY"])
        report = trial.run(packet, checkpoint)
    except Exception:
        print("CAPACITY stopped. Check encrypted checkpoint if present.", file=sys.stderr)
        return 1
    print("CAPACITY", report["outcome"], "Extraction attempts:", report["attempts"]["extract"],
          "verification attempts:", report["attempts"]["verify"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
