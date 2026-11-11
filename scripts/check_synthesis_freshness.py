#!/usr/bin/env python3
"""C3-02: read-only check that BOHUN's public synthesis feed still gains new material.

Exit: 0 fresh, 1 stale, 2 unavailable/invalid. No database access; GH_TOKEN is optional and only raises API limits.
Status comes from the hours since the feed's git history last gained a new item id. The feed's item.date is the
news date, not the publication time: new items can carry an old date, so it is reported but never decides the status.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FEED_URL = "https://bohundefence.com/feed/synthesis-feed.json"
HISTORY_URL = "https://api.github.com/repos/nab0k/bohundefense/commits?path=feed/synthesis-feed.json&per_page=20"
RAW_URL = "https://raw.githubusercontent.com/nab0k/bohundefense/{sha}/feed/synthesis-feed.json"
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def parse_now(value: str) -> datetime:
    """An explicit test clock must include its timezone."""
    raw = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError("now must include UTC offset")
    return dt.astimezone(timezone.utc)


def parse_feed_date(value: object) -> date:
    """Real feed accepts YYYY-MM-DD or offset-aware ISO datetimes.

    A date-only value is a calendar date, NOT midnight UTC for hourly aging.
    A datetime without timezone is ambiguous and must fail closed.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing or non-string item.date")
    raw = value.strip()
    if ISO_DATE.fullmatch(raw):
        return date.fromisoformat(raw)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("datetime item.date must include UTC offset")
    return dt.astimezone(timezone.utc).date()


def feed_items(document: object) -> list[dict]:
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise ValueError("unexpected feed schema: require object with items array")
    if any(not isinstance(item, dict) for item in document["items"]):
        raise ValueError("feed includes non-object items")
    return document["items"]


def evaluate(document: object, now: datetime, max_age_days: int = 1) -> dict:
    """Calendar UTC days only. generated_at is never evidence of publication."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    if max_age_days < 0:
        raise ValueError("max_age_days must be nonnegative")
    rows = feed_items(document)
    if not rows:
        return {"status": "stale", "count": 0, "age_days": None,
                "latest_item_date": None, "reason": "feed_empty"}
    dates = []
    for row in rows:
        if "date" not in row:
            raise ValueError("item lacks date")
        dates.append(parse_feed_date(row["date"]))
    newest = max(dates)
    elapsed_days = (now.astimezone(timezone.utc).date() - newest).days
    if elapsed_days < 0:
        raise ValueError("latest item date is in the future UTC")
    status = "stale" if elapsed_days > max_age_days else "fresh"
    return {"status": status, "count": len(rows), "age_days": elapsed_days,
            "latest_item_date": newest.isoformat(),
            "reason": "older_than_calendar_threshold" if status == "stale" else "ok"}


def last_growth(commits: list[dict], load_ids) -> datetime | None:
    """Time of the newest feed commit that added an item id its previous version did not have."""
    if len(commits) < 2:
        raise ValueError("feed history is too short to compare versions")
    newer = load_ids(commits[0]["sha"])
    for index, commit in enumerate(commits[1:]):
        older = load_ids(commit["sha"])
        if newer - older:
            stamp = commits[index]["commit"]["committer"]["date"]
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        newer = older
    return None


def fetch_growth(timeout: float) -> datetime | None:
    def load_ids(sha: str) -> set:
        return {item["id"] for item in feed_items(fetch_json(RAW_URL.format(sha=sha), timeout, plain_ok=True))}
    return last_growth(fetch_json(HISTORY_URL, timeout), load_ids)


def apply_growth(result: dict, grown_at: datetime | None, now: datetime, max_hours: float) -> dict:
    """The status decision: hours since new material, not the news date."""
    if result.get("count") == 0:
        return {**result, "status": "stale", "hours_since_new": None, "reason": "feed_empty"}
    if grown_at is None:
        return {**result, "status": "stale", "hours_since_new": None, "reason": "no_growth_in_checked_history"}
    hours = (now - grown_at).total_seconds() / 3600
    if hours < 0:
        raise ValueError("feed history is in the future")
    stale = hours > max_hours
    return {**result, "status": "stale" if stale else "fresh", "hours_since_new": round(hours, 1),
            "last_new_at": grown_at.isoformat(timespec="minutes"),
            "reason": "no_new_material_within_limit" if stale else "ok"}


def fetch_json(url: str, timeout: float, plain_ok: bool = False) -> object:
    headers = {"Accept": "application/json", "User-Agent": "BOHUN-Freshness-C3-02/3"}
    token = os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"feed HTTP status {response.status}")
        accepted = ("application/json", "text/json", "text/plain") if plain_ok else ("application/json", "text/json")
        if response.headers.get_content_type() not in accepted:
            raise ValueError("feed response is not JSON")
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("response exceeds 4 MiB")
        return json.loads(raw.decode("utf-8"))


def markdown(result: dict, max_hours: float) -> str:
    status = result["status"]
    line = (f"**WARNING: no new material in the public feed for more than {max_hours:g} hours**" if status == "stale"
            else "**ERROR: public synthesis feed could not be checked**" if status == "error"
            else f"**OK: the public feed gained new material within {max_hours:g} hours**")
    hours = result.get("hours_since_new")
    return ("### C3-02 Public synthesis feed freshness\n"
            + line + "\n"
            + f"- Status: `{status}`\n"
            + f"- Hours since new material: `{hours if hours is not None else 'unknown'}`\n"
            + f"- Last new material: `{result.get('last_new_at') or 'unknown'}`\n"
            + f"- Limit in hours: `{max_hours:g}`\n"
            + f"- Entries in feed: `{result.get('count', 'unknown')}`\n"
            + f"- Newest news date, for reference: `{result.get('latest_item_date') or 'unknown'}`\n"
            + f"- Reason: `{result.get('reason', 'unknown')}`\n"
            + f"- Checked endpoint: {FEED_URL}\n")


def append(path: str | None, body: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as output:
            output.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=FEED_URL)
    parser.add_argument("--max-hours", type=float, default=30,
                        help="hours without new material before warning (default: 30; the longest normal gap so far is 28.2)")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--now", help="offset-aware ISO timestamp; offline testing only")
    options = parser.parse_args(argv)
    try:
        if options.max_hours <= 0 or options.timeout <= 0:
            raise ValueError("max-hours and timeout must be positive")
        now = parse_now(options.now) if options.now else datetime.now(timezone.utc)
        result = evaluate(fetch_json(options.url, options.timeout), now)
        result = apply_growth(result, None if result["count"] == 0 else fetch_growth(options.timeout), now, options.max_hours)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        result = {"status": "error", "reason": type(exc).__name__,
                  "detail": str(exc)[:180], "latest_item_date": None,
                  "age_days": None, "count": None, "hours_since_new": None}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print(markdown(result, options.max_hours), file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") and result["status"] != "fresh":
        print(f"::warning title=Public feed freshness::{result['status']}: {result.get('reason')}, "
              f"hours since new material: {result.get('hours_since_new')}")
    append(os.environ.get("GITHUB_STEP_SUMMARY"), markdown(result, options.max_hours))
    append(os.environ.get("GITHUB_OUTPUT"), "\n".join([
        f"freshness_status={result['status']}",
        f"latest_item_date={result.get('latest_item_date') or ''}",
        f"age_days={result.get('age_days') if result.get('age_days') is not None else ''}",
        f"hours_since_new={result.get('hours_since_new') if result.get('hours_since_new') is not None else ''}",
        f"published_count={result.get('count') if result.get('count') is not None else ''}",
    ]) + "\n")
    return {"fresh": 0, "stale": 1, "error": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
