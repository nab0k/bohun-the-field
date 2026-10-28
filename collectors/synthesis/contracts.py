"""Source-bound input and explicitly supplied editorial resources."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    text: str
    url: str
    published_at: str
    outlet: str
    source_class: str = "unknown"
    company_hosts: tuple = ()

    def __post_init__(self):
        if not all(isinstance(x, str) and x.strip() for x in
                   (self.id, self.title, self.text, self.published_at, self.outlet)):
            raise ValueError("DOCUMENT_FIELDS_REQUIRED")
        if len(self.text.encode()) > 512 * 1024:
            raise ValueError("DOCUMENT_BOUND")
        u = urlsplit(self.url)
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            raise ValueError("HTTPS_SOURCE_REQUIRED")
        if self.source_class not in {"P0", "P1", "P2", "unknown"}:
            raise ValueError("SOURCE_CLASS_REQUIRED")


@dataclass(frozen=True)
class Resources:
    topics: dict
    dictionaries: dict
    prompt: str

    @classmethod
    def load(cls, topics, dictionaries, prompt):
        t = json.loads(Path(topics).read_text(encoding="utf-8"))
        d = json.loads(Path(dictionaries).read_text(encoding="utf-8"))
        p = Path(prompt).read_text(encoding="utf-8")
        if t.get("version") not in {"topics-v1", "topics-v2"} or d.get("version") not in {"dictionaries-v1", "dictionaries-v2"}:
            raise ValueError("UNSUPPORTED_EDITORIAL_INPUTS")
        if not t.get("topics") or not d.get("key_facts_by_event_type"):
            raise ValueError("EDITORIAL_INPUTS_INCOMPLETE")
        return cls(t, d, p)

    def pins(self):
        return {"topics": sha(canonical(self.topics)),
                "dictionaries": sha(canonical(self.dictionaries)), "prompt": sha(self.prompt)}
