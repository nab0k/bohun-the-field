"""One derived planning projection; no source admission is inferred from prose."""
import re

from .contracts import DIMENSIONS, MAX_BYTES, VERSION, hash_value, require, sha256

HEADERS = ["ID", "Источник / profile", "Данные / зачем BOHUN", "Предложено",
           "Фактический задел / следующий вопрос", "Evidence / владелец"]
PRIORITIES = {"R0", "R0/GATE", "R1", "R1/GATE", "R2", "R2/GATE", "R3", "R3/GAP", "R3/GATE"}


def _projection(rows, master_hash):
    require(len(rows) == 42, "MASTER_ROW_COUNT")
    slots = []
    seen = set()
    for index, cells in enumerate(rows, 1):
        require(len(cells) == 6 and all(isinstance(v, str) and v.strip() for v in cells),
                "MASTER_COLUMNS")
        sid, label, purpose, priority, question, owner = cells
        require(sid == f"S{index:02d}" and sid not in seen, "MASTER_IDS")
        require(priority in PRIORITIES, "MASTER_PRIORITY")
        seen.add(sid)
        slots.append(dict(zip(("slot_id", "source_profile_label", "purpose", "priority",
                               "next_question", "evidence_owner"), cells),
                          readiness={d: {"status": "UNKNOWN", "evidence": None, "as_of": None,
                                         "scope": "planning_only", "reason": "NO_OPERATIONAL_EVIDENCE"}
                                     for d in DIMENSIONS}, operational_profile=None))
    return {"schema_name": "bohun-planning-projection", "schema_version": VERSION,
            "master_sha256": master_hash, "slots": slots, "public_release": False}


def project_markdown(data, expected_hash):
    require(isinstance(data, bytes) and len(data) <= MAX_BYTES, "INPUT_SIZE")
    hash_value(expected_hash)
    require(sha256(data) == expected_hash, "MASTER_CHANGED")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError:
        require(False, "MASTER_ENCODING")
    tables = [i for i, line in enumerate(lines) if line.strip().startswith("| ID | Источник / profile")]
    require(len(tables) == 1, "MASTER_TABLE")
    i = tables[0]
    def cells(line):
        require(line.startswith("|") and line.endswith("|"), "MASTER_COLUMNS")
        return [x.strip() for x in line[1:-1].split("|")]
    require(cells(lines[i]) == HEADERS, "MASTER_COLUMNS")
    require(i + 1 < len(lines) and all(re.fullmatch(r":?-{3,}:?", c)
                                    for c in cells(lines[i + 1])) and len(cells(lines[i + 1])) == 6,
            "MASTER_SEPARATOR")
    rows = []
    for line in lines[i + 2:]:
        if not line.strip().startswith("|"):
            break
        rows.append(cells(line.strip()))
    require(sum(bool(re.match(r"^\| S\d+ \|", line)) for line in lines) == len(rows), "MASTER_EXTRA_ROWS")
    return _projection(rows, expected_hash)


def project_synthetic(value):
    """Fixture projection for tests only; the CLI always reads the Markdown master."""
    require(isinstance(value, dict) and set(value) == {"schema_name", "schema_version", "synthetic", "rows"},
            "INVALID_REGISTRY_FIXTURE")
    require(value["schema_name"] == "bohun-planning-fixture" and value["schema_version"] == VERSION
            and value["synthetic"] is True and isinstance(value["rows"], list), "INVALID_REGISTRY_FIXTURE")
    from .contracts import digest
    return _projection(value["rows"], digest(value))


def validate_projection(value):
    require(isinstance(value, dict) and isinstance(value.get("slots"), list), "PROJECTION_CONTRACT")
    columns = ("slot_id", "source_profile_label", "purpose", "priority", "next_question", "evidence_owner")
    require(all(isinstance(s, dict) and all(k in s for k in columns) for s in value["slots"]),
            "PROJECTION_CONTRACT")
    expected = _projection([[s[k] for k in columns] for s in value["slots"]],
                           hash_value(value.get("master_sha256")))
    require(value == expected, "PROJECTION_CONTRACT")
