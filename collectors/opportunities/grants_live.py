"""One explicitly approved, bounded public Grants.gov batch. No database client/writes."""
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import ssl
import time
import urllib.error
import urllib.request

from collectors.source_registry.onboarding.contracts import (ContractError, canonical, digest, parse_json,
                                                             require, safe_path, sha256)
from . import grants_intake as intake

ROOT = Path(__file__).parents[4] / "local-runtime/experiments/EXP-009/grants-live"
APPROVAL = "grants-first-real-20261024-01"
APPROVED_REQUEST_HASH = "83e172ccdc2d06d3cacf799f9a430da16ea16f3cc46a88832462dd05b1dc9bec"
BASE_HEAD = "00519cb710d86975f1b21a8d0d9174d2827acd9e"
ORIGIN = "https://api.grants.gov"
SEARCH_PATH = "/v1/api/search2"
DETAIL_PATH = "/v1/api/fetchOpportunity"
QUERY = dict(intake.APPROVED_QUERY)
PER_RESPONSE = 512 * 1024
TOTAL_BYTES = 2 * 1024 * 1024
TOTAL_SECONDS = 45.0
REQUEST_SECONDS = 10.0


class LiveError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Budget:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.start = clock()
        self.requests = 0
        self.response_bytes = 0

    def remaining(self):
        seconds = TOTAL_SECONDS - (self.clock() - self.start)
        if seconds <= 0:
            raise LiveError("TOTAL_DEADLINE")
        return seconds

    def begin(self):
        if self.requests >= 6:
            raise LiveError("REQUEST_LIMIT")
        if self.response_bytes >= TOTAL_BYTES:
            raise LiveError("TOTAL_BYTE_LIMIT")
        timeout = min(REQUEST_SECONDS, self.remaining())
        self.requests += 1
        return timeout

    def count(self, size):
        if size < 0 or size > TOTAL_BYTES - self.response_bytes:
            raise LiveError("TOTAL_BYTE_LIMIT")
        self.response_bytes += size
        self.remaining()


@contextmanager
def request_deadline(seconds):
    """Main-thread Unix alarm also bounds DNS/connect/header/body stalls."""
    require(seconds > 0, "INVALID_TIMEOUT")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "EXISTING_ALARM")
    previous = signal.getsignal(signal.SIGALRM)
    def expire(signum, frame):
        raise LiveError("REQUEST_DEADLINE")
    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if fp is not None:
            fp.close()
        raise LiveError("REDIRECT_FORBIDDEN")


class PublicTransport:
    """No default/global opener, environment proxies, authorization or response-body logging."""
    def post(self, path, body, budget):
        require(path in {SEARCH_PATH, DETAIL_PATH}, "ENDPOINT_ALLOWLIST")
        timeout = budget.begin()
        request_end = budget.clock() + timeout
        # Explicit empty ProxyHandler avoids urllib's environment proxy discovery.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        request = urllib.request.Request(ORIGIN + path, data=canonical(body), method="POST",
                                        headers={"Content-Type": "application/json", "Accept": "application/json",
                                                 "User-Agent": "BOHUN-Grants-ReadOnly/1.0"})
        data = bytearray()
        try:
            timeout = min(request_end - budget.clock(), budget.remaining())
            if timeout <= 0:
                raise LiveError("REQUEST_DEADLINE")
            with request_deadline(timeout), opener.open(request, timeout=timeout) as response:
                if response.getcode() != 200:
                    raise LiveError("HTTP_STATUS")
                if response.geturl() != ORIGIN + path:
                    raise LiveError("RESPONSE_ORIGIN_CHANGED")
                content_type = response.headers.get("Content-Type", "").lower()
                if "application/json" not in content_type:
                    raise LiveError("CONTENT_TYPE")
                if response.headers.get("Content-Encoding", "identity").lower() not in {"identity", ""}:
                    raise LiveError("CONTENT_ENCODING")
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdigit() or int(length) > PER_RESPONSE):
                    raise LiveError("RESPONSE_BYTE_LIMIT")
                while True:
                    budget.remaining()
                    if budget.clock() >= request_end:
                        raise LiveError("REQUEST_DEADLINE")
                    if length is not None and len(data) == int(length):
                        break
                    available = min(PER_RESPONSE - len(data), TOTAL_BYTES - budget.response_bytes)
                    if available <= 0:
                        raise LiveError("RESPONSE_OR_TOTAL_BYTE_LIMIT")
                    chunk = response.read1(min(8192, available))
                    if not chunk:
                        break
                    budget.count(len(chunk))
                    data.extend(chunk)
                    if len(data) > PER_RESPONSE:
                        raise LiveError("RESPONSE_BYTE_LIMIT")
                if length is not None and len(data) != int(length):
                    raise LiveError("INCOMPLETE_RESPONSE")
                if budget.clock() >= request_end:
                    raise LiveError("REQUEST_DEADLINE")
        except urllib.error.HTTPError as exc:
            # HTTPError may own a response stream. Close without reading/logging its body.
            exc.close()
            raise LiveError("HTTP_STATUS_" + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError):
            raise LiveError("TRANSPORT_FAILURE") from None
        budget.remaining()
        return bytes(data)


