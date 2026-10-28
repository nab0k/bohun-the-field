"""Per-origin immutable intake previews. No subscriptions, credentials or DB writes."""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl
from uuid import UUID

from . import intake_contract as contract
from .content_contract import CaptureBudget
from .sync import normalize


CAPS = dict(requests=100, response_bytes=2*1024*1024,
            total_bytes=32*1024*1024, seconds=600, concurrency=1,
            retries=0, authenticated_redirects=0)
PLAN_KEYS = {"schema", "status", "purpose", "accepted_design_sha256", "master_sha256",
             "profile_limit", "items_per_origin", "caps", "profiles",
             "live_authorized", "intake_apply_authorized"}
PROFILE_KEYS = {"batch_profile_id", "catalog_id", "name", "url", "folder",
                "master_slot", "batch_id"}
ERROR_CODES = frozenset("""
AUTH_REDIRECT_FORBIDDEN BATCH_ID BYTE_BUDGET CAPABILITY_URL_FORBIDDEN CAPS_LIMIT
CAPS_SHAPE CLI_ARGUMENTS DUPLICATE_JSON_KEY DUPLICATE_PROFILE ENDPOINT_INVALID
FEED_ID_INVALID HARD_DEADLINE_BUSY HARD_DEADLINE_UNAVAILABLE HTTP_BODY HTTP_CLOSE_FAILED
HTTP_LENGTH HTTP_READ_FAILED HTTP_STATUS HTTP_TRUNCATED INPUT_SIZE JSON_INVALID
LIVE_APPROVAL_REQUIRED LIVE_APPROVAL_SHAPE LIVE_NOT_APPROVED LIVE_PINS_MISMATCH
LIVE_PRODUCER_INVALID LIVE_REF_INVALID OUTPUT_EXISTS OUTPUT_PATH OUTPUT_SYMLINK OUTPUT_SIZE
PLAN_HASH PLAN_LIMIT PLAN_SCOPE PLAN_SHAPE PRODUCER_INVALID PROFILE_COUNT PROFILE_FOLDER
PROFILE_ID PROFILE_NAME PROFILE_SHAPE REQUEST_BUDGET RESPONSE_LIMIT SNAPSHOT_CATEGORIES
SNAPSHOT_ID SNAPSHOT_INCOMPLETE SNAPSHOT_SHAPE TIME_BUDGET TOKEN_INVALID
TOKEN_PROVIDER_REQUIRED VALIDATE_LIVE_FLAGS SAVED_PREVIEW_INVALID
""".split())


class BatchError(ValueError):
    """Only fixed error codes, never provider messages or credential values."""

    def __init__(self, code):
        super().__init__(code if isinstance(code, str) and code in ERROR_CODES else "INTERNAL_ERROR")


def require(value, code):
    if not value:
        raise BatchError(code)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_json(raw, maximum=256*1024):
    require(isinstance(raw, bytes) and len(raw) <= maximum, "INPUT_SIZE")
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(BatchError("JSON_INVALID")))
        # Reuse the existing recursive UTF-8/NUL/nonfinite validation.
        contract.canonical(value)
        return value
    except BatchError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise BatchError("JSON_INVALID") from None


def endpoint(value, *, profile=False):
    require(isinstance(value, str) and 0 < len(value) <= 2048
            and not any(ord(c) < 33 for c in value), "ENDPOINT_INVALID")
    try:
        u = urlsplit(value)
        require(u.scheme.lower() == "https" and u.hostname and not u.username
                and not u.password and not u.fragment and u.port in (None, 443), "ENDPOINT_INVALID")
        require(re.fullmatch(r"[A-Za-z0-9.-]+", u.hostname) is not None, "ENDPOINT_INVALID")
        if profile:
            require(not any(k.lower() in {"token", "access_token", "key", "apikey", "api_key"}
                            for k, _ in parse_qsl(u.query)), "CAPABILITY_URL_FORBIDDEN")
        host = u.hostname.lower() + (":443" if u.port is not None else "")
        return urlunsplit(("https", host, u.path, u.query, ""))
    except BatchError:
        raise
    except ValueError:
        raise BatchError("ENDPOINT_INVALID") from None


