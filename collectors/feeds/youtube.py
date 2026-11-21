"""A4SC-02 / A5TC-03 YouTube channels through the YouTube Data API v3 only (D-20261120-PODCASTS-YOUTUBE).

YouTube's robots.txt closes /feeds/videos.xml to robots and its developer policies forbid downloading audiovisual
content, so the only path is the Data API with the owner's key (YOUTUBE_API_KEY, free 10 000 units a day):
- discovery (once a day): search.list type=channel for category x country cells (100 units each), then
  channels.list + playlistItems.list (1 unit each); a channel needs fresh uploads and the A1SD judge's "specialised"
  verdict on its recent video titles; the decision goes to source_discovery like any other source;
- collection (every run): the uploads playlist of each accepted channel (1 unit): title, description, date, link.
  The text of a video is its description only: no subtitles, no audio;
- the 30-day rule of the API policies: stored video data older than 25 days is refreshed by videos.list
  (1 unit per 50) or, when the video is gone, cleared.
The key travels only in the request URL and is never logged: errors keep the HTTP code and reason only.
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from collectors.feeds import podcast

API = "https://www.googleapis.com/youtube/v3/"
UA = "BOHUN-FeedCollector/1.0 (+https://github.com/nab0k/bohun-the-field)"
COLLECTOR = "a5tc-youtube-v1"
RULES = "a5tc-youtube-0.2"   # 0.2 (21.11): the channel prompt; 0.1 took any war channel, clickbait included
CHANNEL_PROMPT = """You classify a YouTube channel as a source for a defence-industry news monitor (Europe, Ukraine, NATO).
Channel: {host}
Channel name and description: {name}
Its latest video titles and links (up to 40):
{lines}

Answer with one JSON object only:
{{"specialised": true|false,
  "type": one of {types},
  "defence_section": null,
  "confidence": number 0..1}}
