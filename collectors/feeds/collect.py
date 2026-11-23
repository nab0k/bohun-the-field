"""A4SC-05 own feed collector (D-20261120-A4SC-FEED-COLLECTOR): feeds of the sources accepted by the A1SD search,
read directly - no Inoreader, no shared Inoreader quota.

  python -B -m collectors.feeds.collect --mode shadow [--max-feeds 200]

Stage 2 (--mode write, D-20261120-A4SC-FEED-WRITE): the same entries also go to intelligence.items under one source
"BOHUN feeds (A1SD)" (source_type news, collection_method rss), ON CONFLICT (source_id, external_id) DO NOTHING,
skipping any url already in intelligence.items from another source (Inoreader). The shadow tables stay as the
health record.

Stage 1 (shadow): writes only source_discovery.feed_poll (per feed: status, conditional-GET validators, counts) and
source_discovery.feed_items_shadow (per entry: url, title, date, text length, whether the url is already in
intelligence.items). Product tables are only read (the duplicate check). Writing intelligence.items is stage 2, by a
separate owner decision. Polite: robots.txt, 2 s between requests to one host, conditional GET, 15 s and 2 MB per
response. The repository is public: the log carries counts only.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import Counter

from collectors.feeds import pages, podcast, youtube

SOURCE_NAME = "BOHUN feeds (A1SD)"
UA = "BOHUN-FeedCollector/1.0 (+https://github.com/nab0k/bohun-the-field)"
MAX_BYTES = 2_000_000
TIMEOUT = 15
HOST_PAUSE = 2.0
FRESH_DAYS = 7
PER_FEED = 50
RELAY_ON = (403,)
RELAY_TIMEOUT = 30
BUSY_PER_WEEK = 56      # 8 new entries a day: such a feed is also read by the 30-minute busy run
TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|mc_cid|mc_eid|ref|rss|cmpid)$", re.I)

FEEDS_SQL = """
select s.channel_id, s.canonical_url, d.rules_version, case when d.decision='accept' then d.record->'judge' end,
       case when d.decision='accept' then d.reasons end,
       p.etag, p.last_modified
from source_discovery.source_state s
join lateral (select decision, rules_version, record, reasons from source_discovery.admission_decision a
              where a.channel_id = s.channel_id order by decision_id desc limit 1) d on true
left join lateral (select etag, last_modified from source_discovery.feed_poll f
                   where f.channel_id = s.channel_id and f.http_status = 200 order by polled_at desc limit 1) p on true
where s.status = 'accepted' and s.delivery = 'feed'
order by s.since
"""


# A4SC-06 busy feeds (owner 21.11 "опрашивать частые ленты чаще", Miniflux's entry-frequency idea): 8+ new entries a
# day over 7 days, or a poll of the last day whose every entry was fresh (the feed window may be shorter than 2 hours)
BUSY_SQL = """
select channel_id from source_discovery.feed_items_shadow
where seen_at > now() - interval '7 days' group by 1 having count(*) >= %s
union
select channel_id from source_discovery.feed_poll
where polled_at > now() - interval '1 day' and entries >= 10 and fresh >= entries
"""

# A4SC-06 (owner 21.11 "Да, готовь сравнение"): the Inoreader feeds are read by this collector in parallel, shadow
# only (delivery 'feed_compare'), to compare coverage and delay on one window before Inoreader may be switched off.
COMPARE_SQL = """
select s.channel_id, s.canonical_url, p.etag, p.last_modified
from source_discovery.source_state s
left join lateral (select etag, last_modified from source_discovery.feed_poll f
                   where f.channel_id = s.channel_id and f.http_status = 200 order by polled_at desc limit 1) p on true
