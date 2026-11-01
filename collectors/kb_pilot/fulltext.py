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
from .body_selection import CAPTURE, MAX_TEXT_BYTES, select_body

USER_AGENT = "BOHUN-Evidence/1.0"  # the transport sends this agent; robots rules are read for it
MIN_TEXT = 300
MAX_PAGES = 60
ROBOTS_LIMIT = 128 * 1024
HOST_GAP_SECONDS = 2.0
RUN_DEADLINE_SECONDS = 15 * 60


def pending(cur, day_from, day_to, limit):
    """Only reviewed source/news scope; preserve a pre-fetch compare-and-set receipt."""
    cur.execute("""SELECT i.id, i.source_id, i.url,
                          jsonb_build_object('_raw_object',jsonb_typeof(i.raw_data)='object',
                                             'content_capture', i.raw_data->'content_capture',
                                             'publisher_capture', i.raw_data->'publisher_capture'),
                          i.raw_data ? 'content_capture', i.raw_data ? 'publisher_capture',
                          e.item_id IS NOT NULL,
                          EXISTS (SELECT 1 FROM knowledge.item_processing p WHERE p.item_id=i.id)
                     FROM intelligence.items i
                     JOIN intelligence.sources s ON s.id=i.source_id
                LEFT JOIN knowledge.item_embedding e ON e.item_id=i.id
                    WHERE s.name='Inoreader BOHUN' AND i.item_type='news'
                      AND i.published_at >= %s::date AND i.published_at < %s::date + 1
                      AND length(coalesce(i.raw_data->'content_capture'->>'text','')) < %s
                      AND (e.item_id IS NULL OR e.origin_method='self')
                      AND i.url LIKE 'https://%%'
                 ORDER BY i.published_at DESC, i.id DESC LIMIT %s""", (day_from, day_to, MIN_TEXT, limit))
    items = []
    for iid, source_id, url, raw, provider_present, publisher_present, embedded, processed in cur.fetchall():
        provider = raw.get('content_capture') if isinstance(raw, dict) else None
        text = provider.get('text') if isinstance(provider, dict) else None
        items.append({'item_id': iid, 'source_id': source_id, 'url': url, 'raw': raw,
                      'provider_chars': len(text) if isinstance(text, str) else 0,
                      'provider_present': provider_present, 'publisher_present': publisher_present,
                      'embedded': embedded, 'processed': processed})
    return items


WRITE_SQL = """UPDATE intelligence.items i
 SET raw_data=jsonb_set(i.raw_data,'{publisher_capture}',%s::jsonb,true)
 WHERE jsonb_typeof(i.raw_data)='object' AND i.id=%s AND i.source_id=%s AND i.url IS NOT DISTINCT FROM %s AND i.item_type='news'
   AND (i.raw_data ? 'content_capture')=%s AND (i.raw_data ? 'publisher_capture')=%s
   AND coalesce(i.raw_data->'content_capture','null'::jsonb)=%s::jsonb
   AND coalesce(i.raw_data->'publisher_capture','null'::jsonb)=%s::jsonb
   AND NOT EXISTS (SELECT 1 FROM knowledge.item_processing p WHERE p.item_id=i.id)
   AND NOT EXISTS (SELECT 1 FROM knowledge.item_embedding e WHERE e.item_id=i.id)
   AND EXISTS (SELECT 1 FROM intelligence.sources s WHERE s.id=i.source_id AND s.name='Inoreader BOHUN')"""


def write_capture(conn, item, capture):
    # Existing/invalid publisher bodies require a separately reviewed repair mode.
    raw = item['raw']
    if not isinstance(raw, dict) or raw.get('_raw_object') is False:
        return 'RAW_DATA_INVALID'
    if item['processed'] or item['embedded'] or raw.get('publisher_capture') is not None:
        return 'WRITE_REQUIRES_NEW_UNPROCESSED_ITEM'
    if select_body({'publisher_capture': capture}, item['url']).source_kind != 'publisher_article':
        return 'CAPTURE_VALIDATION_FAILED'
    try:
        with conn.cursor() as cur:
            cur.execute(WRITE_SQL, (json.dumps(capture, ensure_ascii=False), item['item_id'], item['source_id'],
                                   item['url'], item['provider_present'], item['publisher_present'],
                                   json.dumps(raw.get('content_capture'), ensure_ascii=False),
                                   json.dumps(raw.get('publisher_capture'), ensure_ascii=False)))
            updated = cur.rowcount
            if updated not in (0, 1):
                raise RuntimeError('UNEXPECTED_ROW_COUNT')
        conn.commit()
        return 'WRITTEN' if updated else 'STALE_NOT_WRITTEN'
    except Exception:
        conn.rollback()
        raise RuntimeError('PUBLISHER_WRITE_FAILED') from None


class Robots:
    """robots.txt per origin, fetched once; only 404/410 means absent rules; forbidden/rate-limited/unreadable is denied."""

    def __init__(self, fetch, pace=None):
        self.fetch, self.cache = fetch, {}
        self.pace = pace or (lambda host: None)

    def allowed(self, url):
        host = urlsplit(url).hostname
        if host not in self.cache:
            robots_url = "https://%s/robots.txt" % host
            try:
                self.pace(host)
                body = self.fetch(robots_url, {robots_url}, limit=ROBOTS_LIMIT, timeout=20, html=False)["body"]
                parser = robotparser.RobotFileParser()
                parser.parse(body.decode("utf-8").splitlines())
                self.cache[host] = ("rules", parser)
            except transport.FetchError as exc:
                code = str(exc)
                self.cache[host] = ("open", None) if code in ("HTTP_404", "HTTP_410") else ("unreadable:" + code, None)
            except UnicodeError:
                self.cache[host] = ("unreadable:UTF8_INVALID", None)
        state, parser = self.cache[host]
        if state == "open":
            return True, "robots_absent"
        if state != "rules":
            return False, "robots_" + state.split(":", 1)[1].lower()
        return (parser.can_fetch(USER_AGENT, url), "robots_rules")


