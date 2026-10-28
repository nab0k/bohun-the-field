"""Validate source plans locally; real captures need separately reviewed run pins."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

from .batch_delivery import (BatchError, require, parse_json, validate_plan, collect,
                             json_text, publish, checked_output, default_output_root)
from .batch_transport import Budget, Reader


PIN_FILES = ("batch_delivery.py", "batch_transport.py", "batch_cli.py", "sync.py",
             "content_contract.py", "intake_contract.py")


def code_pins():
    base = Path(__file__).absolute().parent
    return {name: hashlib.sha256((base/name).read_bytes()).hexdigest() for name in PIN_FILES}


def read_input(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(256*1024+1)
    return raw, parse_json(raw)


def validate_approval(approval, plan, plan_hash):
    keys = {"schema", "authorized", "authorization_ref", "plan_sha256", "code_pins", "caps",
            "producer_sha", "pipeline_version", "token_env", "subscription_contract_verified",
            "provider_content_retention_approved"}
    require(isinstance(approval, dict) and set(approval) == keys, "LIVE_APPROVAL_SHAPE")
    require(approval["schema"] == "bohun-inoreader-capture-approval-v1"
            and approval["authorized"] is True
            and approval["subscription_contract_verified"] is True
            and approval["provider_content_retention_approved"] is True, "LIVE_NOT_APPROVED")
    require(isinstance(approval["authorization_ref"], str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", approval["authorization_ref"]), "LIVE_REF_INVALID")
    require(approval["plan_sha256"] == plan_hash and approval["code_pins"] == code_pins()
            and approval["caps"] == plan["caps"], "LIVE_PINS_MISMATCH")
    require(approval["token_env"] == "INOREADER_ACCESS_TOKEN"
            and isinstance(approval["producer_sha"], str)
            and re.fullmatch(r"[0-9a-f]{40}", approval["producer_sha"])
            and isinstance(approval["pipeline_version"], str)
            and re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", approval["pipeline_version"]), "LIVE_PRODUCER_INVALID")


def arguments(argv):
    # A small explicit parser avoids locale/terminal environment reads in validate.
    require(isinstance(argv, list) and all(isinstance(x, str) for x in argv), "CLI_ARGUMENTS")
    values = dict(mode="validate", plan=None, output=None, approval_manifest=None, allow_live=False)
    rest = list(argv)
    if rest and not rest[0].startswith("--"):
        values["mode"] = rest.pop(0)
    require(values["mode"] in {"validate", "capture"}, "CLI_ARGUMENTS")
    seen = set()
    while rest:
        name = rest.pop(0)
        require(name in {"--plan", "--output", "--approval-manifest", "--allow-live"}
                and name not in seen, "CLI_ARGUMENTS")
        seen.add(name)
        key = name[2:].replace("-", "_")
        if name == "--allow-live":
            values[key] = True
        else:
            require(rest and not rest[0].startswith("--") and bool(rest[0]), "CLI_ARGUMENTS")
            values[key] = rest.pop(0)
    require(values["plan"] is not None and values["output"] is not None, "CLI_ARGUMENTS")
    return SimpleNamespace(**values)


def main(argv=None, *, output_root=None, token_provider=None, client_factory=Reader):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--help"]:
        print("batch_cli [validate|capture] --plan PATH --output PATH [--allow-live --approval-manifest PATH]")
        return 0
    try:
        args = arguments(argv)
        root = default_output_root() if output_root is None else Path(output_root)
        target, root = checked_output(Path(args.output), root)
        raw, value = read_input(args.plan)
        plan = validate_plan(value)
        plan_hash = hashlib.sha256(raw).hexdigest()
        if args.mode == "validate":
            require(not args.allow_live and args.approval_manifest is None, "VALIDATE_LIVE_FLAGS")
            result = dict(schema="bohun-inoreader-plan-validation-v1", status="VALIDATED_PLAN_ONLY_NO_CAPTURE",
                          mode="VALIDATE_ONLY", plan_sha256=plan_hash, profile_count=len(plan["profiles"]),
                          qa_status="HOLD", public_release=False, code_pins=code_pins())
        else:
            require(args.allow_live and args.approval_manifest is not None, "LIVE_APPROVAL_REQUIRED")
            _, approval = read_input(args.approval_manifest)
            validate_approval(approval, plan, plan_hash)
            root.mkdir(parents=True, exist_ok=True)
            markers = root/"authorizations"
            require(not markers.is_symlink(), "OUTPUT_SYMLINK")
            markers.mkdir(exist_ok=True)
            marker = markers/(approval["authorization_ref"]+".json")
            with marker.open("x", encoding="utf-8") as stream:
                stream.write(json_text(dict(state="CONSUMED_BEFORE_HTTP", plan_sha256=plan_hash,
                                             authorization_ref=approval["authorization_ref"]))+"\n")
            budget = Budget(plan["caps"])
            client = None
            # This single explicit environment lookup is unreachable in validate/import.
            provider = token_provider if token_provider is not None else lambda: os.environ.get(approval["token_env"])
            try:
                client = client_factory(provider, budget)
                snapshot = client.subscriptions()
                result = collect(plan, snapshot, client.stream, captured_at=datetime.now(timezone.utc),
                                 producer_sha=approval["producer_sha"], pipeline_version=approval["pipeline_version"])
            except BatchError as exc:
                result = dict(schema="bohun-inoreader-preview-v1", status="CAPTURE_FAILED", mode="PREVIEW_ONLY_NO_DB",
                              reason=str(exc), outcomes=[dict(profile_id=p["batch_profile_id"],
                              status="NOT_ATTEMPTED_AFTER_STOP") for p in plan["profiles"]],
                              batches={}, qa_status="HOLD", public_release=False)
            except Exception:
                result = dict(schema="bohun-inoreader-preview-v1", status="CAPTURE_FAILED", mode="PREVIEW_ONLY_NO_DB",
                              reason="CAPTURE_FAILED", outcomes=[dict(profile_id=p["batch_profile_id"],
                              status="NOT_ATTEMPTED_AFTER_STOP") for p in plan["profiles"]],
                              batches={}, qa_status="HOLD", public_release=False)
            result.update(plan_sha256=plan_hash, code_pins=approval["code_pins"],
                          producer_sha=approval["producer_sha"], pipeline_version=approval["pipeline_version"],
                          authorization_ref=approval["authorization_ref"], budget=budget.receipt(),
                          provider_receipts=getattr(client, "receipts", []))
        publish(result, target, root=root)
        print(json_text(dict(status=result["status"], profiles=len(plan["profiles"]),
                             prepared=len(result.get("batches", {})), plan_sha256=plan_hash)))
        return 0 if result["status"] in {"VALIDATED_PLAN_ONLY_NO_CAPTURE", "PREVIEW_COMPLETE"} else 2
    except BatchError as exc:
        print(json_text({"error": str(exc)}))
        return 2
    except (OSError, ValueError, TypeError):
        print(json_text({"error": "LOCAL_INPUT_OR_OUTPUT_FAILED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
