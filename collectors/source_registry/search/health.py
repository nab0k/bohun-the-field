"""A6SH-02 health and usefulness of the sources found by A1SD (owner 20.11: design and code at once).

Once a day, before the search run: for every accepted feed source
- polls over 7 days, the streak of failed polls and when it last brought something new (source_discovery.feed_poll);
- its articles over 30 days and how many of them reached an event (intelligence.items of "BOHUN feeds (A1SD)" by
  raw_data.channel_id, knowledge.event_document) -> usefulness = in_events / items;
- status: new (under 3 days or 3 polls), failing (3 failed polls in a row), silent (nothing new for 7 days),
  useless (20+ articles, none in an event), healthy.
Failing and silent sources leave the accepted list for a re-check (on_hold, the normal 14-day recheck through the
judge brings back a live one). Useless is only marked: events are built later than articles, so it informs the search
(usefulness per layer feeds the learning) and is not acted on. Shadow schema only; product tables are only read.
"""
import datetime as dt
import json
from collections import defaultdict
from eligibility import can_poll

RULES = "a6sh-0.2"
NEW_DAYS, MIN_POLLS, FAIL_STREAK, SILENT_DAYS, USELESS_ITEMS = 3, 3, 3, 7, 20
POLL_WINDOW = dt.timedelta(hours=2)

CHANNELS_SQL = """select s.channel_id, s.canonical_url, s.since, d.decision_id,
                        d.rules_version, case when d.decision='accept' then d.record->'judge' end,
                        case when d.decision='accept' then d.reasons end
                  from source_discovery.source_state s
                  left join lateral (select decision_id, decision, rules_version, record, reasons
                    from source_discovery.admission_decision a where a.channel_id=s.channel_id
                    order by decision_id desc limit 1) d on true
                  where s.status = 'accepted' and s.delivery = 'feed'"""
POLLS_SQL = """select channel_id, polled_at, http_status, error, new_items, run_id from source_discovery.feed_poll
               where polled_at > %s order by channel_id, polled_at desc"""
ITEMS_SQL = """select i.raw_data->>'channel_id', count(distinct i.id), count(distinct ed.item_id)
               from intelligence.items i join intelligence.sources s on s.id = i.source_id
               left join knowledge.event_document ed on ed.item_id = i.id
               where s.name = 'BOHUN feeds (A1SD)' and i.discovered_at > %s group by 1"""