specialised = true ONLY if most videos are news, analysis, explainers or official information about the defence
industry: companies and their products, military technology and equipment, procurement and contracts, exports,
defence budgets and policy, research - or the channel is the official channel of a defence company, ministry,
agency, armed service or trade event.
specialised = false for: combat or frontline footage, soldier vlogs, unit or motivational videos, clickbait or
sensational titles ("then THIS happened", "the brutal reality", "what happened next"), channels of mostly #shorts,
games, entertainment, general news or politics without industry content, and re-uploads of other channels."""
DISCOVER_HOUR = int(os.environ.get("YOUTUBE_DISCOVER_HOUR", "4"))   # the 04:25 UTC run of the relay
DISCOVER_QUERIES = int(os.environ.get("YOUTUBE_DISCOVER_QUERIES", "25"))   # x 100 units
MAX_CHANNELS = int(os.environ.get("YOUTUBE_MAX_CHANNELS", "300"))   # x 1 unit x 12 runs a day
MIN_RECENT = 3          # uploads in the last ACTIVE_DAYS for a channel to count as a news channel
ACTIVE_DAYS = 90
REFRESH_DAYS = 25
GONE = {"Private video", "Deleted video"}
START = dt.date(2026, 11, 21)   # day 0 of the discovery rotation: the core countries first


class QuotaOut(Exception):
    """The daily quota is spent or the key is refused: stop all YouTube calls for this run."""


class Client:
    def __init__(self, key, opener=urllib.request.urlopen):
        self.key, self.open = key, opener
        self.units, self.calls, self.errors = 0, 0, {}

    def get(self, endpoint, cost=1, **params):
        q = urllib.parse.urlencode({**params, "key": self.key})
        req = urllib.request.Request(API + endpoint + "?" + q, headers={"User-Agent": UA, "Accept": "application/json"})
        self.calls += 1
        self.units += cost
        try:
            with self.open(req, timeout=30) as r:
                return json.loads(r.read(5_000_000).decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            reason = ""
            try:
                reason = json.loads(exc.read().decode())["error"]["errors"][0]["reason"]
            except Exception:  # noqa: BLE001 - only the reason code is kept
                pass
            kind = f"{endpoint}:{exc.code}:{reason}"
            self.errors[kind] = self.errors.get(kind, 0) + 1
            if exc.code in (401, 403) and reason not in ("playlistNotFound", "forbidden"):
                raise QuotaOut(kind) from None
            return None


def channel_url(ucid):
    return "https://www.youtube.com/channel/" + ucid


def ucid_of(url):
    path = urllib.parse.urlsplit(url or "").path.rstrip("/")
    return path.rsplit("/", 1)[-1] if "/channel/UC" in path else None


def video_url(vid):
    return "https://www.youtube.com/watch?v=" + vid


def when(text):
    try:
        return dt.datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def uploads(client, ucid, n=10):
    """Latest uploads of a channel: [{"id", "title", "description", "published_at"}], newest first."""
    data = client.get("playlistItems", part="snippet,contentDetails", playlistId="UU" + ucid[2:], maxResults=n)
    out = []
    for it in (data or {}).get("items", []):
        sn, cd = it.get("snippet") or {}, it.get("contentDetails") or {}
        vid = cd.get("videoId") or (sn.get("resourceId") or {}).get("videoId")
        published = when(cd.get("videoPublishedAt") or sn.get("publishedAt"))
        if vid and published and sn.get("title") not in GONE:
            out.append({"id": vid, "title": (sn.get("title") or "").strip()[:2000],
                        "description": (sn.get("description") or "").strip(), "published_at": published})
    return sorted(out, key=lambda v: v["published_at"], reverse=True)


def cells(cat, day, n):
    """Category x country cells for today's discovery, rotating over all of them day by day. Countries keep the
    dictionary order (the core countries first, then Europe, then the world); inside a country the categories with
    the most events in our base come first."""
    topics = sorted(cat["categories"], key=lambda c: (-(cat["categories"][c].get("base_events") or 0), c))
    all_cells = [(cc, cid) for cc in cat["countries"] for cid in topics]
    if not all_cells:
        return []
    start = (day * n) % len(all_cells)
    picked = (all_cells + all_cells)[start:start + n]
    out = []
    for cc, cid in picked:
        c = cat["countries"][cc]
        lang = c.get("lang", "en")
        term = cat["categories"][cid]["terms"].get(lang) or cat["categories"][cid]["terms"]["en"]
        out.append({"country": cc, "lang": lang, "category": cid, "q": term,
                    "type": cat["categories"][cid].get("default_type", "media")})
    return out


def assess(client, judge, ucid, now):
    """One channel -> (decision, status, record). Needs fresh uploads, then the judge's verdict on their titles."""
    info = (client.get("channels", part="snippet", id=ucid) or {}).get("items") or [{}]
    sn = info[0].get("snippet") or {}
    vids = uploads(client, ucid, n=20)
    recent = [v for v in vids if now - v["published_at"] <= dt.timedelta(days=ACTIVE_DAYS)]
    # API data (titles, descriptions) is not kept here: only our own counts and the verdict (30-day rule)
    record = {"channel": ucid, "country": sn.get("country"), "recent_uploads": len(recent)}
    if len(recent) < MIN_RECENT:
        return "reject", "no_news", record
    if not judge:
        return "hold", "on_hold", {**record, "why": "no_judge"}
    name = (sn.get("title") or "") + " - " + (sn.get("description") or "")[:300]
    verdict = judge("youtube.com/" + (sn.get("customUrl") or ucid), name,
                    [(video_url(v["id"]), v["title"]) for v in vids], prompt=CHANNEL_PROMPT)
    if verdict is None:
        return "hold", "on_hold", {**record, "why": "judge_no_answer"}
    record["judge"] = verdict
    if verdict["specialised"]:
        return "accept", "accepted", record
    return "reject", "rejected", record


def discover(client, judge, cat, known, now, n=DISCOVER_QUERIES, recheck=None):
    """Today's cells -> decisions for channels not seen before, plus `recheck` ({ucid: cell}: channels on hold or
    decided under older rules): [(ucid, cell, decision, status, record)]."""
    found = {}
    for cell in cells(cat, (now.date() - START).days, n):
        data = client.get("search", cost=100, part="snippet", type="channel", q=cell["q"], maxResults=10,
                          regionCode=cell["country"], relevanceLanguage=cell["lang"])
        for it in (data or {}).get("items", []):
            ucid = (it.get("id") or {}).get("channelId") or (it.get("snippet") or {}).get("channelId")
            if ucid and ucid.startswith("UC") and ucid not in known and ucid not in found:
                found[ucid] = cell
    for ucid, cell in (recheck or {}).items():
        found.setdefault(ucid, cell)
    out = []
    for ucid, cell in found.items():
        decision, status, record = assess(client, judge, ucid, now)
        out.append((ucid, cell, decision, status, {**record, "query": cell["q"]}))
    return out


