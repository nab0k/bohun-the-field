"""Append-only machine pages; durable outbox before POST. Unknown is never a retry."""
from uuid import UUID
import json

from .contracts import digest, json_text, nonempty
from .gemini import http_json

NOTION_VERSION = "2026-03-11"


class DeliveryUnknown(RuntimeError):
    pass


class DeliveryConflict(DeliveryUnknown):
    pass


def page_id(value):
    return str(UUID(value))


def page_content(record, parent):
    packet = record["packet"]; material = record["material"]
    if packet.get("qa") != {"decision":"HOLD", "public_release":False}:
        raise ValueError("PRIVATE_EDITORIAL_PACKET_REQUIRED")
    from .editorial import MaterialVersion
    data = dict(material); data["source_hashes"] = tuple(data["source_hashes"])
    version = MaterialVersion(**data)
    if (version.content_hash != record["content_hash"] or packet["material_id"] != version.material_id
            or packet["draft"]["title"] != version.title or packet["draft"]["body"] != version.body
            or tuple(s["text_hash"] for s in packet["sources"]) != version.source_hashes):
        raise ValueError("NOTION_VERSION_HASH_MISMATCH")
    marker = f"BOHUN:{material['material_id']}:{material['version']}:{record['content_hash']}:{digest(packet)}"
    title = material["title"][:1400] + " [" + marker + "]"
    # Simple paragraphs have a deterministic readback. No external embeds/files.
    paragraphs = [marker, "PROPOSED / QA HOLD / public_release=false", material["body"],
                  "Почему важно: " + packet["draft"]["why_it_matters"],
                  "Альтернативы: " + json_text(packet["draft"]["alternatives"]),
                  "Неизвестное: " + json_text(packet["draft"]["unknowns"]),
                  "Досье: " + json_text(packet.get("dossier")),
                  "Утверждения источника и буквальные цитаты; независимая QA ещё не пройдена: " + json_text(packet["claims"]),
                  "Источники: " + json_text(packet["sources"]),
                  "Контекст и статусы: " + json_text(packet["context_manifest"]),
                  "Охват: " + json_text(packet.get("coverage", {})),
                  "Обработка: " + json_text(packet.get("analysis_run", {})),
                  "Ограничения: " + json_text(packet.get("warnings", []))]
    texts = [part[i:i+2000] for part in paragraphs for i in range(0,len(part),2000)]
    if len(texts) > 80:
        raise ValueError("NOTION_BLOCK_LIMIT")
    children = [{"object":"block", "type":"paragraph", "paragraph":{
        "rich_text":[{"type":"text", "text":{"content":t}}]}} for t in texts]
    body = {"parent":{"type":"page_id", "page_id":page_id(parent)},
            "properties":{"title":{"type":"title", "title":[{"type":"text", "text":{"content":title}}]}},
            "children":children}
    if len(json_text(body).encode()) > 256 * 1024:
        raise ValueError("NOTION_REQUEST_LIMIT")
    return body, texts, title


