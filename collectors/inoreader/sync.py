#!/usr/bin/env python3
"""Inoreader BOHUN_* folders -> Supabase intelligence.items.

Read-only Inoreader access. Stores metadata (headline, URL, timestamps), not article bodies.
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

API = "https://www.inoreader.com/reader/api/0/"
TOKEN = "https://www.inoreader.com/oauth2/token"
USER_AGENT = "Bohundefence-Collector/1.0"
PREFIX = "BOHUN_"


def request_json(url, *, headers=None, form=None):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    if body is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


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
        print("WARNING: refresh token rotated; update INOREADER_REFRESH_TOKEN secret", file=sys.stderr)
    return response["access_token"]


def api(path, token, params=None):
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


def stream_items(folder, token, max_pages):
    stream = "user/-/label/" + folder
    seen = set()
    continuation = None
    for _ in range(max_pages):
        params = {"n": 100}
        if continuation:
            params["c"] = continuation
        result = api("stream/contents/" + urllib.parse.quote(stream, safe="/"), token, params)
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


def normalize(item, folder):
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
            "inoreader_id": item["id"],
            "folder": folder,
            "origin_title": origin.get("title"),
            "origin_stream_id": origin.get("streamId"),
            "published": published,
            "url": url,
            "title": title,
        },
    }


def import_items(items):
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
                for item in items:
                    cur.execute("""INSERT INTO intelligence.items
                        (source_id, external_id, title, url, item_type, published_at, raw_data)
                        VALUES (%s,%s,%s,%s,'news',%s,%s)
                        ON CONFLICT (source_id, external_id) DO NOTHING
                        RETURNING id""",
                        (source_id, item["external_id"], item["title"], item["url"],
                         item["published_at"], Jsonb(item["raw_data"])))
                    inserted += int(cur.fetchone() is not None)
                cur.execute("SELECT count(*) FROM intelligence.items WHERE source_id = %s", (source_id,))
                total = cur.fetchone()[0]
    return inserted, total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-pages", type=int, default=2, help="Per BOHUN folder, max 100 items per page")
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 20:
        parser.error("--max-pages must be 1..20")
    token = access_token()
    subscriptions = api("subscription/list", token).get("subscriptions", [])
    folders, source_count = folders_from_subscriptions(subscriptions)
    print(f"DISCOVERED subscriptions={len(subscriptions)} bohun_subscriptions={source_count} folders={len(folders)}")
    if not folders:
        raise RuntimeError("No BOHUN_ folders found; refusing empty successful run")
    unique = {}
    for folder in folders:
        count = 0
        for raw in stream_items(folder, token, args.max_pages):
            item = normalize(raw, folder)
            if item["external_id"] not in unique:
                unique[item["external_id"]] = item
            count += 1
        print(f"FOLDER {folder}: fetched={count}")
    items = list(unique.values())
    print(f"VALIDATED unique_items={len(items)}")
    if args.dry_run:
        print("DRY RUN: no database writes")
        return
    new, total = import_items(items)
    print(f"COMMITTED fetched={len(items)} new={new} cloud_inoreader_items={total}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        # Do not log URLs, response bodies or credentials.
        print(f"FAILED: {type(exc).__name__}; check secrets, API limits and database", file=sys.stderr)
        sys.exit(1)
