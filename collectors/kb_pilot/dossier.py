"""Programme dossiers kept between runs: the market model (knowledge.node / edge / node_document, migration v18;
v16 programme / dossier_entry stay for comparison and are no longer written).

An event of a known programme gets its verified chronology, supply chain and restrictions from here, and
the research step asks only for what is new. Rows are added only after the code verified them on their page.
"""
import hashlib
import json
import re

from ..synthesis.contracts import sha
from .pages import host, norm


def programme_key(name):
    key = re.sub(r"[^a-z0-9-]+", " ", (name or "").lower()).strip()
    key = re.sub(r"\s+", " ", key)[:160].strip()
    return key if len(key) >= 2 and key[0].isalnum() else None


def node_key(name):
    """programme_key, or a stable hash key for names without Latin letters (same as the v18 transfer SQL)."""
    name = (name or "").strip()
    return programme_key(name) or "x-" + hashlib.md5(name.encode()).hexdigest()[:10]


# Names that are a role, not a thing: never a node (run 41041653349 kept 166 rows under "other").
GENERIC = {"other", "others", "unknown", "none", "n a", "na", "programme", "program", "contract", "company",
           "companies", "government", "the government", "ministry of defence", "ministry of defense", "mod", "dod",
           "army", "navy", "air force", "military", "armed forces", "industry", "partners", "customer", "customers",
           "the company", "it", "they", "we", "supplier", "suppliers", "manufacturer", "country", "countries"}


def is_generic(name):
    key = programme_key(name)
    return not key or key in GENERIC


MAX_NAME_KEY = 60
REL = {"chronology": "milestone", "supply_chain": "supplies", "restriction": "restricts"}
KIND_OF_REL = {v: k for k, v in REL.items()}
LOWER_KIND = {"material": "material", "raw_material": "material", "technology": "technology"}
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


def edge_ends(kind, row):
    """(src, dst) node specs (kind, name) of a dossier row; None means the programme itself."""
    if kind == "supply_chain":
        lower, upper = (row.get("lower") or "").strip(), (row.get("upper") or "").strip()
        return ((LOWER_KIND.get(row.get("level"), "company"), lower) if lower else None,
                ("company", upper) if upper else None)
    if kind == "restriction":
        name = (row.get("subject") or "").strip() or ("%s %s" % (row.get("issuer") or "", row.get("kind") or "")).strip()
        return (("regulation", name) if name else None), None
    return None, None


def valid_date(text):
    return text if text and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


CHRONO_RELS = {"milestone", "orders", "funds", "price"}


