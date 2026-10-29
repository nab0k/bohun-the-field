"""Programme dossiers kept between runs (knowledge.programme / document / dossier_entry, migration v16).

An event of a known programme gets its verified chronology, supply chain and restrictions from here, and
the research step asks only for what is new. Rows are added only after the code verified them on their page.
"""
import json
import re

from ..synthesis.contracts import sha
from .pages import host, norm


def programme_key(name):
    key = re.sub(r"[^a-z0-9-]+", " ", (name or "").lower()).strip()
    key = re.sub(r"\s+", " ", key)[:160].strip()
    return key if len(key) >= 2 and key[0].isalnum() else None


MAX_NAME_KEY = 60
KIND = {"T": "chronology", "S": "supply_chain", "R": "restriction"}
PREFIX = {v: k for k, v in KIND.items()}


def row_hash(kind, row, source_url, archive_id):
    """Same step from the same source is one row, however the model words it."""
    if kind == "chronology":
        basis = [row.get("date"), source_url or "archive:%s" % archive_id]
    elif kind == "supply_chain":
        basis = [norm(row.get("upper")), norm(row.get("lower"))]
    else:
        basis = [norm(row.get("issuer")), norm(row.get("subject")), row.get("starts_on")]
    return sha(kind + "|" + json.dumps(basis, ensure_ascii=False))


def valid_date(text):
    return text if text and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


class MemoryStore:
    """Same interface as DbStore, for tests and dry runs."""

    def __init__(self):
        self.programmes, self.entries, self.documents = {}, {}, {}

    def known_names(self, limit=400):
        return [p["name"] for p in list(self.programmes.values())[:limit]]

    def find(self, name):
        key = programme_key(name)
        for p in self.programmes.values():
            if key and (p["key"] == key or key in p["aliases"]):
                return p
        return None

    def load(self, programme_id):
        return [e for e in self.entries.values() if e["programme_id"] == programme_id]

    def save(self, name, aliases, topic, summary, facts, cards, run_ref, version):
        key = programme_key(name)
        if not key:
            return {"programme": None, "added": 0}
        p = self.find(name) or self.programmes.setdefault(key, {"id": len(self.programmes) + 1, "key": key, "name": name,
                                                                 "aliases": [], "topic": topic})
        p["aliases"] = sorted(set(p["aliases"]) | {k for k in map(programme_key, aliases) if k})
        added = 0
        for row in dossier_rows(facts):
            h = row_hash(row["kind"], row["row"], row["source_url"], row["archive_id"])
            if (p["id"], h) not in self.entries:
                self.entries[(p["id"], h)] = dict(row, programme_id=p["id"], row_hash=h); added += 1
        for c in cards:
            self.documents.setdefault(c["url"], c)
        return {"programme": p["id"], "added": added}


def dossier_rows(facts):
    """Verified new rows of an event, ready for the dossier."""
    out = []
    for f in facts:
        if f["kind"] in KIND and f["status"] == "ok" and f.get("origin") != "dossier" and isinstance(f.get("row"), dict):
            if not (f.get("source_url") or f.get("archive_id")):
                continue
            out.append({"kind": KIND[f["kind"]], "row": f["row"], "source_url": f.get("source_url"),
                        "archive_id": f.get("archive_id"), "anchor": f.get("anchor"), "text": f["text"]})
    return out