def extract_text(html_bytes, url):
    import importlib.metadata
    if importlib.metadata.version("trafilatura") != "2.3.1":
        raise ValueError("EXTRACTOR_VERSION_MISMATCH")
    import trafilatura  # runner only
    text = trafilatura.extract(html_bytes.decode("utf-8"), url=url, include_comments=False, include_tables=False,
                               favor_precision=True)
    return (text or "").strip()


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
    if page.get("url") != url or page.get("status") != 200:
        return "page_receipt_mismatch", None
    try:
        text = extract(page["body"], url)
    except Exception:
        return "extract_failed", None
    if not isinstance(text, str):
        return "extract_shape_invalid", None
    try:
        text_bytes = text.encode("utf-8")
    except UnicodeError:
        return "text_utf8_invalid", None
    if len(text_bytes) > MAX_TEXT_BYTES:
        return "text_bytes_exceeded", None
    if len(text) < MIN_TEXT:
        return "text_too_short", None
    if len(text) <= item["provider_chars"]:
        return "not_longer_than_provider", None
    return "ok", {"source_kind": "publisher_article", "capture": CAPTURE, "url": url,
                  "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "http_status": 200,
                  "html_sha256": hashlib.sha256(page["body"]).hexdigest(),
                  "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                  "extractor": "trafilatura", "extractor_version": "2.3.1",
                  "requested_url": url, "final_url": page["url"], "text_bytes": len(text_bytes), "characters": len(text), "completeness": "UNKNOWN", "text": text}


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
    if type(limit) is not int or not 1 <= limit <= MAX_PAGES:
        raise ValueError('limit must be 1..60')
    stats = {"capture": CAPTURE, "mode": "write" if write else "measure", "items": 0, "ok": 0, "written": 0,
             "reasons": Counter(), "by_host": defaultdict(Counter), "chars_gained": 0, "stop": None}
    with conn.cursor() as cur:
        todo = pending(cur, day_from, day_to, limit)
    stats["items"] = len(todo)
    pace = pace or Pacer()
    started = clock()
    def bounded_fetch(*args, **kwargs):
        remaining = RUN_DEADLINE_SECONDS - (clock() - started)
        if remaining <= 0:
            raise transport.FetchError('RUN_DEADLINE')
        stats['http_attempts'] += 1
        stats['robots_attempts'] += int(args[0].endswith('/robots.txt'))
        kwargs['timeout'] = min(kwargs.get('timeout', 20), remaining)
        return fetch(*args, **kwargs)
    stats.update(http_attempts=0, robots_attempts=0, not_attempted=0, stale_not_written=0)
    robots = Robots(bounded_fetch, pace)
    for index, item in enumerate(todo):
        if clock() - started >= RUN_DEADLINE_SECONDS:
            stats['stop'] = 'RUN_DEADLINE'
            stats['not_attempted'] = len(todo) - index
            break
        existing = item['raw'].get('publisher_capture') if isinstance(item['raw'], dict) else None
        if not isinstance(item['raw'], dict) or item['raw'].get('_raw_object') is False:
            reason, capture = 'raw_data_invalid', None
        elif existing is not None:
            body = select_body(item['raw'], item['url'])
            reason = 'publisher_already_present' if body.source_kind == 'publisher_article' else 'publisher_repair_requires_review'
            capture = None
        elif write and (item['embedded'] or item['processed']):
            reason, capture = 'write_requires_new_unprocessed_item', None
        else:
            reason, capture = read_one(item, robots, bounded_fetch, extract, pace)
        deadline_reached = reason.endswith('run_deadline')
        host = urlsplit(item['url']).hostname or '?'
        stats['reasons'][reason] += 1
        stats['by_host'][host][reason] += 1
        if capture:
            stats['ok'] += 1
            stats['chars_gained'] += capture['characters'] - item['provider_chars']
            if write:
                try:
                    outcome = write_capture(conn, item, capture)
                except RuntimeError:
                    stats['stop'] = 'PUBLISHER_WRITE_FAILED'
                    stats['not_attempted'] = len(todo) - index - 1
                    break
                if outcome == 'WRITTEN':
                    stats['written'] += 1
                else:
                    stats['reasons'][outcome.lower()] += 1
                    stats['stale_not_written'] += int(outcome == 'STALE_NOT_WRITTEN')
        if deadline_reached:
            stats['stop'] = 'RUN_DEADLINE'
            stats['not_attempted'] = len(todo) - index - 1
            break
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
    if args.limit < 0:
        parser.error("--limit must be 0..60")
    with connect() as conn:
        stats = run(conn, args.day_from, args.day_to, min(args.limit or MAX_PAGES, MAX_PAGES), write=write)
    print("KB_FULLTEXT_RESULT " + canonical(stats))
    return 1 if stats["stop"] else 0


if __name__ == "__main__":
    sys.exit(main())