class NotionDelivery:
    def __init__(self, store, token, parent, *, enabled=False, transport=http_json):
        self.store, self.token, self.parent = store, token, page_id(parent)
        self.enabled, self.transport = enabled, transport

    def _call(self, method, path, body=None):
        if self.enabled is not True or not self.token:
            raise ValueError("NOTION_NOT_ENABLED")
        return self.transport(method, "https://api.notion.com/v1/" + path,
                              {"Authorization":"Bearer " + self.token, "Notion-Version":NOTION_VERSION}, body)

    def verify(self, identifier, texts, title):
        identifier = page_id(identifier)
        meta = self._call("GET", "pages/"+identifier)
        prop = meta.get("properties", {}).get("title", {})
        actual_title = "".join(t.get("plain_text", t.get("text",{}).get("content","")) for t in prop.get("title", []))
        parent = meta.get("parent", {})
        if actual_title != title or parent.get("type") != "page_id" or page_id(parent["page_id"]) != self.parent:
            raise DeliveryConflict("NOTION_PAGE_CHANGED_OR_WRONG_PARENT")
        result = self._call("GET", "blocks/"+identifier+"/children?page_size=100")
        if result.get("has_more") is not False:
            raise DeliveryUnknown("NOTION_READBACK_INCOMPLETE")
        actual = []
        for block in result.get("results", []):
            if block.get("type") != "paragraph" or block.get("has_children") is not False:
                raise DeliveryConflict("NOTION_UNEXPECTED_BLOCK")
            actual.append("".join(t.get("plain_text", t.get("text",{}).get("content",""))
                                  for t in block["paragraph"]["rich_text"]))
        if actual != texts:
            raise DeliveryConflict("NOTION_BODY_CHANGED_OR_INCOMPLETE")

    def deliver(self, record):
        record = json.loads(json_text(record))  # Freeze exactly the packet covered by the claim/hash.
        body, texts, title = page_content(record, self.parent)  # Validate before any I/O.
        if self.enabled is not True or not self.token:
            raise ValueError("NOTION_NOT_ENABLED")
        material = record["material"]; mid, version = material["material_id"], material["version"]
        state = self.store.claim_delivery(mid, version, expected_hash=record["content_hash"],
                                          packet_hash=digest(record["packet"]))
        if state["state"] == "CONFIRMED":
            return {"state":"CONFIRMED", "page_id":state["page_id"]}
        if state["state"] != "SENDING" or state.get("claimed") is not True or not state.get("token"):
            raise DeliveryUnknown("NOTION_UNKNOWN_RECONCILE_WITHOUT_POST")
        token = state["token"]
        identifier = None
        try:
            reply = self._call("POST", "pages", body)
            identifier = page_id(reply["id"])
            self.verify(identifier, texts, title)
            self.store.mark_delivery(mid, version, "CONFIRMED", page_id=identifier,
                                     expected_hash=record["content_hash"], token=token)
            return {"state":"CONFIRMED", "page_id":identifier}
        except DeliveryConflict:
            self.store.mark_delivery(mid, version, "CONFLICT", page_id=identifier,
                                     expected_hash=record["content_hash"], token=token)
            raise
        except Exception:
            self.store.mark_delivery(mid, version, "UNKNOWN", page_id=identifier,
                                     expected_hash=record["content_hash"], token=token)
            raise DeliveryUnknown("NOTION_DELIVERY_UNKNOWN_NO_POST_RETRY") from None

    def reconcile(self, record):
        """Explicit read-only recovery. Not found does not authorize a second POST."""
        record = json.loads(json_text(record))
        _, texts, title = page_content(record, self.parent)
        if self.enabled is not True or not self.token:
            raise ValueError("NOTION_NOT_ENABLED")
        material = record["material"]; mid, version = material["material_id"], material["version"]
        state = self.store.delivery(mid, version)
        if state["state"] not in {"UNKNOWN", "CONFIRMED"}:
            raise DeliveryUnknown("NOTION_RECONCILE_REQUIRES_UNKNOWN")
        identifier = state.get("page_id")
        if identifier is None:
            listing = self._call("GET", "blocks/"+self.parent+"/children?page_size=100")
            if listing.get("has_more") is not False:
                raise DeliveryUnknown("NOTION_PARENT_SCAN_INCOMPLETE")
            matches = [b["id"] for b in listing.get("results", []) if b.get("type") == "child_page"
                       and b["child_page"].get("title") == title]
            if len(matches) != 1:
                raise DeliveryUnknown("NOTION_RECONCILE_NOT_UNIQUE_NO_RETRY")
            identifier = page_id(matches[0])
        try:
            self.verify(identifier, texts, title)
        except DeliveryConflict:
            self.store.mark_delivery(mid, version, "CONFLICT", page_id=identifier, expected_hash=record["content_hash"])
            raise
        if state["state"] == "UNKNOWN":
            self.store.mark_delivery(mid, version, "CONFIRMED", page_id=identifier, expected_hash=record["content_hash"])
        return {"state":"CONFIRMED", "page_id":identifier}