def _response(data, path, body, receipts):
    require(isinstance(data, bytes) and len(data) <= PER_RESPONSE, "RESPONSE_BYTE_LIMIT")
    receipt = {"endpoint": ORIGIN + path, "request_body": body, "request_sha256": digest(body),
               "response_sha256": sha256(data), "response_bytes": len(data), "retained_original_bytes": False,
               "redaction": "ALLOWLIST_ONLY_TOKEN_CONTACT_ATTACHMENTS_NOT_RETAINED"}
    receipts.append(receipt)
    try:
        value = parse_json(data, limit=PER_RESPONSE)
    except ContractError:
        raise
    except (ValueError, RecursionError):
        raise LiveError("INVALID_JSON") from None
    require(isinstance(value, dict) and str(value.get("errorcode")) == "0", "API_ERRORCODE")
    result = value.get("data")
    require(isinstance(result, dict), "API_DATA_SHAPE")
    for obj in (value, result):
        for key in ("errorMessages", "errorMsgs"):
            if key in obj:
                require(isinstance(obj[key], list) and not obj[key], "API_ERRORS")
    return result


def collect(transport, *, clock=time.monotonic):
    budget = Budget(clock)
    receipts, rows, quarantine = [], [], []
    status, reason, scope = "FAILED", None, {"query": dict(QUERY), "coverage": "UNVERIFIED"}
    try:
        data = transport.post(SEARCH_PATH, dict(QUERY), budget)
        search = _response(data, SEARCH_PATH, dict(QUERY), receipts)
        hits = search.get("oppHits")
        require(isinstance(hits, list) and len(hits) <= 5, "SEARCH_HIT_SHAPE_OR_LIMIT")
        require(type(search.get("hitCount")) is int and search["hitCount"] >= len(hits), "SEARCH_COUNTS")
        require(type(search.get("startRecord")) is int and search["startRecord"] == 0, "SEARCH_PAGE")
        params = search.get("searchParams")
        require(isinstance(params, dict), "SEARCH_PARAMS")
        for key, expected in QUERY.items():
            require(str(params.get(key)) == str(expected), "SEARCH_SCOPE_MISMATCH")
        scope.update({"hit_count": search["hitCount"], "start_record": search["startRecord"],
                      "returned_occurrences": len(hits), "search_params": dict(QUERY)})
        unique = {}
        for hit in hits:
            require(isinstance(hit, dict), "SEARCH_HIT_SHAPE")
            ident = intake.native_id(hit.get("id"))
            # Compare only semantic search fields; do not retain arbitrary hit extras.
            fingerprint = digest({k: hit.get(k) for k in ("id", "number", "title", "agencyCode", "oppStatus", "openDate", "closeDate")})
            if ident in unique:
                require(unique[ident][1] == fingerprint, "SEARCH_ID_COLLISION")
            else:
                unique[ident] = (hit, fingerprint)
        scope["duplicate_search_occurrences"] = len(hits) - len(unique)
        for ident, (hit, _) in unique.items():
            request = {"opportunityId": int(ident)}
            raw = transport.post(DETAIL_PATH, request, budget)
            detail = _response(raw, DETAIL_PATH, request, receipts)
            # ID/draft errors stop the entire batch, no opportunistic partial admission.
            require(intake.native_id(detail.get("id")) == ident, "SEARCH_DETAIL_ID_MISMATCH")
            try:
                projection = intake.project_record(hit, detail)
                rows.append(intake.candidate(projection, sha256(raw), query=QUERY))
            except ContractError as exc:
                quarantine.append({"native_id": ident, "reason": exc.code})
                raise
            budget.remaining()
        status = "SUCCEEDED" if rows else "EMPTY"
    except (LiveError, ContractError) as exc:
        reason = exc.code
        status = "PARTIAL" if rows else "FAILED"
    # No exception message/body/error object from the server is ever returned.
    return {"schema": "bohun-grants-source-result-v1", "status": status, "reason": reason, "rows": rows,
            "scope": scope, "quarantine": quarantine, "receipts": receipts,
            "budget": {"requests": budget.requests, "response_bytes": budget.response_bytes,
                       "elapsed_seconds": round(clock() - budget.start, 6), "max_requests": 6,
                       "max_bytes": TOTAL_BYTES, "max_seconds": TOTAL_SECONDS},
            "public_release": False, "database_writes": 0}