where s.status = 'accepted' and s.delivery = 'feed_compare'
order by s.since
"""


def compare_feeds(cur, fetcher, now, limit=120, only=None):
    """Inoreader's feeds, polled like ours; every row is shadow only (never written to intelligence.items).
    only: channel ids of the busy run."""
    cur.execute(COMPARE_SQL)
    feeds = [{"channel_id": c, "url": u, "etag": e, "last_modified": m} for c, u, e, m in cur.fetchall()
             if u and u.startswith(("https://", "http://")) and (only is None or c in only)][:limit]
    polls, shadow = poll(feeds, fetcher, now, max_requests=1000) if feeds else ([], [])
    for s in shadow:
        s["skip_write"] = True
    return polls, shadow


def eligible(rows, limit):
    """Accepted feeds checked under rules >= 0.7 with the judge's verdict, or accepted by the owner."""
    from collectors.source_registry.search.eligibility import can_poll

    out = []
    for channel, url, rules, judged, reasons, etag, modified in rows:
        if can_poll(url, rules, judged, reasons):
            out.append({"channel_id": channel, "url": url, "etag": etag, "last_modified": modified})
    return out[:limit]


def canonical(url):
    """Article address without fragment and tracking parameters; host lower-case."""
    p = urllib.parse.urlsplit((url or "").strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
                                    if not TRACKING.match(k)])
    netloc = p.hostname.lower() + (f":{p.port}" if p.port else "")
    return urllib.parse.urlunsplit((p.scheme, netloc, p.path or "/", query, ""))


def external_id(url, guid=None):
    key = canonical(url) or (guid or "")
    return hashlib.sha256(key.encode("utf-8")).hexdigest() if key else None


def entries(body, feed_url, now, fresh_days=FRESH_DAYS, per_feed=PER_FEED):
    """Feed bytes -> dated entries not older than fresh_days (undated ones are skipped: the day window needs a date)."""
    import feedparser
    parsed = feedparser.parse(body)
    out = []
    entries.total = len(parsed.entries)
    entries.feed_title = ((parsed.get("feed") or {}).get("title") or "").strip()[:300] or None
    entries.next_url = next_page(parsed, feed_url)
    for e in parsed.entries:
        link = urllib.parse.urljoin(feed_url, e.get("link") or "")
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        if not stamp:
            continue
        published = dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc)
        if published > now + dt.timedelta(days=1) or now - published > dt.timedelta(days=fresh_days):
            continue
        html = ((e.get("content") or [{}])[0].get("value")) or e.get("summary") or None
        out.append({"url": canonical(link), "link": e.get("link"), "guid": e.get("id"),
                    "title": (e.get("title") or "").strip()[:2000],
                    "published_at": published, "html": html})
        if len(out) >= per_feed:
            break
    return out


def next_page(parsed, feed_url):
    """The older page of a feed: rel="next" (RFC 5005 paging), else WordPress ?paged=2; None otherwise."""
    feed = parsed.get("feed") or {}
    for link in feed.get("links") or []:
        if link.get("rel") in ("next", "prev-archive") and link.get("href"):
            return urllib.parse.urljoin(feed_url, link["href"])
    if "wordpress" in (feed.get("generator") or "").lower() and "paged=" not in feed_url:
        return feed_url + ("&" if "?" in feed_url else "?") + "paged=2"
    return None


def capture(html, now):
    """The same content contract as Inoreader items (raw_data.content_capture)."""
    from collectors.inoreader.content_contract import extract_inoreader_content
    return extract_inoreader_content({"summary": {"content": html}} if html is not None else {}, captured_at=now)


