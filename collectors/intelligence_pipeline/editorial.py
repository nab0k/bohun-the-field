"""Append-only material versions, optimistic editing and a disabled public gate."""

from dataclasses import asdict, dataclass
from threading import RLock

from .contracts import digest, nonempty
from .ports import VersionConflict


@dataclass(frozen=True)
class MaterialVersion:
    material_id: str
    version: int
    title: str
    body: str
    source_hashes: tuple
    origin: str

    def __post_init__(self):
        for value in (self.material_id, self.title, self.body):
            nonempty(value)
        if self.origin not in {"machine", "human"} or type(self.version) is not int or self.version < 1:
            raise ValueError("Explicit origin and positive version required")
        if not isinstance(self.source_hashes, tuple) or not self.source_hashes:
            raise ValueError("Evidence hashes required")
        if any(not isinstance(h, str) or len(h) != 64 or any(c not in "0123456789abcdef" for c in h)
               for h in self.source_hashes):
            raise ValueError("Evidence SHA256 required")

    @property
    def content_hash(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class EditorDecision:
    material_id: str
    version: int
    content_hash: str
    decision: str
    comment: str = ""

    def __post_init__(self):
        if self.decision not in {"APPROVE", "RETURN", "REJECT"}:
            raise ValueError("Invalid editor decision")


@dataclass(frozen=True)
class QAReceipt:
    content_hash: str
    version: int
    decision: str = "HOLD"
    public_release: bool = False
    independent_check_ok: bool = False
    evidence_check_ok: bool = False
    rights_verified: bool = False
    sensitive: bool = True
    checker_id: str = ""


class MemoryEditorial:
    """Offline reference adapter. Cloud CAS and Notion conflict handling are unimplemented."""

    def __init__(self):
        self._lock = RLock()
        self._versions = {}
        self._editable = {}
        self._decisions = []

    def propose(self, material_id, title, body, source_hashes):
        with self._lock:
            versions = self._versions.setdefault(material_id, [])
            version = MaterialVersion(material_id, len(versions) + 1, title, body, tuple(source_hashes), "machine")
            versions.append(version)
            current = self._editable.get(material_id)
            # Machine updates remain separate while a human version is active.
            if current is None or current.origin == "machine":
                self._editable[material_id] = version
            return version

    def current(self, material_id):
        with self._lock:
            return self._editable[material_id]

    def edit(self, material_id, expected_hash, title, body):
        with self._lock:
            base = self._editable[material_id]
            if base.content_hash != expected_hash:
                raise VersionConflict("EDITOR_VERSION_CHANGED")
            versions = self._versions[material_id]
            version = MaterialVersion(material_id, len(versions) + 1, title, body, base.source_hashes, "human")
            versions.append(version)
            self._editable[material_id] = version
            return version

    def decide(self, material_id, expected_hash, decision, comment=""):
        with self._lock:
            current = self._editable[material_id]
            if current.content_hash != expected_hash:
                raise VersionConflict("DECISION_VERSION_CHANGED")
            record = EditorDecision(material_id, current.version, current.content_hash, decision, comment)
            self._decisions.append(record)
            return record


def public_projection(version, editorial, qa, *, enabled=False, trusted_checkers=frozenset()):
    """Pure contract check only. No transport, release authority or enabled runtime.

    This does not replace m0-gate, calibrate auto-release, or grant authority to a
    model response. Explicit trusted QA receipts must come from a future reviewed
    adapter; the CLI never enables this projection.
    """
    if enabled is not True or qa.checker_id not in trusted_checkers:
        return None
    if editorial.material_id != version.material_id or editorial.decision != "APPROVE":
        return None
    if editorial.version != version.version or qa.version != version.version:
        return None
    if editorial.content_hash != version.content_hash or qa.content_hash != version.content_hash:
        return None
    if qa.decision != "APPROVE" or any(flag is not True for flag in (
            qa.public_release, qa.independent_check_ok, qa.evidence_check_ok, qa.rights_verified)):
        return None
    if qa.sensitive is not False:
        return None
    return {"material_id": version.material_id, "version": version.version,
            "content_hash": version.content_hash, "headline": version.title, "body": version.body,
            "origin": version.origin, "qa": {"decision": "APPROVE", "public_release": True}}