def edge_as_row(rel, day, precision, amount, currency, commitment, what, src, dst, unit, polarity):
    """A market-model edge from per-item extraction as a dossier row (kind, row) for the synthesis facts."""
    if rel in CHRONO_RELS:
        if not day:
            return None  # an undated step cannot sit in a chronology
        text = what if rel == "milestone" else "%s %s %s: %s" % (src, rel, dst or "", what)
        return "chronology", {"date": day, "precision": precision or "day", "what": " ".join(text.split()),
                              "commitment": ("cancelled/delayed: " if polarity == "negative" else "") + (commitment or rel),
                              "amount": float(amount) if amount is not None else None, "currency": currency}
    if rel == "supplies":
        return "supply_chain", {"upper": dst or "", "lower": src, "level": unit or "component", "note": what}
    if rel == "depends_on":
        return "supply_chain", {"upper": src, "lower": dst or "", "level": unit or "component", "note": what}
    if rel == "restricts":
        return "restriction", {"issuer": src, "kind": "restriction", "subject": dst or "", "status": "in_force",
                               "starts_on": day, "ends_on": None}
    return None


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

    def find_node(self, names):
        return None

    def load_node(self, node_id):
        return []

    def save(self, name, aliases, topic, summary, facts, cards, run_ref, version):
        key = programme_key(name)
        if not key or is_generic(name):
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
        # Programme names are short; headlines saved by a failed grouping (run 40923499569) are 'suspect'.
        self.cur.execute("""SELECT name FROM knowledge.node
                             WHERE kind = 'programme' AND status <> 'suspect' AND length(key) <= %s
                             ORDER BY updated_at DESC LIMIT %s""", (MAX_NAME_KEY, limit))
        return [r[0] for r in self.cur.fetchall()]

    def find(self, name):
        key = programme_key(name)
        if not key:
            return None
        self.cur.execute("""SELECT id, key, name, aliases FROM knowledge.node
                             WHERE kind = 'programme' AND (key = %s OR %s = ANY(aliases)) LIMIT 1""", (key, key))
        r = self.cur.fetchone()
        return {"id": r[0], "key": r[1], "name": r[2], "aliases": r[3]} if r else None

    def load(self, programme_id):
        self.cur.execute("""SELECT rel, occurred_on, source_url, anchor, item_id, row_data
                              FROM knowledge.edge
                             WHERE context_node_id = %s AND status IN ('verified', 'confirmed') AND superseded_by IS NULL
                               AND rel IN ('milestone', 'supplies', 'restricts')
                          ORDER BY occurred_on NULLS LAST, id""", (programme_id,))
        return [{"kind": KIND_OF_REL[rel], "row": data, "source_url": url, "archive_id": arch, "anchor": anchor,
                 "date": day.isoformat() if day else None}
                for rel, day, url, anchor, arch, data in self.cur.fetchall()]

    def find_node(self, names):
        """The programme or product node the event is about: matched by key or alias, most verified edges first."""
        keys = sorted({k for k in map(programme_key, names) if k and not is_generic(k)})
        if not keys:
            return None
        self.cur.execute("""SELECT n.id, n.key, n.name, n.aliases,
                                   (SELECT count(*) FROM knowledge.edge e
                                     WHERE n.id IN (e.src_node_id, e.dst_node_id, e.context_node_id)
                                       AND e.status IN ('verified', 'confirmed') AND e.superseded_by IS NULL) AS edges
                              FROM knowledge.node n
                             WHERE n.kind IN ('programme', 'product') AND n.status <> 'suspect'
                               AND (n.key = ANY(%s) OR n.aliases && %s::text[])
                          ORDER BY edges DESC, n.id LIMIT 1""", (keys, keys))
        r = self.cur.fetchone()
        return {"id": r[0], "key": r[1], "name": r[2], "aliases": r[3], "edges": r[4]} if r and r[4] else None

    def load_node(self, node_id):
        """Every verified edge touching the node, as dossier rows: v16/pilot rows keep their row, extracted ones are
        converted (edge_as_row)."""
        self.cur.execute("""SELECT e.rel, e.occurred_on, e.date_precision, e.amount, e.currency, e.commitment, e.what,
                                   s.name, d.name, e.unit, e.polarity, e.source_url, e.anchor, e.item_id, e.row_data,
                                   e.extractor
                              FROM knowledge.edge e
                              JOIN knowledge.node s ON s.id = e.src_node_id
                         LEFT JOIN knowledge.node d ON d.id = e.dst_node_id
                             WHERE %s IN (e.src_node_id, e.dst_node_id, e.context_node_id)
                               AND e.status IN ('verified', 'confirmed') AND e.superseded_by IS NULL
                          ORDER BY e.occurred_on NULLS LAST, e.id LIMIT 300""", (node_id,))
        out = []
        for rel, day, prec, amount, cur_, commit, what, src, dst, unit, pol, url, anchor, item, data, extractor in \
                self.cur.fetchall():
            if extractor.startswith(("v16:", "kb-pilot")) and rel in KIND_OF_REL:
                kind, row = KIND_OF_REL[rel], data
            else:
                conv = edge_as_row(rel, day.isoformat() if day else None, prec, amount, cur_, commit, what, src, dst,
                                   unit, pol)
                if not conv:
                    continue
                kind, row = conv
            out.append({"kind": kind, "row": row, "source_url": url, "archive_id": item, "anchor": anchor,
                        "date": day.isoformat() if day else None})
        return out

    def node(self, kind, name):
        self.cur.execute("""INSERT INTO knowledge.node (kind, key, name) VALUES (%s, %s, %s)
                            ON CONFLICT (kind, key) DO UPDATE SET updated_at = now() RETURNING id""",
                         (kind, node_key(name), name[:300]))
        return self.cur.fetchone()[0]

    def save(self, name, aliases, topic, summary, facts, cards, run_ref, version):
        key = programme_key(name)
        if not key or len(key) > MAX_NAME_KEY or is_generic(name):
            return {"programme": None, "added": 0, "skipped": "no_programme_name"}
        alias_keys = sorted({k for k in map(programme_key, aliases) if k and k != key})
        existing = self.find(name)
        if existing:
            pid = existing["id"]
            self.cur.execute("""UPDATE knowledge.node
                                   SET aliases = ARRAY(SELECT DISTINCT unnest(aliases || %s::text[])), updated_at = now()
                                 WHERE id = %s""", (alias_keys, pid))
        else:
            self.cur.execute("""INSERT INTO knowledge.node (kind, key, name, aliases, topic_id, summary)
                                VALUES ('programme', %s, %s, %s, (SELECT id FROM knowledge.topic WHERE slug = %s), %s)
                                ON CONFLICT (kind, key) DO UPDATE SET updated_at = now() RETURNING id""",
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
            self.cur.execute("""INSERT INTO knowledge.node_document VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                             (pid, doc_ids[c["url"]]))
        added = 0
        for r in dossier_rows(facts):
            row, kind = r["row"], r["kind"]
            src, dst = edge_ends(kind, row)
            src_id = self.node(*src) if src else pid
            dst_id = self.node(*dst) if dst else (pid if kind == "restriction" else None)
            h = row_hash(kind, row, r["source_url"], r["archive_id"])
            amount = row.get("amount") if isinstance(row.get("amount"), (int, float)) else None
            ends = row.get("ends_on") if kind == "restriction" else None
            self.cur.execute("""INSERT INTO knowledge.edge (src_node_id, rel, dst_node_id, context_node_id, polarity,
                                       occurred_on, date_precision, valid_to, amount, currency, unit, commitment, what,
                                       source_url, document_id, anchor, item_id, origin_item_id, status, row_data,
                                       row_hash, extractor, run_ref)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                        (SELECT origin_item_id FROM knowledge.item_embedding WHERE item_id = %s),
                                        'verified',%s,%s,%s,%s)
                                ON CONFLICT (context_node_id, row_hash) DO NOTHING""",
                             (src_id, REL[kind], dst_id, pid,
                              "negative" if row.get("commitment") == "suspension" else "positive",
                              valid_date(row.get("date") or row.get("starts_on")),
                              row.get("precision") if row.get("precision") in ("day", "month", "year") else None,
                              valid_date(ends), amount, (row.get("currency") or None),
                              row.get("level") if kind == "supply_chain" else None, row.get("commitment"),
                              r["text"][:2000], r["source_url"], doc_ids.get(r["source_url"]), r["anchor"],
                              r["archive_id"], r["archive_id"], json.dumps(row, ensure_ascii=False), h, version, run_ref))
            added += self.cur.rowcount
        return {"programme": pid, "added": added}
