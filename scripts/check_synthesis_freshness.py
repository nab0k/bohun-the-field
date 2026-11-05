#!/usr/bin/env python3
"""C3-02: read-only calendar-day freshness of BOHUN's public synthesis feed.

Exit: 0 fresh, 1 stale, 2 unavailable/invalid. No credentials or database access.
The feed's item.date is a DATE, not an established publication timestamp.
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


def fetch_json(url: str, timeout: float) -> object:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "BOHUN-Freshness-C3-02/2"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"feed HTTP status {response.status}")
        if response.headers.get_content_type() not in ("application/json", "text/json"):
            raise ValueError("feed response is not JSON")
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("feed exceeds 4 MiB")
        return json.loads(raw.decode("utf-8"))


def markdown(result: dict, max_age_days: int) -> str:
    status = result["status"]
    line = (f"**WARNING: latest item date older than {max_age_days} UTC calendar day(s)**" if status == "stale"
            else "**ERROR: public synthesis feed could not be checked**" if status == "error"
            else "**OK: latest item date within the UTC calendar-day threshold**")
    age = result.get("age_days")
    return ("### C3-02 Public synthesis item-date freshness\n"
            + line + "\n"
            + f"- Status: `{status}`\n"
            + f"- Latest item date: `{result.get('latest_item_date') or 'unknown'}`\n"
            + f"- Age in UTC calendar days: `{age if age is not None else 'unknown'}`\n"
            + f"- Threshold in days: `{max_age_days}`\n"
            + f"- Entries in feed: `{result.get('count', 'unknown')}`\n"
            + f"- Reason: `{result.get('reason', 'unknown')}`\n"
            + f"- Checked endpoint: {FEED_URL}\n"
            + "- Caution: feed item.date may be news/event date rather than actual website publication time.\n")


def append(path: str | None, body: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as output:
            output.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=FEED_URL)
    parser.add_argument("--max-age-days", type=int, default=1,
                        help="maximum whole UTC calendar-day difference (default: 1)")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--now", help="offset-aware ISO timestamp; offline testing only")
    options = parser.parse_args(argv)
    try:
        if options.max_age_days < 0 or options.timeout <= 0:
            raise ValueError("max-age-days must be >=0 and timeout must be positive")
        now = parse_now(options.now) if options.now else datetime.now(timezone.utc)
        result = evaluate(fetch_json(options.url, options.timeout), now, options.max_age_days)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "error", "reason": type(exc).__name__,
                  "detail": str(exc)[:180], "latest_item_date": None,
                  "age_days": None, "count": None}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print(markdown(result, options.max_age_days), file=sys.stderr)
    append(os.environ.get("GITHUB_STEP_SUMMARY"), markdown(result, options.max_age_days))
    append(os.environ.get("GITHUB_OUTPUT"), "\n".join([
        f"freshness_status={result['status']}",
        f"latest_item_date={result.get('latest_item_date') or ''}",
        f"age_days={result.get('age_days') if result.get('age_days') is not None else ''}",
        f"published_count={result.get('count') if result.get('count') is not None else ''}",
    ]) + "\n")
    return {"fresh": 0, "stale": 1, "error": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