def validate_plan(value):
    try:
        contract.canonical(value)
    except (ValueError, TypeError, RecursionError):
        raise BatchError("PLAN_SHAPE") from None
    require(isinstance(value, dict) and set(value) == PLAN_KEYS, "PLAN_SHAPE")
    require(value["schema"] == "bohun-inoreader-delivery-plan-v1"
            and value["status"] == "REVIEW_INPUT_NOT_LIVE_APPROVAL"
            and value["live_authorized"] is False
            and value["intake_apply_authorized"] is False, "PLAN_SCOPE")
    require(isinstance(value["purpose"], str) and 0 < len(value["purpose"]) <= 500, "PLAN_SHAPE")
    for name in ("accepted_design_sha256", "master_sha256"):
        require(isinstance(value[name], str) and re.fullmatch(r"[0-9a-f]{64}", value[name]), "PLAN_HASH")
    count = value["profile_limit"]
    require(type(count) is int and 1 <= count <= 12 and type(value["items_per_origin"]) is int
            and value["items_per_origin"] == 1, "PLAN_LIMIT")
    caps = value["caps"]
    require(isinstance(caps, dict) and set(caps) == set(CAPS), "CAPS_SHAPE")
    for key, maximum in CAPS.items():
        require(type(caps[key]) is int and (caps[key] == maximum if key in
                {"concurrency", "retries", "authenticated_redirects"} else 1 <= caps[key] <= maximum), "CAPS_LIMIT")
    require(caps["response_bytes"] <= caps["total_bytes"], "CAPS_LIMIT")
    require(isinstance(value["profiles"], list) and len(value["profiles"]) == count, "PROFILE_COUNT")
    seen = {k: set() for k in ("batch_profile_id", "catalog_id", "url", "batch_id", "name")}
    for p in value["profiles"]:
        require(isinstance(p, dict) and set(p) == PROFILE_KEYS, "PROFILE_SHAPE")
        for key in ("batch_profile_id", "catalog_id", "master_slot"):
            require(isinstance(p[key], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", p[key]), "PROFILE_ID")
        require(isinstance(p["name"], str) and 0 < len(p["name"]) <= 300, "PROFILE_NAME")
        require(isinstance(p["folder"], str) and re.fullmatch(r"BOHUN_[A-Za-z0-9_-]{1,80}", p["folder"]), "PROFILE_FOLDER")
        try:
            require(isinstance(p["batch_id"], str) and str(UUID(p["batch_id"])) == p["batch_id"], "BATCH_ID")
        except (ValueError, AttributeError):
            raise BatchError("BATCH_ID") from None
        url = endpoint(p["url"], profile=True)
        for key in seen:
            identity = url if key == "url" else p[key]
            require(identity not in seen[key], "DUPLICATE_PROFILE")
            seen[key].add(identity)
    return copy.deepcopy(value)


def resolve_profiles(plan, snapshot):
    require(isinstance(snapshot, dict) and isinstance(snapshot.get("subscriptions"), list)
            and len(snapshot["subscriptions"]) <= 10000, "SNAPSHOT_SHAPE")
    require(snapshot.get("complete", True) is True and not any(snapshot.get(k) for k in
            ("continuation", "next", "nextPageToken", "next_page")), "SNAPSHOT_INCOMPLETE")
    seen, by_url = set(), {}
    for sub in snapshot["subscriptions"]:
        require(isinstance(sub, dict) and isinstance(sub.get("id"), str)
                and 0 < len(sub["id"]) <= 4096 and sub["id"] not in seen, "SNAPSHOT_ID")
        seen.add(sub["id"])
        cats = sub.get("categories", [])
        require(isinstance(cats, list) and all(isinstance(c, dict) for c in cats), "SNAPSHOT_CATEGORIES")
        folders = set()
        for c in cats:
            label, cid = c.get("label"), c.get("id")
            require(label is None or isinstance(label, str), "SNAPSHOT_CATEGORIES")
            require(cid is None or isinstance(cid, str), "SNAPSHOT_CATEGORIES")
            if label:
                folders.add(label)
            if cid and "/label/" in cid:
                folders.add(cid.rsplit("/label/", 1)[1])
        # A present native feed ID is authoritative; this is parsing, not ID invention.
        url = sub["id"][5:] if sub["id"].startswith("feed/") and sub["id"][5:13].lower() == "https://" else sub.get("url")
        if not isinstance(url, str):
            continue
        key = endpoint(url)
        by_url.setdefault(key, []).append((sub["id"], folders))
    result = []
    for p in plan["profiles"]:
        matches = by_url.get(endpoint(p["url"]), [])
        row = {"profile_id": p["batch_profile_id"], "status": "MISSING_SUBSCRIPTION"}
        if len(matches) > 1:
            row["status"] = "REVIEW_AMBIGUOUS_ENDPOINT"
        elif matches:
            feed_id, folders = matches[0]
            row.update(status="RESOLVED" if p["folder"] in folders else "REVIEW_FOLDER", feed_id=feed_id)
        result.append(row)
    return result


def collect(plan, snapshot, fetch_stream, *, captured_at, producer_sha, pipeline_version):
    plan = validate_plan(plan)
    # Validate producer/time/version before any injected provider call.
    try:
        contract.prepare_batch([], batch_id=plan["profiles"][0]["batch_id"],
                               producer_sha=producer_sha, pipeline_version=pipeline_version,
                               collected_at=captured_at)
    except (ValueError, TypeError):
        raise BatchError("PRODUCER_INVALID") from None
    outcomes = resolve_profiles(plan, snapshot)
    batches, seen = {}, {}
    stopped = False
    for index, (p, out) in enumerate(zip(plan["profiles"], outcomes)):
        if stopped:
            if out["status"] == "RESOLVED":
                out["status"] = "NOT_ATTEMPTED_AFTER_STOP"
            continue
        if out["status"] != "RESOLVED":
            continue
        try:
            response = fetch_stream(out["feed_id"])
        except BatchError as exc:
            out.update(status="STOPPED", reason=str(exc))
            stopped = True
            continue
        except Exception:
            out.update(status="STOPPED", reason="PROVIDER_FAILED")
            stopped = True
            continue
        if not isinstance(response, dict) or not isinstance(response.get("items"), list) or len(response["items"]) > 1:
            out["status"] = "HOLD_ITEM_SHAPE"
            continue
        out["coverage"] = "RECENT_ONE_NOT_HISTORY"
        if not response["items"]:
            out["status"] = "WAITING_FOR_DELIVERY"
            continue
        raw = response["items"][0]
        if (not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"].strip()
                or not isinstance(raw.get("origin"), dict)
                or raw["origin"].get("streamId") != out["feed_id"]):
            out["status"] = "HOLD_ORIGIN_OR_ID"
            continue
        native = raw["id"]
        if native in seen:
            other = seen[native]
            out["status"] = "HOLD_NATIVE_ID_COLLISION"
            outcomes[other]["status"] = "HOLD_NATIVE_ID_COLLISION"
            batches.pop(outcomes[other]["profile_id"], None)
            continue
        try:
            item = normalize(copy.deepcopy(raw), p["folder"], captured_at=captured_at,
                             content_budget=CaptureBudget())
            batch = contract.prepare_batch([item], batch_id=p["batch_id"], producer_sha=producer_sha,
                                           pipeline_version=pipeline_version, collected_at=captured_at)
        except Exception:
            out["status"] = "HOLD_PAYLOAD_CONTRACT"
            continue
        seen[native] = index
        body = item["raw_data"]["content_capture"]["status"]
        out.update(status="PREPARED" if body == "BODY_PRESENT_COMPLETENESS_UNVERIFIED" else "PREPARED_PARTIAL",
                   external_id=native, content_status=body, manifest_sha256=batch.manifest_sha256)
        batches[out["profile_id"]] = dict(manifest_json=batch.manifest_json,
                                          manifest_sha256=batch.manifest_sha256, payloads=list(batch.payloads))
    return dict(schema="bohun-inoreader-preview-v1", mode="PREVIEW_ONLY_NO_DB",
                status="PREVIEW_COMPLETE" if all(x["status"] == "PREPARED" for x in outcomes) else "PREVIEW_PARTIAL",
                outcomes=outcomes, batches=batches, qa_status="HOLD", public_release=False,
                rights="UNKNOWN", relevance="UNVERIFIED")


def default_output_root():
    return Path(__file__).absolute().parents[4] / "local-runtime/experiments/EXP-009/source-mass/runs"


def checked_output(target, root):
    target, root = Path(target), Path(root)
    require(root.is_absolute() and target.is_absolute() and target.parent == root
            and target.name != "authorizations"
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", target.name), "OUTPUT_PATH")
    for p in (root, *root.parents):
        require(not p.is_symlink(), "OUTPUT_SYMLINK")
    require(not target.exists() and not target.is_symlink(), "OUTPUT_EXISTS")
    return target, root


def publish(bundle, target, *, root=None):
    target, root = checked_output(target, default_output_root() if root is None else root)
    root.mkdir(parents=True, exist_ok=True)
    target.mkdir()  # Exclusive reservation; partial outputs are never overwritten.
    text = json_text(bundle) + "\n"
    require(len(text.encode("utf-8")) <= 64*1024*1024, "OUTPUT_SIZE")
    with (target / "preview.json").open("x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    manifest = dict(schema="bohun-inoreader-preview-manifest-v1", status=bundle["status"],
                    mode=bundle["mode"], files={"preview.json": digest(text)},
                    qa_status="HOLD", public_release=False)
    manifest_text = json_text(manifest) + "\n"
    temporary = target / ".run_manifest.tmp"
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(manifest_text)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, target / "run_manifest.json")  # Atomic, no replacement.
    temporary.unlink()
    return manifest


def read_saved_preview(target, *, root=None):
    """Verify saved bytes/contracts without a provider request or database call."""
    target, root = Path(target), default_output_root() if root is None else Path(root)
    require(target.is_absolute() and root.is_absolute() and target.parent == root
            and target.name not in {".", ".."}, "OUTPUT_PATH")
    for p in (target, root, *root.parents):
        require(not p.is_symlink(), "OUTPUT_SYMLINK")
    try:
        manifest_path, data_path = target/"run_manifest.json", target/"preview.json"
        require(not manifest_path.is_symlink() and not data_path.is_symlink(), "OUTPUT_SYMLINK")
        with manifest_path.open("rb") as stream:
            manifest = parse_json(stream.read(32769), maximum=32768)
        require(isinstance(manifest, dict) and set(manifest) ==
                {"schema", "status", "mode", "files", "qa_status", "public_release"}
                and manifest["schema"] == "bohun-inoreader-preview-manifest-v1"
                and isinstance(manifest["files"], dict) and set(manifest["files"]) == {"preview.json"},
                "SAVED_PREVIEW_INVALID")
        with data_path.open("rb") as stream:
            data = stream.read(64*1024*1024+1)
        require(len(data) <= 64*1024*1024 and hashlib.sha256(data).hexdigest() ==
                manifest["files"]["preview.json"], "SAVED_PREVIEW_INVALID")
        bundle = parse_json(data, maximum=64*1024*1024)
        require(isinstance(bundle, dict) and bundle.get("status") == manifest["status"]
                and bundle.get("mode") == manifest["mode"] and bundle.get("public_release") is False
                and manifest["public_release"] is False and bundle.get("qa_status") == manifest["qa_status"] == "HOLD",
                "SAVED_PREVIEW_INVALID")
        if "batches" in bundle:
            require(isinstance(bundle["batches"], dict) and len(bundle["batches"]) <= 12
                    and isinstance(bundle.get("outcomes"), list), "SAVED_PREVIEW_INVALID")
            outcomes = {x["profile_id"]: x for x in bundle["outcomes"]}
            require(1 <= len(outcomes) == len(bundle["outcomes"]) <= 12, "SAVED_PREVIEW_INVALID")
            prepared = {key for key, x in outcomes.items() if x["status"] in {"PREPARED", "PREPARED_PARTIAL"}}
            require(set(bundle["batches"]) == prepared, "SAVED_PREVIEW_INVALID")
            for profile_id, saved in bundle["batches"].items():
                require(set(saved) == {"manifest_json", "manifest_sha256", "payloads"}
                        and isinstance(saved["payloads"], list), "SAVED_PREVIEW_INVALID")
                batch = contract.PreparedBatch(saved["manifest_json"], saved["manifest_sha256"], tuple(saved["payloads"]))
                details = contract.validate_batch(batch)
                outcome = outcomes[profile_id]
                require(details["item_count"] == 1 and outcome["status"] in {"PREPARED", "PREPARED_PARTIAL"}
                        and outcome["manifest_sha256"] == batch.manifest_sha256
                        and json.loads(batch.payloads[0])["item"]["external_id"] == outcome["external_id"]
                        and json.loads(batch.payloads[0])["item"]["raw_data"]["origin_stream_id"] == outcome["feed_id"],
                        "SAVED_PREVIEW_INVALID")
        return bundle
    except BatchError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise BatchError("SAVED_PREVIEW_INVALID") from None