def assess(channels, polls, items, now, eligible_ids):
    """Assess channel observations with same-run successes from other eligible channels as evidence."""
    by = defaultdict(list)
    successful_runs = defaultdict(set)
    successful_windows = set()
    for p in polls:
        by[p[0]].append(p)
        channel, at, status, error, _, run_id = p
        if channel in eligible_ids and error is None and status in (200, 304):
            successful_runs[run_id].add(channel)
            successful_windows.add(int(at.timestamp() // POLL_WINDOW.total_seconds()))
    out = []
    for channel, url, since in channels:
        ps = by.get(channel, [])
        streak = 0
        failed_runs = set()
        for _, _, status, error, _, run_id in ps:
            if error is None and status in (200, 304):
                break
            failed_runs.add(run_id)
        streak = len(failed_runs)
        new_at = [p[1] for p in ps if (p[4] or 0) > 0]
        last_new = max(new_at) if new_at else None
        n_items, in_events = items.get(channel, (0, 0))
        usefulness = round(in_events / n_items, 3) if n_items >= 5 else None
        age = now - since
        reason = None
        if channel not in eligible_ids:
            status, reason = "new", "not_eligible"
        elif not ps:
            first_window = int(since.timestamp() // POLL_WINDOW.total_seconds())
            windows = sum(window > first_window for window in successful_windows)
            status = "new"
            reason = "first_poll_overdue" if age >= 2 * POLL_WINDOW and windows >= 2 else "first_poll_pending"
        elif age < dt.timedelta(days=NEW_DAYS) or len(ps) < MIN_POLLS:
            status = "new"
        elif streak >= FAIL_STREAK:
            status = "failing"
            if not all(successful_runs[run] - {channel} for run in failed_runs):
                reason = "transport_unconfirmed"
        elif (age >= dt.timedelta(days=SILENT_DAYS) and (last_new is None or now - last_new > dt.timedelta(days=SILENT_DAYS))
              and ps[0][3] is None and ps[0][2] in (200, 304) and now - ps[0][1] <= 2 * POLL_WINDOW):
            status = "silent"
        elif n_items >= USELESS_ITEMS and in_events == 0:
            status = "useless"
        else:
            status = "healthy"
        out.append({"channel_id": channel, "url": url, "status": status, "polls_7d": len(ps), "errors_streak": streak,
                    "last_new_at": last_new, "items_30d": n_items, "in_events_30d": in_events,
                    "usefulness": usefulness, "reason": reason,
                    "action": "recheck" if not reason and status in ("failing", "silent") else None})
    return out


def update(pg, now=None, diagnostics=None):
    """Assess, store source_health, and move failing/silent sources to a re-check. Returns counts (public log)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cur = pg.cursor()
    cur.execute(CHANNELS_SQL)
    channel_rows = cur.fetchall()
    channels = [(channel, url, since) for channel, url, since, *_ in channel_rows]
    eligible_ids = {channel for channel, url, _, _, rules, judged, reasons in channel_rows
                    if can_poll(url, rules, judged, reasons)}
    snapshots = {channel: (since, decision_id) for channel, _, since, decision_id, *_ in channel_rows}
    cur.execute(POLLS_SQL, (now - dt.timedelta(days=7),))
    polls = cur.fetchall()
    cur.execute(ITEMS_SQL, (now - dt.timedelta(days=30),))
    items = {c: (n, e) for c, n, e in cur.fetchall() if c}
    rows = assess(channels, polls, items, now, eligible_ids)
    if diagnostics is not None:
        diagnostics.update({r["channel_id"]: r["reason"] for r in rows if r["reason"]})
    with pg.transaction():
        for r in rows:
            cur.execute("insert into source_discovery.source_health (channel_id, updated_at, status, polls_7d, errors_streak,"
                        " last_new_at, items_30d, in_events_30d, usefulness, action) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                        " on conflict (channel_id) do update set updated_at=excluded.updated_at, status=excluded.status,"
                        " polls_7d=excluded.polls_7d, errors_streak=excluded.errors_streak, last_new_at=excluded.last_new_at,"
                        " items_30d=excluded.items_30d, in_events_30d=excluded.in_events_30d,"
                        " usefulness=excluded.usefulness, action=excluded.action",
                        (r["channel_id"], now, r["status"], r["polls_7d"], r["errors_streak"], r["last_new_at"],
                         r["items_30d"], r["in_events_30d"], r["usefulness"], r["action"]))
            if r["action"] != "recheck":
                continue
            url = r["url"]
            reason = "health_" + r["status"]
            cur.execute("update source_discovery.source_state set status='on_hold', since=%s, rules_version=%s "
                        "where channel_id=%s and status='accepted' and since=%s and "
                        "(select max(decision_id) from source_discovery.admission_decision "
                        "where channel_id=%s) is not distinct from %s",
                        (now, RULES, r["channel_id"], snapshots[r["channel_id"]][0],
                         r["channel_id"], snapshots[r["channel_id"]][1]))
            if cur.rowcount != 1:
                continue
            cur.execute("insert into source_discovery.admission_decision(candidate_url, channel_id, decision, reasons,"
                        " record, rules_version, decided_at, found_via, run_id) values (%s,%s,'hold',%s,%s,%s,%s,%s,%s)",
                        (url, r["channel_id"], json.dumps([reason]), json.dumps({"decision": "hold", "health": {
                            k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in r.items() if k != "url"}}),
                         RULES, now, json.dumps({"layer": "health"}), "health-" + now.strftime("%Y%m%dT%H%M")))
            cur.execute("insert into source_discovery.candidate_seen values (%s,'hold',%s,0,%s) on conflict (candidate_url)"
                        " do update set last_decision='hold', decided_at=excluded.decided_at, channel_id=excluded.channel_id",
                        (url, now, r["channel_id"]))
    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1
        if r["reason"]:
            counts[r["reason"]] += 1
    return {"sources": len(rows), **counts}


def useful_by_layer(pg):
    """Per search layer: accepted sources with 5+ articles and how many of them reached events (for the learning)."""
    cur = pg.cursor()
    cur.execute("""select d.found_via->>'layer', count(*), count(*) filter (where h.in_events_30d > 0)
                   from source_discovery.source_health h
                   join lateral (select found_via from source_discovery.admission_decision a
                                 where a.channel_id = h.channel_id
                                   and a.found_via->>'layer' not in ('health', 'audit', 'recheck')
                                 order by decision_id limit 1) d on true
                   where h.items_30d >= 5 group by 1""")
    return {layer: (n, useful) for layer, n, useful in cur.fetchall() if layer}