class DbStore:
    def __init__(self, cur):
        self.cur = cur

    def known_names(self, limit=400):
        # Programme names are short; long keys are headlines saved by a failed grouping (run 40923499569).
        self.cur.execute("""SELECT name FROM knowledge.programme WHERE length(key) <= %s
                             ORDER BY updated_at DESC LIMIT %s""", (MAX_NAME_KEY, limit))
        return [r[0] for r in self.cur.fetchall()]

    def find(self, name):
        key = programme_key(name)
        if not key:
            return None
        self.cur.execute("""SELECT id, key, name, aliases FROM knowledge.programme
                             WHERE key = %s OR %s = ANY(aliases) LIMIT 1""", (key, key))
        r = self.cur.fetchone()
        return {"id": r[0], "key": r[1], "name": r[2], "aliases": r[3]} if r else None

    def load(self, programme_id):
        self.cur.execute("""SELECT kind, entry_date, date_precision, what, commitment, amount, currency, source_url,
                                   anchor, archive_item_id, row_data
                              FROM knowledge.dossier_entry WHERE programme_id = %s AND verify_status = 'ok'
                          ORDER BY entry_date NULLS LAST, id""", (programme_id,))
        out = []
        for kind, day, prec, what, commit, amount, cur_, url, anchor, arch, data in self.cur.fetchall():
            out.append({"kind": kind, "row": data, "source_url": url, "archive_id": arch, "anchor": anchor,
                        "date": day.isoformat() if day else None})
        return out

    def save(self, name, aliases, topic, summary, facts, cards, run_ref, version):
        key = programme_key(name)
        if not key or len(key) > MAX_NAME_KEY:
            return {"programme": None, "added": 0, "skipped": "no_programme_name"}
        alias_keys = sorted({k for k in map(programme_key, aliases) if k and k != key})
        existing = self.find(name)
        if existing:
            pid = existing["id"]
            self.cur.execute("""UPDATE knowledge.programme
                                   SET aliases = ARRAY(SELECT DISTINCT unnest(aliases || %s::text[])), updated_at = now()
                                 WHERE id = %s""", (alias_keys, pid))
        else:
            self.cur.execute("""INSERT INTO knowledge.programme (key, name, aliases, topic_id, summary)
                                VALUES (%s, %s, %s, (SELECT id FROM knowledge.topic WHERE slug = %s), %s)
                                ON CONFLICT (key) DO UPDATE SET updated_at = now() RETURNING id""",
                             (key, name[:300], alias_keys, topic, (summary or "")[:1000] or None))
            pid = self.cur.fetchone()[0]
        doc_ids = {}
        for c in cards:
            if not (c.get("url") or "").startswith(("http://", "https://")):
                continue
            self.cur.execute("""INSERT INTO knowledge.document (url, host, title, doc_type, issuer, date_text, official,
                                       storage, text_sha256, size_bytes, anchors, fetched_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, CASE WHEN %s THEN now() END)
                                ON CONFLICT (url) DO UPDATE SET
                                  title = COALESCE(knowledge.document.title, EXCLUDED.title),
                                  storage = CASE WHEN EXCLUDED.storage = 'markdown' THEN 'markdown'
                                                 ELSE knowledge.document.storage END,
                                  text_sha256 = COALESCE(EXCLUDED.text_sha256, knowledge.document.text_sha256),
                                  anchors = CASE WHEN jsonb_array_length(EXCLUDED.anchors) > 0 THEN EXCLUDED.anchors
                                                 ELSE knowledge.document.anchors END
                                RETURNING id""",
                             (c["url"], c.get("host") or host(c["url"]), (c.get("title") or "")[:500] or None,
                              c.get("doc_type"), c.get("issuer"), c.get("date"), bool(c.get("official")),
                              c.get("storage", "link_only"), c.get("text_sha256"), c.get("size"),
                              json.dumps(c.get("anchors") or []), c.get("format") == "html"))
            doc_ids[c["url"]] = self.cur.fetchone()[0]
            self.cur.execute("""INSERT INTO knowledge.programme_document VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                             (pid, doc_ids[c["url"]]))
        added = 0
        for r in dossier_rows(facts):
            row = r["row"]
            h = row_hash(r["kind"], row, r["source_url"], r["archive_id"])
            amount = row.get("amount") if isinstance(row.get("amount"), (int, float)) else None
            self.cur.execute("""INSERT INTO knowledge.dossier_entry (programme_id, kind, entry_date, date_precision, what,
                                       commitment, amount, currency, document_id, source_url, anchor, archive_item_id,
                                       verify_status, row_data, row_hash, pilot_version, run_ref)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ok',%s,%s,%s,%s)
                                ON CONFLICT (programme_id, row_hash) DO NOTHING""",
                             (pid, r["kind"], valid_date(row.get("date") or row.get("starts_on")),
                              row.get("precision") if row.get("precision") in ("day", "month", "year") else None,
                              r["text"][:2000], row.get("commitment"), amount, (row.get("currency") or None),
                              doc_ids.get(r["source_url"]), r["source_url"], r["anchor"], r["archive_id"],
                              json.dumps(row, ensure_ascii=False), h, version, run_ref))
            added += self.cur.rowcount
        return {"programme": pid, "added": added}