class Fetcher:
    """One GET per feed: robots.txt per host, a pause per host, conditional GET, bounded size and time."""

    def __init__(self, opener=None, clock=time.monotonic, sleep=time.sleep, pause=HOST_PAUSE, relay=None):
        self.open = opener or urllib.request.urlopen
        self.clock, self.sleep, self.pause = clock, sleep, pause
        self.robots, self.last, self.requests = {}, {}, 0
        # A4SC-06 fallback (owner 21.11): a site that refuses GitHub's addresses (403) or cannot be reached from them
        # is read once more through the owner's Cloudflare Worker. Never for 429 (we wait) or for robots rules.
        env_relay = (os.environ.get("FEED_RELAY_URL"), os.environ.get("FEED_RELAY_TOKEN"))
        self.relay = relay if relay is not None else (env_relay if all(env_relay) else None)
        self.relayed = Counter()

    def _wait(self, host):
        gap = self.last.get(host, -1e9) + self.pause - self.clock()
        if gap > 0:
            self.sleep(gap)
        self.last[host] = self.clock()

    def _get(self, url, headers=None):
        host = urllib.parse.urlsplit(url).hostname
        self._wait(host)
        self.requests += 1
        req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
        try:
            result = self._read(req, TIMEOUT)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if not self.relay:
                raise
            via = self._via_relay(url, headers)
            if via is None:
                raise
            self.relayed[type(exc).__name__] += 1
            return via
        if self.relay and result[0] in RELAY_ON:
            via = self._via_relay(url, headers)
            if via is not None:
                self.relayed[f"http_{result[0]}"] += 1
                return via
        return result

    def _read(self, req, timeout):
        try:
            with self.open(req, timeout=timeout) as r:
                body = r.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    return 413, {}, b""
                return r.status, dict(r.headers), body
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), b""

    def _via_relay(self, url, headers=None):
        """The same GET through the Cloudflare Worker; None when the Worker itself fails (the direct result stands)."""
        base, token = self.relay
        fwd = {k: v for k, v in (headers or {}).items() if k in ("If-None-Match", "If-Modified-Since")}
        req = urllib.request.Request(base.rstrip("/") + "/fetch?url=" + urllib.parse.quote(url, safe=""),
                                     headers={"User-Agent": UA, "X-Relay-Token": token, **fwd})
        try:
            status, hdrs, body = self._read(req, RELAY_TIMEOUT)
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
        if not any(k.lower() == "x-relay" for k in hdrs):
            return None  # the Worker refused the call (token, address) or never answered: not the site's answer
        return status, hdrs, body

    def allowed(self, url):
        p = urllib.parse.urlsplit(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self.robots:
            status, _, body = self._get(root + "/robots.txt")
            if status == 200:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(body.decode("utf-8", "replace").splitlines())
                self.robots[root] = rp
            elif 400 <= status < 500:
                self.robots[root] = None          # no robots.txt: allowed
            else:
                self.robots[root] = False         # unreadable: not fetched this run
        rp = self.robots[root]
        return rp is None or (rp is not False and rp.can_fetch(UA, url))

    def feed(self, item):
        if not self.allowed(item["url"]):
            return None, {}, b""
        headers = {}
        if item.get("etag"):
            headers["If-None-Match"] = item["etag"]
        if item.get("last_modified"):
            headers["If-Modified-Since"] = item["last_modified"]
        return self._get(item["url"], headers)


def poll(feeds, fetcher, now, max_requests=600, transcriber=None, done=frozenset()):
    """Feeds -> (poll rows, shadow rows). A failing feed is recorded and the rest go on. With a transcriber
    (A5TC-04), podcast episodes not yet in the base get their text from the publisher's transcript or Whisper."""
    polls, shadow = [], []
    for f in feeds:
        if fetcher.requests >= max_requests:
            break
        row = {"channel_id": f["channel_id"], "http_status": None, "etag": None, "last_modified": None,
               "entries": 0, "fresh": 0, "error": None}
        try:
            status, headers, body = fetcher.feed(f)
            row["http_status"] = status
            low = {k.lower(): v for k, v in headers.items()}
            row["etag"], row["last_modified"] = low.get("etag"), low.get("last-modified")
            if status is None:
                row["error"] = "robots"
            elif status == 200:
                found = entries(body, f["url"], now)
                total, feed_title, nxt = entries.total, entries.feed_title, entries.next_url
                # every entry of the feed is fresh: older ones may have dropped off between two polls -> one more page
                if nxt and total >= 10 and len(found) == total and fetcher.requests < max_requests and fetcher.allowed(nxt):
                    ps, _, pbody = fetcher._get(nxt)
                    if ps == 200 and pbody:
                        have = {e["url"] for e in found}
                        more = [e for e in entries(pbody, nxt, now) if e["url"] not in have]
                        found += more
                        row["paged"] = len(more)
                row["entries"], row["fresh"] = total, len(found)
                eps = podcast.episodes(body) if transcriber is not None else {}
                for e in found:
                    eid = external_id(e["url"], e["guid"])
                    if not eid:
                        continue
                    cap, source = capture(e["html"], now), None
                    info = eps.get((e.get("link") or "").strip()) or eps.get((e.get("guid") or "").strip())
                    if info and eid not in done:
                        text, source = transcriber.text(info)
                        if text:
                            cap = podcast.as_capture(text, now)
                    shadow.append({"channel_id": f["channel_id"], "external_id": eid, "url": e["url"],
                                   "title": e["title"], "published_at": e["published_at"],
                                   "text_chars": cap.get("text_characters") or 0, "capture_status": cap.get("status"),
                                   "feed_url": f["url"], "origin_title": feed_title, "capture": cap,
                                   "transcript": source})
            elif status != 304:
                row["error"] = f"http_{status}"
        except Exception as exc:  # noqa: BLE001 - one feed must not stop the run; only the error kind is kept
            row["error"] = type(exc).__name__
        polls.append(row)
    return polls, shadow


def save_shadow(pg, run_id, polls, shadow):
    """Mark entries whose url is already in intelligence.items (read), then write the two shadow tables."""
    cur = pg.cursor()
    urls = sorted({s["url"] for s in shadow if s["url"]})
    known = set()
    if urls:
        variants = urls + [u.rstrip("/") for u in urls] + [u + "/" for u in urls if not u.endswith("/")]
        cur.execute("select url from intelligence.items where url = any(%s)", (variants,))
        known = {canonical(u) for (u,) in cur.fetchall()}
    new_by_channel = Counter()
    with pg.transaction():
        for s in shadow:
            cur.execute("insert into source_discovery.feed_items_shadow(run_id, channel_id, external_id, url, title, "
                        "published_at, text_chars, capture_status, already_in_items) values "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (channel_id, external_id) do nothing",
                        (run_id, s["channel_id"], s["external_id"], s["url"], s["title"], s["published_at"],
                         s["text_chars"], s["capture_status"], s["url"] in known))
            new_by_channel[s["channel_id"]] += cur.rowcount
        for p in polls:
            cur.execute("insert into source_discovery.feed_poll(run_id, channel_id, http_status, etag, last_modified, "
                        "entries, fresh, new_items, error) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (run_id, p["channel_id"], p["http_status"], p["etag"], p["last_modified"], p["entries"],
                         p["fresh"], new_by_channel[p["channel_id"]], p["error"]))
    return {"new_shadow_items": sum(new_by_channel.values()), "already_in_items": sum(s["url"] in known for s in shadow),
            "_known": known}