def save_decisions(pg, run_id, decisions, now):
    """Decisions -> source_discovery (the only schema this step writes). Returns counts by status."""
    from psycopg.types.json import Jsonb
    from registry import channel_id_for
    counts = {}
    cur = pg.cursor()
    with pg.transaction():
        for ucid, cell, decision, status, record in decisions:
            url = channel_url(ucid)
            cid = channel_id_for(url)
            found_via = {"layer": "youtube", "country": cell["country"], "category": cell["category"]}
            cur.execute("insert into source_discovery.admission_decision(candidate_url, channel_id, decision, reasons,"
                        " record, rules_version, decided_at, found_via, run_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (url, cid, decision, Jsonb([record.get("why") or status]), Jsonb(record), RULES, now,
                         Jsonb(found_via), run_id))
            vtype = (record.get("judge") or {}).get("type") or cell["type"]
            cur.execute("insert into source_discovery.source_state values (%s,%s,%s,%s,'youtube',%s,%s,%s,%s) "
                        "on conflict (channel_id) do update set status=excluded.status, since=excluded.since, "
                        "rules_version=excluded.rules_version, type=excluded.type "
                        "where source_discovery.source_state.status in ('candidate', 'on_hold') "
                        "or source_discovery.source_state.rules_version is distinct from excluded.rules_version",
                        (cid, url, status, vtype, cell["country"], cell["lang"], now, RULES))
            counts[status] = counts.get(status, 0) + 1
    return counts


def collect(client, channels, now, fresh_days=7):
    """Accepted channels -> (poll rows, shadow rows) in the feed collector's shape; the description is the text."""
    from collectors.feeds.collect import external_id
    polls, shadow = [], []
    for ch in channels[:MAX_CHANNELS]:
        ucid = ucid_of(ch["url"])
        row = {"channel_id": ch["channel_id"], "http_status": None, "etag": None, "last_modified": None,
               "entries": 0, "fresh": 0, "error": None}
        polls.append(row)
        if not ucid:
            row["error"] = "not_a_channel"
            continue
        vids = uploads(client, ucid)
        row["http_status"], row["entries"] = 200, len(vids)
        for v in vids:
            if now - v["published_at"] > dt.timedelta(days=fresh_days) or v["published_at"] > now + dt.timedelta(days=1):
                continue
            row["fresh"] += 1
            url = video_url(v["id"])
            cap = podcast.as_capture(v["description"], now)
            shadow.append({"channel_id": ch["channel_id"], "external_id": external_id(url, None), "url": url,
                           "title": v["title"], "published_at": v["published_at"],
                           "text_chars": cap.get("text_characters") or 0, "capture_status": cap.get("status"),
                           "feed_url": ch["url"], "origin_title": None, "capture": cap, "transcript": None,
                           "raw_extra": {"collector": COLLECTOR,
                                         "youtube": {"video_id": v["id"], "refreshed_at": now.isoformat()}}})
    return polls, shadow


REFRESH_SQL = """
select i.id, i.raw_data->'youtube'->>'video_id' from intelligence.items i
join intelligence.sources s on s.id = i.source_id
where s.name = %s and i.raw_data->>'collector' = %s and not coalesce((i.raw_data->'youtube'->>'removed')::boolean, false)
  and coalesce((i.raw_data->'youtube'->>'refreshed_at')::timestamptz, i.discovered_at) < %s
order by i.id limit 500
"""


