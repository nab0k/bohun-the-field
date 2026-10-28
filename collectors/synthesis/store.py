"""Atomic offline reference store. NOT a production database adapter."""
from copy import deepcopy
from .contracts import canonical, sha


class MemoryStore:
    def __init__(self):
        self.events = {}
        self.processing = {}

    def save(self, key, result, pipeline_version):
        # Copy-on-write models one event transaction: failures never leave half
        # an event or a processing marker. No DDL or item_processing is installed.
        events, markers = deepcopy(self.events), deepcopy(self.processing)
        old = events.get(key)
        digest = sha(canonical(result))
        if old and old["content_hash"] == digest:
            return "NO_CHANGE"
        if old and (old["active_origin"] == "human" or old["is_hidden"]):
            return "PROTECTED"
        record = {"content_hash": digest, "version": old["version"]+1 if old else 1,
                  "active_origin": "machine", "is_hidden": False, "result": deepcopy(result)}
        events[key] = record
        for item_id in result["document_ids"]:
            markers[(item_id, pipeline_version)] = {"event_key": key,
                "outcome": "event" if result["admission"]["decision"] == "PUBLISH" else result["admission"]["decision"].lower(), "cost_micro_usd": 0}
        self.events, self.processing = events, markers
        return "SAVED"
