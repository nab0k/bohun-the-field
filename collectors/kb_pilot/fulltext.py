"""A-11 S2: read the publisher's page for news Inoreader gave no usable text (D-20261031-A11-S2-FETCH).

For first-source items of the window whose Inoreader text is missing or shorter than 300 characters, the page is
fetched with Codex's isolated transport (public addresses only, verified TLS, no redirects, cookies, retries,
paywall or script workarounds) after robots.txt allows it, and the article text is extracted with trafilatura.

measure (default): nothing is written; the run reports how many pages gave a usable text, per site and per reason.
write: the text goes to raw_data.publisher_capture (Inoreader's content_capture is never touched) — only after the
owner has seen a measurement report. Logs and artifacts carry counts and reasons, never article text.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
import sys
import time
from urllib import robotparser
from urllib.parse import urlsplit

from ..synthesis.contracts import canonical
from . import transport

CAPTURE = "kb-fulltext-v1"
USER_AGENT = "BOHUN-Evidence/1.0"  # the transport sends this agent; robots rules are read for it
MIN_TEXT = 300
MAX_PAGES = 60
MAX_TEXT_CHARS = 128 * 1024
ROBOTS_LIMIT = 128 * 1024
HOST_GAP_SECONDS = 2.0
RUN_DEADLINE_SECONDS = 15 * 60


def pending(cur, day_from, day_to, limit):
    """First-source items of the window with no usable Inoreader text and no publisher capture yet."""
    cur.execute("""SELECT i.id, i.url, length(coalesce(i.raw_data->'content_capture'->>'text', ''))
                     FROM intelligence.items i
                LEFT JOIN knowledge.item_embedding e ON e.item_id = i.id
                    WHERE i.published_at >= %s::date AND i.published_at < %s::date + 1
                      AND length(coalesce(i.raw_data->'content_capture'->>'text', '')) < %s
                      AND i.raw_data->'publisher_capture' IS NULL
                      AND coalesce(e.origin_method, 'self') = 'self'
                      AND i.url LIKE 'https://%%'
                 ORDER BY i.published_at DESC, i.id DESC LIMIT %s""", (day_from, day_to, MIN_TEXT, limit))
    return [{"item_id": a, "url": u, "provider_chars": n} for a, u, n in cur.fetchall()]


class Robots:
    """robots.txt per origin, fetched once; 4xx means no rules (allowed), anything else unreadable means denied."""

    def __init__(self, fetch):
        self.fetch, self.cache = fetch, {}

    def allowed(self, url):
        host = urlsplit(url).hostname
        if host not in self.cache:
            robots_url = "https://%s/robots.txt" % host
            try:
                body = self.fetch(robots_url, {robots_url}, limit=ROBOTS_LIMIT, timeout=20, html=False)["body"]
                parser = robotparser.RobotFileParser()
                parser.parse(body.decode("utf-8", "replace").splitlines())
                self.cache[host] = ("rules", parser)
            except transport.FetchError as exc:
                code = str(exc)
                self.cache[host] = ("open", None) if code.startswith("HTTP_4") else ("unreadable:" + code, None)
        state, parser = self.cache[host]
        if state == "open":
            return True, "robots_absent"
        if state != "rules":
            return False, "robots_" + state.split(":", 1)[1].lower()
        return (parser.can_fetch(USER_AGENT, url), "robots_rules")


def extract_text(html_bytes, url):
    import trafilatura  # installed on the runner only (fulltext mode), never on the owner's Mac
    text = trafilatura.extract(html_bytes.decode("utf-8"), url=url, include_comments=False, include_tables=False,
                               favor_precision=True)
    return (text or "").strip()[:MAX_TEXT_CHARS]


def read_one(item, robots, fetch, extract, pace):
    """Outcome of one item: (reason, capture or None)."""
    url = item["url"]
    try:
        transport.target(url)
    except transport.FetchError as exc:
        return str(exc).lower(), None
    ok, why = robots.allowed(url)
    if not ok:
        return "robots_denied" if why == "robots_rules" else why, None
    pace(urlsplit(url).hostname)
    try:
        page = fetch(url, {url}, timeout=20, html=True)
    except transport.FetchError as exc:
        return str(exc).lower(), None
    try:
        text = extract(page["body"], url)
    except Exception:
        return "extract_failed", None
    if len(text) < MIN_TEXT:
        return "text_too_short", None
    if len(text) <= item["provider_chars"]:
        return "not_longer_than_provider", None
    return "ok", {"source_kind": "publisher_article", "capture": CAPTURE, "url": url,
                  "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "http_status": 200,
                  "html_sha256": hashlib.sha256(page["body"]).hexdigest(),
                  "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                  "extractor": "trafilatura", "characters": len(text), "completeness": "UNKNOWN", "text": text}


class Pacer:
    """At least HOST_GAP_SECONDS between two requests to one host; one request at a time."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep, self.last = clock, sleep, {}

    def __call__(self, host):
        wait = self.last.get(host, -1e9) + HOST_GAP_SECONDS - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.last[host] = self.clock()


def run(conn, day_from, day_to, limit=MAX_PAGES, write=False, fetch=transport.fetch, extract=extract_text,
        clock=time.monotonic, pace=None):
    stats = {"capture": CAPTURE, "mode": "write" if write else "measure", "items": 0, "ok": 0, "written": 0,
             "reasons": Counter(), "by_host": defaultdict(Counter), "chars_gained": 0, "stop": None}
    with conn.cursor() as cur:
        todo = pending(cur, day_from, day_to, limit)
    stats["items"] = len(todo)
    robots, pace = Robots(fetch), pace or Pacer()
    started = clock()
    for item in todo:
        if clock() - started > RUN_DEADLINE_SECONDS:
            stats["stop"] = "RUN_DEADLINE"
            break
        reason, capture = read_one(item, robots, fetch, extract, pace)
        host = urlsplit(item["url"]).hostname or "?"
        stats["reasons"][reason] += 1
        stats["by_host"][host][reason] += 1
        if capture:
            stats["ok"] += 1
            stats["chars_gained"] += capture["characters"] - item["provider_chars"]
            if write:
                with conn.cursor() as cur:
                    cur.execute("""UPDATE intelligence.items
                                      SET raw_data = jsonb_set(raw_data, '{publisher_capture}', %s::jsonb, true)
                                    WHERE id = %s AND raw_data->'publisher_capture' IS NULL""",
                                (json.dumps(capture, ensure_ascii=False), item["item_id"]))
                    stats["written"] += cur.rowcount
                conn.commit()
    stats["reasons"] = dict(stats["reasons"])
    stats["by_host"] = {h: dict(c) for h, c in sorted(stats["by_host"].items(), key=lambda kv: -sum(kv[1].values()))}
    return stats


def main(argv=None):
    from ..synthesis.live import connect
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day-from", required=True)
    parser.add_argument("--day-to", required=True)
    parser.add_argument("--limit", type=int, default=MAX_PAGES)
    args, _ = parser.parse_known_args(argv)  # the workflow passes the pilot's flags too
    write = os.environ.get("KB_FULLTEXT_WRITE") == "1"  # off until the owner has seen a measurement report
    with connect() as conn:
        stats = run(conn, args.day_from, args.day_to, min(args.limit or MAX_PAGES, MAX_PAGES), write=write)
    print("KB_FULLTEXT_RESULT " + canonical(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
