"""Append-only PRIVATE child pages. No updates/deletes or publication methods."""

from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID
from ..contracts import digest, json_text, validate_dossier
from .http import CloudError, JsonHTTP

VERSION = "2026-03-11"


def page_id(value):
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise CloudError("NOTION_ID_INVALID") from None


def render(packet):
    if packet.get("qa") != {"decision": "HOLD", "public_release": False} or packet.get("editorial_status") != "PROPOSED":
        raise CloudError("NOTION_PRIVATE_DRAFT_REQUIRED")
    material = packet["material_id"]
    if not isinstance(material, str) or len(material) != 64 or any(c not in "0123456789abcdef" for c in material):
        raise CloudError("NOTION_MATERIAL_ID_INVALID")
    marker = f"BOHUN {material}/{digest(packet)}"
    draft = packet["draft"]
    sections = [marker, "PROPOSED · QA HOLD · public_release=false", draft["title"], draft["body"],
                "Почему это важно\n" + draft["why_it_matters"],
                "Альтернативы\n" + "\n".join(draft["alternatives"]),
                "Неизвестное\n" + "\n".join(draft["unknowns"]),
                "Ограничения\n" + "\n".join(packet.get("warnings", []))]
    for claim in packet["claims"]:
        sections.append(f"[{claim['id']}] {claim['text']}\nОснование: {claim['quote']}\nСтадия: {claim['stage']}; идентичность: {claim['entity_status']}")
    for source in packet["sources"]:
        sections.append(f"Источник: {source['url']}\nОпубликован: {source['published_at']}\nПолучен: {source['fetched_at']}\nSHA256 текста: {source['text_hash']}")
    if packet.get("dossier") is not None:
        validate_dossier(packet["dossier"], packet["claims"], packet.get("context_manifest", []))
        sections += ["Досье\n" + json_text(packet["dossier"]),
                     "Контекст (справочная запись не является подтверждением компании)\n" + json_text(packet.get("context_manifest", [])),
                     "Область проверки\n" + json_text(packet.get("coverage", {})),
                     "Версия анализа\n" + json_text(packet.get("analysis_run", {}))]
    sections.append("Утверждения выше приписаны источнику. Независимая смысловая проверка ещё не пройдена.")
    texts = [text[i:i + 2000] for text in sections for i in range(0, len(text), 2000)]
    if len(texts) > 80 or any(not isinstance(t, str) for t in texts):
        raise CloudError("NOTION_DRAFT_TOO_LARGE")
    blocks = [{"object": "block", "type": "paragraph", "paragraph": {
        "rich_text": [{"type": "text", "text": {"content": text}}]}} for text in texts]
    return marker, texts, blocks


@dataclass(frozen=True)
class Delivery:
    status: str
    page_id: str | None = None


class NotionDelivery:
    def __init__(self, token, parent_id, http=None):
        if not isinstance(token, str) or not token.strip():
            raise CloudError("NOTION_TOKEN_REQUIRED")
        self.parent = page_id(parent_id)
        self._headers = {"Authorization": "Bearer " + token, "Notion-Version": VERSION}
        self.http = http if http is not None else JsonHTTP("api.notion.com")

    def _get(self, path):
        reply = self.http.request("GET", path, self._headers)
        if reply.status != 200:
            raise CloudError("NOTION_READ_UNAVAILABLE")
        return reply.payload

    def _find(self, marker):
        matches, cursor = [], None
        for _ in range(10):
            query = {"page_size": 100}
            if cursor is not None:
                query["start_cursor"] = cursor
            value = self._get(f"/v1/blocks/{self.parent}/children?" + urlencode(query))
            for block in value["results"]:
                if block.get("type") == "child_page" and block["child_page"]["title"] == marker:
                    matches.append(page_id(block["id"]))
            if value.get("has_more") is False:
                return matches
            cursor = value.get("next_cursor")
            if not isinstance(cursor, str) or not cursor:
                break
        raise CloudError("NOTION_DISCOVERY_INCOMPLETE")

    def _verify(self, pid, expected):
        metadata = self._get(f"/v1/pages/{pid}")
        parent = metadata.get("parent", {})
        if parent.get("type") != "page_id" or page_id(parent.get("page_id")) != self.parent or metadata.get("archived") or metadata.get("in_trash"):
            return Delivery("CONFLICT", pid)
        value = self._get(f"/v1/blocks/{pid}/children?page_size=100")
        if value.get("has_more") is not False:
            return Delivery("CONFLICT", pid)
        actual = []
        for block in value["results"]:
            if block.get("type") != "paragraph" or block.get("has_children"):
                return Delivery("CONFLICT", pid)
            parts = block["paragraph"]["rich_text"]
            if any(part.get("type") != "text" for part in parts):
                return Delivery("CONFLICT", pid)
            actual.append("".join(part["text"]["content"] for part in parts))
        return Delivery("DELIVERED" if actual == expected else "CONFLICT", pid)

    def deliver(self, packet, *, reconcile_only=False):
        # Body and payload limits are validated before ANY network call.
        marker, expected, blocks = render(packet)
        title = packet["draft"]["title"][:180] + " · " + marker
        body = {"parent": {"page_id": self.parent}, "properties": {
            "title": {"title": [{"type": "text", "text": {"content": title}}]}}, "children": blocks}
        from ..contracts import json_text
        if len(json_text(body).encode()) > 256 * 1024:
            raise CloudError("NOTION_DRAFT_TOO_LARGE")
        pid = None
        try:
            matches = self._find(title)
            if len(matches) > 1:
                return Delivery("CONFLICT")
            if matches:
                return self._verify(matches[0], expected)
            if reconcile_only:
                # Absence from a read does NOT prove an uncertain POST never committed.
                return Delivery("UNKNOWN")
            reply = self.http.request("POST", "/v1/pages", self._headers, body)
            if reply.status in {200, 201}:
                pid = page_id(reply.payload["id"])
            elif reply.status == 503:
                committed = reply.payload.get("additional_data", {}).get("committed_resource_id")
                if committed:
                    pid = page_id(committed)
            if pid is not None:
                return self._verify(pid, expected)
            return Delivery("UNKNOWN")
        except (CloudError, ValueError, KeyError, TypeError):
            # No repeat POST and no write back into an existing or human-edited page.
            return Delivery("UNKNOWN", pid)
