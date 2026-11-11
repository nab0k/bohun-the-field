#!/usr/bin/env python3
"""Inoreader BOHUN_* folders -> Supabase intelligence.items.

Read-only Inoreader access. Captures metadata and bounded provider body content;
body presence does not establish completeness, source verification or publication QA.
Requires: INOREADER_CLIENT_ID, INOREADER_CLIENT_SECRET, INOREADER_REFRESH_TOKEN,
SUPABASE_DB_PASSWORD. Run --dry-run to inspect without database writes.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

if __package__:
    from .content_contract import capture_size, extract_inoreader_content
    from .body_backfill import backfill_missing_body
elif __name__ == "__main__":
    from content_contract import capture_size, extract_inoreader_content
    from body_backfill import backfill_missing_body
else:
    # Support existing file-spec test loaders without changing sys.path.
    from collectors.inoreader.content_contract import capture_size, extract_inoreader_content
    from collectors.inoreader.body_backfill import backfill_missing_body

API = "https://www.inoreader.com/reader/api/0/"
TOKEN = "https://www.inoreader.com/oauth2/token"
USER_AGENT = "Bohundefence-Collector/1.0"
PREFIX = "BOHUN_"


def request_json(url, *, headers=None, form=None, admission=None):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    if body is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body else "GET")
    for attempt in range(4):
        if admission is not None:
            admission.before_request(url, req.get_method())
        try:
            opener = urllib.request.urlopen if admission is None else admission.open_request
            with opener(req, timeout=45) as response:
                if admission is not None:
                    admission.observe(response.headers)
                return json.load(response) if admission is None else admission.read_json(response)
        except urllib.error.HTTPError as exc:
            if admission is not None:
                try:
                    admission.observe(exc.headers or {})
                finally:
                    exc.close()
            stage = "oauth_token" if url.startswith(TOKEN) else "inoreader_api"
            if exc.code == 429 and attempt < 3 and admission is None:
                # Honour server Retry-After when available; otherwise exponential backoff.
                raw_wait = exc.headers.get("Retry-After", "") if exc.headers else ""
                try:
                    wait = max(1, min(120, int(raw_wait)))
                except (ValueError, TypeError):
                    wait = min(60, 5 * (2 ** attempt))
                print(f"RATE_LIMIT stage={stage} retry={attempt + 1} wait_seconds={wait}", file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(
                f"HTTP request failed: stage={stage}, status={exc.code}"
                + ("; refresh token rejected or OAuth configuration mismatch" if stage == "oauth_token" and exc.code in (400, 401) else "")
            ) from None


class OAuthTokenRotated(RuntimeError):
    """Refresh token rotated; no persistent token store is configured."""


def access_token():
    values = {key: os.environ.get(key) for key in (
        "INOREADER_CLIENT_ID", "INOREADER_CLIENT_SECRET", "INOREADER_REFRESH_TOKEN")}
    if not all(values.values()):
        raise RuntimeError("Missing Inoreader OAuth environment variables")
    response = request_json(TOKEN, form={
        "client_id": values["INOREADER_CLIENT_ID"],
        "client_secret": values["INOREADER_CLIENT_SECRET"],
        "refresh_token": values["INOREADER_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    if not response.get("access_token"):
        raise RuntimeError("Inoreader token refresh did not return access_token")
    # Some OAuth providers rotate refresh tokens. Never print returned credentials.
    if response.get("refresh_token") and response["refresh_token"] != values["INOREADER_REFRESH_TOKEN"]:
        raise OAuthTokenRotated("OAUTH_ROTATED: new refresh token was returned but cannot be persisted; stop collection and reauthorize securely")
    return response["access_token"]


def api(path, token, params=None, *, services=None):
    if services is not None:
        return services.api(path, params=params)
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return request_json(url, headers={"Authorization": "Bearer " + token})


def folders_from_subscriptions(subscriptions):
    folders = set()
    source_count = 0
    for subscription in subscriptions:
        matched = False
        for category in subscription.get("categories", []):
            identifier = category.get("id", "")
            label = category.get("label") or urllib.parse.unquote(identifier.rsplit("/", 1)[-1])
            if label.startswith(PREFIX):
                folders.add(label)
                matched = True
        source_count += int(matched)
    return sorted(folders), source_count


def stream_items(folder, token, max_pages, page_size=100, *, services=None):
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("stream page_size must be 1..100")
    stream = "user/-/label/" + folder
    seen = set()
    continuation = None
    for _ in range(max_pages):
        params = {"n": page_size}
        if continuation:
            params["c"] = continuation
        path = "stream/contents/" + urllib.parse.quote(stream, safe="/")
        result = (api(path, token, params) if services is None else
                  api(path, token, params, services=services))
        for item in result.get("items", []):
            external_id = str(item.get("id") or "").strip()
            if external_id and external_id not in seen:
                seen.add(external_id)
                yield item
        new_continuation = result.get("continuation")
        if not new_continuation or new_continuation == continuation:
            break
        continuation = new_continuation
        time.sleep(0.25)


def normalize(item, folder, *, captured_at=None, content_budget=None, collector_cycle_id=None):
    if collector_cycle_id is not None and (
            not isinstance(collector_cycle_id, str) or len(collector_cycle_id) != 42 or
            not collector_cycle_id.startswith("inoreader:") or
            any(c not in "0123456789abcdef" for c in collector_cycle_id[10:])):
        raise ValueError("Invalid collector cycle ID")
    links = item.get("canonical") or item.get("alternate") or []
    url = next((link.get("href") for link in links if link.get("href")), None)
    title = item.get("title") or "(untitled)"
    published = item.get("published") or item.get("updated")
    timestamp = dt.datetime.fromtimestamp(published, dt.timezone.utc) if isinstance(published, (int, float)) else None
    origin = item.get("origin") or {}
    return {
        "external_id": str(item["id"]),
        "title": title[:2000],
        "url": url,
        "published_at": timestamp,
        "raw_data": {
            **({"collector_cycle_id": collector_cycle_id} if collector_cycle_id is not None else {}),
            "inoreader_id": item["id"],
            "folder": folder,
            "origin_title": origin.get("title"),
            "origin_stream_id": origin.get("streamId"),
            "published": published,
            "url": url,
            "title": title,
            "content_capture": extract_inoreader_content(
                item, captured_at=captured_at, budget=content_budget),
        },
    }


def import_items(items, *, backfill_bodies=False):
    import psycopg
    from psycopg.types.json import Jsonb
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if not password:
        raise RuntimeError("Missing SUPABASE_DB_PASSWORD")
    params = dict(
        host=os.environ.get("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
        dbname="postgres",
        user=os.environ.get("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        password=password, sslmode="require", connect_timeout=20,
    )
    with psycopg.connect(**params) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                if backfill_bodies:
                    cur.execute("SET LOCAL statement_timeout = '15s'")
                    cur.execute("SET LOCAL lock_timeout = '5s'")
                cur.execute("SELECT to_regclass('intelligence.sources'), to_regclass('intelligence.items')")
                if any(value is None for value in cur.fetchone()):
                    raise RuntimeError("Missing intelligence.sources or intelligence.items")
                cur.execute("SELECT id FROM intelligence.sources WHERE name = %s ORDER BY id LIMIT 1", ("Inoreader BOHUN",))
                row = cur.fetchone()
                if row:
                    source_id = row[0]
                else:
                    cur.execute("""INSERT INTO intelligence.sources (name, url, source_type, collection_method)
                        VALUES (%s,%s,%s,%s) RETURNING id""",
                        ("Inoreader BOHUN", "https://www.inoreader.com/", "news", "official_api"))
                    source_id = cur.fetchone()[0]
                inserted = 0
                backfilled = 0
                for item in items:
                    cur.execute("""INSERT INTO intelligence.items
                        (source_id, external_id, title, url, item_type, published_at, raw_data)
                        VALUES (%s,%s,%s,%s,'news',%s,%s)
                        ON CONFLICT (source_id, external_id) DO NOTHING
                        RETURNING id""",
                        (source_id, item["external_id"], item["title"], item["url"],
                         item["published_at"], Jsonb(item["raw_data"])))
                    created = cur.fetchone() is not None
                    inserted += int(created)
                    if not created and backfill_bodies:
                        backfilled += int(backfill_missing_body(cur, source_id, item, Jsonb))
                cur.execute("SELECT count(*) FROM intelligence.items WHERE source_id = %s", (source_id,))
                total = cur.fetchone()[0]
    if backfill_bodies:
        print(f"CONTENT_BACKFILL updated={backfilled}")
    return inserted, total


BATCH_ITEMS = 50
BATCH_CAPTURE_BYTES = 5 * 1024 * 1024


def normalized_items(folders, token, max_pages, max_items=None, *, services=None):
    """Deduplicate IDs only; provider pages remain bounded by stream_items."""
    collector_cycle_id = "inoreader:" + uuid.uuid4().hex
    seen = set()
    for folder in folders:
        if max_items is not None and len(seen) >= max_items:
            break
        count = 0
        stream_options = {} if max_items is None else {"page_size": min(100, max_items - len(seen))}
        if services is not None:
            stream_options["services"] = services
        stream = stream_items(folder, token, max_pages, **stream_options)
        for raw in stream:
            external_id = str(raw["id"])
            count += 1
            if external_id not in seen:
                seen.add(external_id)
                # No whole-run eviction budget: each capture is bounded individually.
                yield normalize(raw, folder, collector_cycle_id=collector_cycle_id)
            if max_items is not None and len(seen) >= max_items:
                break
        print(f"FOLDER {folder}: fetched={count}")


def process_batches(items, writer=None, *, max_items=BATCH_ITEMS,
                    max_capture_bytes=BATCH_CAPTURE_BYTES):
    """Flush before exceeding a buffer limit; never evict a received body.

    writer must return only after committing its batch. An oversized single
    capture is sent alone. Earlier committed batches survive a later failure.
    None is a non-persisting observer, used by the still-networked CLI dry run.
    """
    for limit in (max_items, max_capture_bytes):
        if type(limit) is not int or limit <= 0:
            raise ValueError("Batch limits must be positive integers")
    stats = {"items": 0, "bytes": 0, "statuses": {}, "batches": 0,
             "committed_items": 0, "new": 0, "total": None}
    batch = []
    batch_bytes = 0

    def flush():
        nonlocal batch_bytes
        if not batch:
            return
        if writer is not None:
            new, total = writer(batch)
            stats["committed_items"] += len(batch)
            stats["new"] += new
            stats["total"] = total
        stats["batches"] += 1
        if writer is not None:
            print(f"BATCH_COMMITTED batch={stats['batches']} items={len(batch)} "
                  f"capture_bytes={batch_bytes} committed_items={stats['committed_items']}")
        batch.clear()
        batch_bytes = 0

    try:
        for item in items:
            capture = item["raw_data"]["content_capture"]
            size = capture_size(capture)
            if batch and (len(batch) >= max_items or batch_bytes + size > max_capture_bytes):
                flush()
            batch.append(item)
            batch_bytes += size
            stats["items"] += 1
            stats["bytes"] += size
            status = capture["status"]
            stats["statuses"][status] = stats["statuses"].get(status, 0) + 1
            if len(batch) >= max_items or batch_bytes >= max_capture_bytes:
                flush()
        flush()
    except Exception:
        # Never emit provider bodies, URLs, credentials or raw DB exceptions.
        raise RuntimeError(
            f"BATCH_PROCESSING_FAILED committed_batches={stats['batches'] if writer else 0} "
            f"committed_items={stats['committed_items']}; rerun is safe via existing unique/backfill guards"
        ) from None
    return stats


def main(argv=None, *, services=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-pages", type=int, default=2, help="Per BOHUN folder, max 100 items per page")
    parser.add_argument("--max-items", type=int, help="Optional bound on unique items across all folders (1..1000)")
    parser.add_argument("--backfill-bodies", action="store_true", help="Fill absent provider bodies on matching existing items; preserve stored bodies")
    args = parser.parse_args(argv)
    if not 1 <= args.max_pages <= 20:
        parser.error("--max-pages must be 1..20")
    if args.max_items is not None and not 1 <= args.max_items <= 1000:
        parser.error("--max-items must be 1..1000")
    token = access_token() if services is None else services.access_token()
    subscriptions = (api("subscription/list", token) if services is None else
                     api("subscription/list", token, services=services)).get("subscriptions", [])
    folders, source_count = folders_from_subscriptions(subscriptions)
    print(f"DISCOVERED subscriptions={len(subscriptions)} bohun_subscriptions={source_count} folders={len(folders)}")
    if not folders:
        raise RuntimeError("No BOHUN_ folders found; refusing empty successful run")
    items = (normalized_items(folders, token, args.max_pages, args.max_items) if services is None else
             normalized_items(folders, token, args.max_pages, args.max_items, services=services))
    writer = None if args.dry_run else (
        (lambda batch: import_items(batch, backfill_bodies=True)) if args.backfill_bodies else import_items)
    stats = process_batches(items, writer)
    print(f"VALIDATED unique_items={stats['items']}")
    print(f"CONTENT_CAPTURE bytes={stats['bytes']} statuses={json.dumps(stats['statuses'], sort_keys=True)}")
    if args.dry_run:
        print("DRY RUN: no database writes (Inoreader API access was live)")
        return
    print(f"COMMITTED fetched={stats['committed_items']} new={stats['new']} cloud_inoreader_items={stats['total']}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"FAILED: {type(exc).__name__}; check network", file=sys.stderr)
        sys.exit(1)