def _write_new(path, data):
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _reserve(root, output, approval, producer):
    root, output = safe_path(root), safe_path(output)
    require(approval == APPROVAL and output == root / "runs/first-real-20261024-01", "APPROVAL_SCOPE")
    require(not ({"public", "dist", ".git", "repos"} & set(root.parts)), "OUTPUT_SCOPE")
    marker = root / "authorizations" / (APPROVAL + ".json")
    require(not marker.exists() and not output.exists(), "ATTEMPT_ALREADY_RESERVED")
    (root / "runs").mkdir(parents=True, exist_ok=True)
    (root / "authorizations").mkdir(parents=True, exist_ok=True)
    safe_path(marker)
    safe_path(output)
    output.mkdir(mode=0o700)
    authorization = {"approval_ref": approval, "request_sha256": APPROVED_REQUEST_HASH,
                     "state": "CONSUMED_BEFORE_FIRST_HTTP", "producer": producer, "query": dict(QUERY),
                     "output": str(output), "consumed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    _write_new(marker, canonical(authorization) + b"\n")
    return output


def save_result(output, result, *, producer, fetched_at):
    plan = intake.prepare_plan(result["rows"], fetched_at=fetched_at, run_id=APPROVAL,
                               source_status=result["status"])
    payloads = {"request_manifest.json": canonical({"approval_ref": APPROVAL, "request_hash": APPROVED_REQUEST_HASH,
                "query": dict(QUERY), "producer": producer, "fetched_at": fetched_at}) + b"\n",
                "source_projections.jsonl": b"".join(canonical(r["payload"]["candidate"]) + b"\n" for r in result["rows"]),
                "candidates.jsonl": b"".join(canonical(r) + b"\n" for r in result["rows"]),
                "response_receipts.json": canonical({k: v for k, v in result.items() if k != "rows"}) + b"\n",
                "write_plan.json": canonical(plan) + b"\n", "write_plan.sql": intake.render_sql(plan).encode("utf-8")}
    manifest = {"schema": "bohun-grants-run-manifest-v1", "source_status": result["status"],
                "public_release": False, "database_writes": 0, "producer": producer,
                "files": {k: sha256(v) for k, v in payloads.items()}}
    for name, data in payloads.items():
        _write_new(output / name, data)
    # Manifest last. Incomplete directories are retained and must never be reused.
    _write_new(output / "run_manifest.json", canonical(manifest) + b"\n")
    return sha256(canonical(manifest) + b"\n")


def main(argv=None, *, transport=None, root=None, clock=time.monotonic):
    parser = argparse.ArgumentParser(description="Single approved read-only Grants.gov sample; NEVER writes DB.")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--approval-ref")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if not args.fetch:
        print(json.dumps({"status": "PREPARE_ONLY_NO_NETWORK", "database_writes": 0}))
        return 0
    if not args.output or not args.approval_ref:
        print(json.dumps({"status": "STOP", "reason": "EXPLICIT_APPROVAL_AND_OUTPUT_REQUIRED"}))
        return 2
    try:
        files = {p.name: sha256(p.read_bytes()) for p in (Path(__file__), Path(intake.__file__))}
        producer = {"head": BASE_HEAD, "tree_state": "DIRTY_ISOLATED", "runtime_code_hash": digest(files), "files": files}
        output = _reserve(ROOT if root is None else root, Path(args.output), args.approval_ref, producer)
        fetched_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = collect(PublicTransport() if transport is None else transport, clock=clock)
        manifest_hash = save_result(output, result, producer=producer, fetched_at=fetched_at)
        print(json.dumps({"status": result["status"], "reason": result["reason"], "records": len(result["rows"]),
                          "requests": result["budget"]["requests"], "response_bytes": result["budget"]["response_bytes"],
                          "manifest_sha256": manifest_hash, "database_writes": 0}))
        return 0 if result["status"] in {"SUCCEEDED", "EMPTY"} else 2
    except (ContractError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, ContractError) else "LOCAL_IO_OR_CONTRACT_FAILURE"
        print(json.dumps({"status": "STOP", "reason": code, "database_writes": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
