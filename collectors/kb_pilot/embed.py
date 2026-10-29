"""Embeddings of news items (EXP-028 v2 step 1, D-20261029-EXP028-V2-STEP1) and their independent origin.

Every item gets a bge-m3 vector (Cloudflare Workers AI, multilingual, 1024 dims) in knowledge.item_embedding.
Origin (Signals memo): an item whose text is the same as, or almost the same as, an earlier item is a reprint of it
and is not an independent piece of evidence; origin_item_id points to the earliest one.
"""
import json
import os
import urllib.error
import urllib.request

from ..synthesis.contracts import sha
from .free import USER_AGENT

MODEL = "@cf/baai/bge-m3"
LOCAL_MODEL = "BAAI/bge-m3"   # the same open weights on the runner CPU: compatible vectors, no account, no daily limit
EMBED_URL = "https://api.cloudflare.com/client/v4/accounts/{}/ai/run/" + MODEL
MAX_CHARS = 1800            # bge-m3 on Workers AI reads up to 512 tokens
BATCH = 50
NEAR_DUPLICATE = 0.06       # cosine distance: below this an earlier item within the window is the same story text
ORIGIN_WINDOW_DAYS = 4


def item_text(title, body):
    return ((title or "").strip() + "\n" + (body or "").strip())[:MAX_CHARS].strip()


def vector_literal(vec):
    return "[" + ",".join("%.6f" % x for x in vec) + "]"


