"""Read-only status of the shared Inoreader coordinator (Cloudflare state Worker, route /v1/status).

The collector has answered HOLD since 18.11 18:55 UTC with one generic reason. /v1/status changes nothing on the
Worker: it returns whether the lease is held, the revision and generation, and OAuth metadata (status, generation,
expiry, scope, cached access length, last diagnostic stage). This script prints only those fields - never the
endpoint, the token, the deployment name or any credential. Config and token come from the existing Actions secrets
SOURCE_DISCOVERY_CONFIG and SOURCE_DISCOVERY_STATE_TOKEN. No retry, one request.
"""
import json
import os
import re
import sys
import urllib.request

ENDPOINT = re.compile(r"https://bohun-source-discovery-state\.[a-z0-9-]+\.workers\.dev\Z")
SAFE_CREDENTIAL = ("generation", "status", "expires_at", "scope", "cached_access_bytes")
SAFE_DIAGNOSTIC = ("generation", "stage", "http_status", "upstream_may_have_started")


def request_status(document, token, opener=urllib.request.urlopen):
    endpoint = document["state_endpoint"]
    if not ENDPOINT.fullmatch(endpoint):
        raise ValueError("endpoint")
    req = urllib.request.Request(endpoint + "/v1/status", data=b"{}", method="POST", headers={
        # run 44630120368: Cloudflare answered 403 to the default Python agent (the Worker itself never says 403);
        # the coordinator's own transport sends this agent (collectors/source_registry/discovery/radars.py)
        "User-Agent": "BOHUN-Source-Discovery/1.0",
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "X-Deployment": document["runtime"]["deployment_id"], "X-Code-Sha256": document["code_sha256"],
        "X-Policy-Sha256": document["policy_sha256"]})
    with opener(req, timeout=30) as r:
        return r.status, json.loads(r.read(65536).decode("utf-8"))


def safe(status, data):
    """Only non-secret fields."""
    oauth = data.get("oauth") or {}
    cred, diag = oauth.get("credential") or {}, oauth.get("diagnostic") or {}
    rec = oauth.get("recovery") or {}
    return {"http": status, "status": data.get("status"), "leased": data.get("leased"),
            "revision": data.get("revision"), "generation": data.get("generation"),
            "credential": {k: cred.get(k) for k in SAFE_CREDENTIAL if k in cred},
            "diagnostic": {k: diag.get(k) for k in SAFE_DIAGNOSTIC if k in diag},
            "last_recovery_fields": sorted(rec)[:12]}


def main():
    try:
        document = json.loads(os.environ["SOURCE_DISCOVERY_CONFIG"])
        status, data = request_status(document, os.environ["SOURCE_DISCOVERY_STATE_TOKEN"])
        print(json.dumps(safe(status, data), sort_keys=True))
    except urllib.error.HTTPError as exc:
        print(json.dumps({"http": exc.code, "error": "HTTPError"}))
        return 1
    except Exception as exc:  # noqa: BLE001 - the type only, never the message (it may echo a header)
        print(json.dumps({"error": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
