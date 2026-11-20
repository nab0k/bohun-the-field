"""Shared collector entry point that names why it holds (20.11, owner "Да, сейчас").

shared_runtime.main() turns every failure into one generic line, HOLD SHARED_COLLECTOR_CONFIG_STATE_OR_API, which hid
a stuck coordinator lease for 36 hours (18-20.11). shared_runtime.py is pinned by the coordinator's code hash and
cannot change without re-pinning, so this wrapper (outside the pins) runs it unchanged and, on failure, prints the
error type and its fixed public code (discovery.policy.Problem texts are fixed codes by contract; any other message is
dropped, it may carry an upstream URL). Exit code and the original HOLD line stay the same.
"""
import json
import re
import sys

CODE = re.compile(r"[A-Z][A-Z0-9_]{2,79}")


def describe(exc):
    out = {"type": type(exc).__name__}
    text = str(exc)
    if CODE.fullmatch(text):
        out["code"] = text
    status = getattr(exc, "code", None)
    if isinstance(status, int):
        out["http"] = status
    return out


def main(argv=None, run=None):
    if run is None:
        from collectors.inoreader import shared_runtime
        run = shared_runtime.main
    try:
        run(argv)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - the same catch-all as shared_runtime, with a named reason
        detail = describe(exc)
        cause = exc.__cause__ or exc.__context__
        if cause is not None:
            detail["cause"] = describe(cause)
        print(json.dumps({"status": "HOLD", "reason": "SHARED_COLLECTOR_CONFIG_STATE_OR_API", "detail": detail}))
        raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