def embed_texts(texts, account, token, transport=None):
    body = {"text": texts}
    req = urllib.request.Request(EMBED_URL.format(account), data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    if transport:
        reply = transport(req)
    else:
        with urllib.request.urlopen(req, timeout=120) as r:
            reply = json.load(r)
    data = (reply.get("result") or {}).get("data")
    if not isinstance(data, list) or len(data) != len(texts) or any(len(v) != 1024 for v in data):
        raise RuntimeError("EMBED_BAD_SHAPE")
    return data


_local = None


def embed_local(texts):
    """Spare airfield: bge-m3 with sentence-transformers on the GitHub runner (installed only in embed mode)."""
    global _local
    if _local is None:
        from sentence_transformers import SentenceTransformer
        _local = SentenceTransformer(LOCAL_MODEL, device="cpu")
        _local.max_seq_length = 512
    return [list(map(float, v)) for v in _local.encode(texts, batch_size=16, normalize_embeddings=True)]


def origin_of(item, neighbours):
    """Earliest earlier item with the same text or a near-identical vector; otherwise the item itself."""
    same = [n for n in neighbours if n["text_sha256"] == item["text_sha256"] and n["published"] <= item["published"]]
    near = [n for n in neighbours if n["distance"] < NEAR_DUPLICATE and n["published"] <= item["published"]]
    for method, pool in (("same_text", same), ("near_duplicate", near)):
        pool = [n for n in pool if n["item_id"] != item["item_id"]]
        if pool:
            first = min(pool, key=lambda n: (n["published"], n["item_id"]))
            return first.get("origin_item_id") or first["item_id"], method
    return item["item_id"], "self"


def pending(cur, limit, day_from=None):
    cur.execute("""SELECT i.id, i.title, i.raw_data->'content_capture'->>'text', i.published_at
                     FROM intelligence.items i
                LEFT JOIN knowledge.item_embedding e ON e.item_id = i.id
                    WHERE e.item_id IS NULL AND i.published_at IS NOT NULL
                      AND (%s::date IS NULL OR i.published_at >= %s::date)
                 ORDER BY i.published_at, i.id LIMIT %s""", (day_from, day_from, limit))
    return [{"item_id": i, "text": item_text(t, b), "published": p} for i, t, b, p in cur.fetchall()]


def neighbours(cur, item_id, k=8):
    cur.execute("""SELECT e2.item_id, e2.text_sha256, e2.origin_item_id, i2.published_at,
                          e2.embedding OPERATOR(extensions.<=>) e1.embedding AS distance
                     FROM knowledge.item_embedding e1
                     JOIN knowledge.item_embedding e2 ON e2.item_id <> e1.item_id
                     JOIN intelligence.items i1 ON i1.id = e1.item_id
                     JOIN intelligence.items i2 ON i2.id = e2.item_id
                    WHERE e1.item_id = %s
                      AND i2.published_at BETWEEN i1.published_at - make_interval(days => %s) AND i1.published_at
                 ORDER BY e2.embedding OPERATOR(extensions.<=>) e1.embedding LIMIT %s""",
                (item_id, ORIGIN_WINDOW_DAYS, k))
    return [{"item_id": a, "text_sha256": b, "origin_item_id": c, "published": d, "distance": float(e)}
            for a, b, c, d, e in cur.fetchall()]


def similar_items(cur, item_ids, before, k=15, max_distance=0.45, years=6):
    """Earlier items closest in meaning to the given items, one per origin (reprints collapse)."""
    cur.execute("""SELECT DISTINCT ON (COALESCE(e2.origin_item_id, e2.item_id)) i2.id, i2.title, i2.url, i2.published_at,
                          left(i2.raw_data->'content_capture'->>'text', 1500),
                          e2.embedding OPERATOR(extensions.<=>) e1.embedding AS distance
                     FROM knowledge.item_embedding e1
                     JOIN knowledge.item_embedding e2 ON e2.item_id <> ALL(%s)
                     JOIN intelligence.items i2 ON i2.id = e2.item_id
                    WHERE e1.item_id = ANY(%s)
                      AND i2.published_at < %s::date AND i2.published_at >= %s::date - make_interval(years => %s)
                      AND e2.embedding OPERATOR(extensions.<=>) e1.embedding < %s
                 ORDER BY COALESCE(e2.origin_item_id, e2.item_id), distance""",
                (list(item_ids), list(item_ids), before[:10], before[:10], years, max_distance))
    rows = sorted(cur.fetchall(), key=lambda r: r[5])[:k]
    return [{"archive_id": i, "title": t, "url": u, "date": p.date().isoformat(), "text": (t or "") + "\n" + (b or ""),
             "distance": round(float(d), 3)} for i, t, u, p, b, d in rows]


def run(conn, account, token, limit=8000, day_from=None, transport=None, backend="auto", local=embed_local):
    """backend: auto (Cloudflare, then the local model once Cloudflare refuses), cloudflare, local."""
    stats = {"embedded": 0, "batches": 0, "origin": {"self": 0, "same_text": 0, "near_duplicate": 0}, "errors": 0,
             "by_backend": {}}
    use_local = backend == "local" or not (account and token)
    with conn.cursor() as cur:
        todo = pending(cur, limit, day_from)
    for i in range(0, len(todo), BATCH):
        chunk = [it for it in todo[i:i + BATCH] if it["text"]]
        if not chunk:
            continue
        vectors, model = None, MODEL
        if not use_local:
            try:
                vectors = embed_texts([it["text"] for it in chunk], account, token, transport)
            except urllib.error.HTTPError as exc:
                try:
                    detail = [e.get("message", "")[:100] for e in json.loads(exc.read().decode()).get("errors") or []]
                except Exception:
                    detail = []
                stats.setdefault("error_codes", {}).setdefault(str(exc.code), detail)
                if backend == "cloudflare":
                    stats["errors"] += 1
                    if exc.code == 429:
                        stats["stop"] = "FREE_ALLOCATION_429"
                        break
                    continue
                use_local = True  # 429 daily allocation, 5xx, auth: the rest of the run goes local
            except (urllib.error.URLError, RuntimeError) as exc:
                stats.setdefault("error_codes", {})[type(exc).__name__ + ":" + str(exc)[:60]] = []
                if backend == "cloudflare":
                    stats["errors"] += 1
                    continue
                use_local = True
        if vectors is None:
            vectors, model = local([it["text"] for it in chunk]), LOCAL_MODEL
        stats["by_backend"][model] = stats["by_backend"].get(model, 0) + len(chunk)
        stats["batches"] += 1
        with conn.cursor() as cur:
            for it, vec in zip(chunk, vectors):
                cur.execute("""INSERT INTO knowledge.item_embedding (item_id, model, text_sha256, embedding)
                               VALUES (%s, %s, %s, %s::extensions.halfvec)
                               ON CONFLICT (item_id) DO UPDATE SET model = EXCLUDED.model,
                                 text_sha256 = EXCLUDED.text_sha256, embedding = EXCLUDED.embedding""",
                            (it["item_id"], model, sha(it["text"]), vector_literal(vec)))
            for it in chunk:  # chronological order: earlier items already have their origin
                it["text_sha256"] = sha(it["text"])
                origin, method = origin_of(it, neighbours(cur, it["item_id"]))
                cur.execute("UPDATE knowledge.item_embedding SET origin_item_id = %s, origin_method = %s WHERE item_id = %s",
                            (origin, method, it["item_id"]))
                stats["origin"][method] += 1
        conn.commit()
        stats["embedded"] += len(chunk)
    return stats


def main():
    from ..synthesis.live import connect
    account = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = (os.environ.get("CLOUDFLARE_WORKERS_AI_TOKEN") or "").strip()
    backend = os.environ.get("KB_EMBED_BACKEND") or "auto"
    # Workflow inputs: limit (0 = all, up to 8000) and day_from (items published from that day).
    limit = int(os.environ.get("LIMIT") or "0") or 8000
    with connect() as conn:
        stats = run(conn, account, token, limit=limit, day_from=os.environ.get("DAY_FROM") or None, backend=backend)
    print("KB_EMBED_RESULT " + json.dumps(stats))
    return 0 if not stats["errors"] else 1