def write_items(pg, run_id, shadow, known):
    """Stage 2: entries whose url is not yet in intelligence.items -> intelligence.items (one source row)."""
    from psycopg.types.json import Jsonb
    cur = pg.cursor()
    inserted = 0
    with pg.transaction():
        cur.execute("select id from intelligence.sources where name = %s order by id limit 1", (SOURCE_NAME,))
        row = cur.fetchone()
        if row:
            source_id = row[0]
        else:
            cur.execute("insert into intelligence.sources (name, url, source_type, collection_method) "
                        "values (%s, null, 'news', 'rss') returning id", (SOURCE_NAME,))
            source_id = cur.fetchone()[0]
        for s in shadow:
            if not s["url"] or s["url"] in known or s.get("skip_write"):
                continue
            host = urllib.parse.urlsplit(s["url"]).hostname
            raw = {"collector": "a4sc-feeds-v1", "collector_cycle_id": run_id, "channel_id": s["channel_id"],
                   "feed_url": s.get("feed_url"), "host": host, "origin_title": s.get("origin_title"),
                   "url": s["url"], "title": s["title"], "published": s["published_at"].isoformat(),
                   "content_capture": s.get("capture"),
                   **({"transcript": s["transcript"]} if s.get("transcript") else {}), **s.get("raw_extra", {})}
            cur.execute("insert into intelligence.items (source_id, external_id, title, url, item_type, published_at,"
                        " raw_data) values (%s,%s,%s,%s,'news',%s,%s) on conflict (source_id, external_id) do nothing"
                        " returning id", (source_id, s["external_id"], s["title"] or "(untitled)", s["url"],
                                          s["published_at"], Jsonb(raw)))
            inserted += cur.fetchone() is not None
    return {"items_inserted": inserted}