def refresh(pg, client, source_name, now):
    """API policy: stored video data is refreshed within 30 days, or cleared when the video is gone."""
    from psycopg.types.json import Jsonb
    cur = pg.cursor()
    cur.execute(REFRESH_SQL, (source_name, COLLECTOR, now - dt.timedelta(days=REFRESH_DAYS)))
    rows = [(i, v) for i, v in cur.fetchall() if v]
    counts = {"refreshed": 0, "cleared": 0}
    for k in range(0, len(rows), 50):
        batch = rows[k:k + 50]
        data = client.get("videos", part="snippet", id=",".join(v for _, v in batch))
        if data is None:
            continue
        live = {it["id"]: it.get("snippet") or {} for it in data.get("items", [])}
        with pg.transaction():
            for item_id, vid in batch:
                sn = live.get(vid)
                yt = {"video_id": vid, "refreshed_at": now.isoformat()}
                if sn:
                    cap = podcast.as_capture((sn.get("description") or "").strip(), now)
                    cur.execute("update intelligence.items set title = %s, raw_data = raw_data || %s where id = %s",
                                ((sn.get("title") or "(untitled)")[:2000],
                                 Jsonb({"content_capture": cap, "title": sn.get("title"), "youtube": yt}), item_id))
                    counts["refreshed"] += 1
                else:
                    cur.execute("update intelligence.items set title = '(video removed)', raw_data = raw_data || %s "
                                "where id = %s", (Jsonb({"content_capture": None, "title": None,
                                                         "youtube": {**yt, "removed": True}}), item_id))
                    counts["cleared"] += 1
    return counts


STATE_SQL = """
select s.canonical_url, s.status, s.rules_version, s.country, s.language, s.type,
       (select found_via from source_discovery.admission_decision a where a.channel_id = s.channel_id
        order by decision_id desc limit 1)
from source_discovery.source_state s where s.delivery = 'youtube'
"""
RECHECK_MAX = 60


def prune_shadow(pg, now):
    """The 30-day rule also covers the shadow table: YouTube titles there are deleted after REFRESH_DAYS."""
    cur = pg.cursor()
    with pg.transaction():
        cur.execute("delete from source_discovery.feed_items_shadow where seen_at < %s and channel_id in "
                    "(select channel_id from source_discovery.source_state where delivery = 'youtube')",
                    (now - dt.timedelta(days=REFRESH_DAYS),))
    return max(cur.rowcount or 0, 0)


CHANNELS_SQL = ("select channel_id, canonical_url from source_discovery.source_state "
                "where status = 'accepted' and delivery = 'youtube' order by since")


def run(pg, now, run_id, key, write, discover_now, source_name, judge=None, client=None, cat=None):
    """One collector run: discovery (once a day), collection, and in write mode the 30-day refresh.
    Returns (poll rows, shadow rows, counts). A spent quota stops YouTube only; feeds go on."""
    client = client or Client(key)
    cur = pg.cursor()
    polls, shadow, counts = [], [], {}
    try:
        if discover_now:
            cur.execute(STATE_SQL)
            rows = cur.fetchall()
            known = {ucid_of(u) for u, *_ in rows} - {None}
            recheck = {ucid_of(u): {"country": cc, "lang": lang or "en", "category": (fv or {}).get("category"),
                                    "type": t or "media", "q": None}
                       for u, status, rules, cc, lang, t, fv in rows
                       if ucid_of(u) and (status == "on_hold" or (status == "accepted" and rules != RULES))}
            recheck = dict(list(recheck.items())[:RECHECK_MAX])
            if judge is None or cat is None:
                import judge as J
                import plan as P
                judge = J.Judge() if judge is None else judge
                cat = P.with_europe(P.load(), P.world()) if cat is None else cat
            counts["rechecked"] = len(recheck)
            counts["discovered"] = save_decisions(pg, run_id, discover(client, judge, cat, known, now,
                                                                       recheck=recheck), now)
        cur.execute(CHANNELS_SQL)
        channels = [{"channel_id": c, "url": u} for c, u in cur.fetchall()]
        polls, shadow = collect(client, channels, now)
        if write:
            counts["refresh"] = refresh(pg, client, source_name, now)
        counts["shadow_pruned"] = prune_shadow(pg, now)
    except QuotaOut as exc:
        counts["stopped"] = str(exc)
    counts.update(channels=len(polls), videos=len(shadow), units=client.units, errors=client.errors)
    return polls, shadow, counts
