"""Small, strict contracts shared by the offline adapters and auditor."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat

VERSION = "1.0.0"
MAX_BYTES = 1024 * 1024
MAX_ROWS = 500
MAX_ROW_BYTES = 64 * 1024
KINDS = {"OPPORTUNITY", "PROJECT_RESULT", "COMPANY_EVIDENCE", "PUBLISHER_DOCUMENT",
         "SCHOLARLY_METADATA", "POLICY_DOCUMENT", "TECHNICAL_DATASET"}
DIMENSIONS = ("discovery", "documentation", "access", "probe", "adapter", "schema_QA",
              "intake", "downstream", "freshness")


class ContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ContractError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return sha256(canonical(value))


def hash_value(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "INVALID_HASH")
    return value


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value),
            "INVALID_TIMESTAMP")
    try:
        dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError("INVALID_TIMESTAMP") from exc
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse_json(data, *, limit=MAX_BYTES):
    require(isinstance(data, bytes) and len(data) <= limit, "INPUT_SIZE")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ContractError("NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("INVALID_JSON") from exc


def safe_path(value):
    """Require explicit absolute paths, without symlinks in any existing ancestor."""
    p = Path(value)
    require(p.is_absolute() and ".." not in p.parts, "UNSAFE_PATH")
    for part in (p, *p.parents):
        require(not part.is_symlink(), "SYMLINK_PATH")
    return p


def read_bytes(path, *, limit=MAX_BYTES):
    p = safe_path(path)
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            require(stat.S_ISREG(os.fstat(handle.fileno()).st_mode), "NOT_REGULAR_FILE")
            data = handle.read(limit + 1)
        require(len(data) <= limit, "INPUT_SIZE")
        return data
    except OSError as exc:
        raise ContractError("INPUT_IO") from exc


def producer_identity(value):
    require(isinstance(value, dict) and set(value) == {"version", "git_sha", "code_hash", "tree_state"},
            "INVALID_PRODUCER")
    require(value["version"] == VERSION and value["tree_state"] == "DIRTY_ISOLATED", "INVALID_PRODUCER")
    require(isinstance(value["git_sha"], str) and re.fullmatch(r"[a-f0-9]{40}", value["git_sha"]),
            "INVALID_PRODUCER")
    hash_value(value["code_hash"])
    return dict(value)


def presence(row, fields):
    states = {}
    for key, expected in fields.items():
        if key not in row:
            states[key] = "MISSING"
        elif row[key] is None:
            states[key] = "NULL"
        elif type(row[key]) is not expected:
            states[key] = "INVALID"
        elif row[key] in ("", [], {}):
            states[key] = "EMPTY"
        else:
            states[key] = "PRESENT"
    return states