def summary(polls, shadow, saved, fetcher):
    st = Counter("ok" if p["http_status"] == 200 else "not_modified" if p["http_status"] == 304
                 else p["error"] or "other" for p in polls)
    return {"feeds": len(polls), "requests": fetcher.requests, "status": dict(st),
            "feeds_with_fresh": sum(p["fresh"] > 0 for p in polls), "fresh_entries": len(shadow),
            "with_text": sum(s["text_chars"] >= 400 for s in shadow), **saved}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["shadow", "write"], default="shadow")
    ap.add_argument("--max-feeds", type=int, default=200)
    ap.add_argument("--youtube-discover", action="store_true", help="run YouTube channel discovery now")
    ap.add_argument("--busy", action="store_true", help="30-minute run: only the busy feeds, no YouTube or pages")
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source_registry", "search"))
    import pg_sync
    now = dt.datetime.now(dt.timezone.utc)
    run_id = "feeds-" + os.environ.get("GITHUB_RUN_ID", "local") + "-" + now.strftime("%Y%m%dT%H%M")
    with pg_sync.connect() as pg:
        cur = pg.cursor()
        cur.execute(FEEDS_SQL)
        feeds = eligible(cur.fetchall(), a.max_feeds)
        busy = None
        if a.busy:
            cur.execute(BUSY_SQL, (BUSY_PER_WEEK,))
            busy = {r[0] for r in cur.fetchall()}
            feeds = [f for f in feeds if f["channel_id"] in busy]
        fetcher = Fetcher()
        transcriber, done = None, frozenset()
        if a.mode == "write":
            transcriber = podcast.Transcriber(fetcher, key=os.environ.get("GROQ_API_KEY") or None)
            cur.execute("select i.external_id from intelligence.items i join intelligence.sources s on s.id = i.source_id "
                        "where s.name = %s and i.discovered_at > now() - interval '8 days'", (SOURCE_NAME,))
            done = frozenset(r[0] for r in cur.fetchall())
        polls, shadow = poll(feeds, fetcher, now, transcriber=transcriber, done=done)
        yt_polls, yt_shadow, yt = [], [], None
        if os.environ.get("YOUTUBE_API_KEY") and not a.busy:  # A4SC-02 / A5TC-03: Data API only, descriptions
            yt_polls, yt_shadow, yt = youtube.run(pg, now, run_id, os.environ["YOUTUBE_API_KEY"], a.mode == "write",
                                                  a.youtube_discover or now.hour == youtube.DISCOVER_HOUR, SOURCE_NAME)
        pg_polls, pg_shadow, pg_counts = ([], [], None) if a.busy else pages.run(  # A4SC-03: own write switch
            pg, fetcher, now, write=a.mode == "write" and os.environ.get("PAGES_WRITE") == "true")
        cmp_polls, cmp_shadow = compare_feeds(cur, fetcher, now, only=busy)
        saved = save_shadow(pg, run_id, polls + yt_polls + pg_polls + cmp_polls,
                            shadow + yt_shadow + pg_shadow + cmp_shadow)
        known = saved.pop("_known")
        if a.mode == "write":
            saved.update(write_items(pg, run_id, shadow + yt_shadow + pg_shadow, known))
    print(json.dumps({"run": run_id, "mode": a.mode, "busy": a.busy, "relayed": dict(fetcher.relayed),
                      **summary(polls, shadow, saved, fetcher),
                      **({"podcasts": transcriber.counts} if transcriber else {}),
                      **({"youtube": yt} if yt is not None else {}), "pages": pg_counts,
                      "inoreader_compare": {"feeds": len(cmp_polls), "entries": len(cmp_shadow),
                                            "errors": dict(Counter(p["error"] for p in cmp_polls if p["error"]))}}))


if __name__ == "__main__":
    main()
