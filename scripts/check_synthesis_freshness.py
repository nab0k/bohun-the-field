#!/usr/bin/env python3
"""C3-02: read-only freshness probe for the *public* BOHUN synthesis feed.

Exit: 0 fresh, 1 stale, 2 unavailable/invalid. No credentials or database access.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FEED_URL = "https://bohundefence.com/feed/synthesis-feed.json"
DATE_FIELDS = ("published_at", "publishedAt", "published", "publication_date", "published_date", "created_at", "createdAt", "date")
LIST_FIELDS = ("items", "syntheses", "syntheses_feed", "entries", "articles", "data")
MAX_FUTURE_SECONDS = 300


def parse_date(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing or non-string publication timestamp")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError("publication timestamp must include UTC offset")
    return dt.astimezone(timezone.utc)


def feed_items(document: object) -> list[dict]:
    if isinstance(document, list):
        rows = document
    elif isinstance(document, dict):
        rows = None
        for field in LIST_FIELDS:
            candidate = document.get(field)
            if isinstance(candidate, list):
                rows = candidate
                break
            if isinstance(candidate, dict):
                for nested in LIST_FIELDS:
                    if isinstance(candidate.get(nested), list):
                        rows = candidate[nested]
                        break
                if rows is not None:
                    break
        if rows is None:
            raise ValueError("feed has no recognized array of publications")
    else:
        raise ValueError("feed root must be an object or an array")
    if any(not isinstance(item, dict) for item in rows):
        raise ValueError("feed includes non-object entries")
    return rows


def evaluate(document: object, now: datetime, max_age_hours: float = 24.0) -> dict:
    """Only per-publication dates count; feed build time is not evidence of new content."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    rows = feed_items(document)
    if not rows:
        return {"status": "stale", "count": 0, "age_hours": None,
                "latest_published_at": None, "reason": "feed_empty"}
    dates = []
    for row in rows:
        field = next((name for name in DATE_FIELDS if name in row and row[name]), None)
        if field is None:
            raise ValueError("publication without a recognized publication timestamp")
        dates.append(parse_date(row[field]))
    newest = max(dates)
    seconds = (now.astimezone(timezone.utc) - newest).total_seconds()
    if seconds < -MAX_FUTURE_SECONDS:
        raise ValueError("latest publication timestamp is more than 5 minutes in the future")
    age_hours = max(0.0, seconds / 3600.0)
    return {"status": "stale" if seconds > max_age_hours * 3600 else "fresh",
            "count": len(rows), "age_hours": round(age_hours, 2),
            "latest_published_at": newest.isoformat(),
            "reason": "older_than_threshold" if seconds > max_age_hours * 3600 else "ok"}


def fetch_json(url: str, timeout: float) -> object:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "BOHUN-Freshness-C3-02/1"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"feed HTTP status {response.status}")
        if response.headers.get_content_type() not in ("application/json", "text/json"):
            raise ValueError("feed response is not JSON")
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("feed exceeds 4 MiB")
        return json.loads(raw.decode("utf-8"))


def markdown(result: dict) -> str:
    status = result["status"]
    age = result.get("age_hours")
    line = (f"**WARNING: no new synthesis published in 24 hours**" if status == "stale"
            else "**ERROR: public synthesis feed could not be checked**" if status == "error"
            else "**OK: public synthesis feed is fresh**")
    return ("### C3-02 Public synthesis freshness\n"
            + line + "\n"
            + f"- Status: `{status}`\n"
            + f"- Latest publication: `{result.get('latest_published_at') or 'unknown'}`\n"
            + f"- Age, hours: `{age if age is not None else 'unknown'}`\n"
            + f"- Published entries in feed: `{result.get('count', 'unknown')}`\n"
            + f"- Reason: `{result.get('reason', 'unknown')}`\n"
            + f"- Checked endpoint: {FEED_URL}\n")


def append(path: str | None, body: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as output:
            output.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=FEED_URL)
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--now", help="UTC ISO timestamp for offline tests only")
    options = parser.parse_args(argv)
    try:
        if options.max_age_hours <= 0 or options.timeout <= 0:
            raise ValueError("threshold and timeout must be positive")
        now = parse_date(options.now) if options.now else datetime.now(timezone.utc)
        result = evaluate(fetch_json(options.url, options.timeout), now, options.max_age_hours)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "error", "reason": type(exc).__name__,
                  "detail": str(exc)[:180], "latest_published_at": None,
                  "age_hours": None, "count": None}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print(markdown(result), file=sys.stderr)
    append(os.environ.get("GITHUB_STEP_SUMMARY"), markdown(result))
    append(os.environ.get("GITHUB_OUTPUT"), "\n".join([
        f"freshness_status={result['status']}",
        f"latest_published_at={result.get('latest_published_at') or ''}",
        f"age_hours={result.get('age_hours') if result.get('age_hours') is not None else ''}",
        f"published_count={result.get('count') if result.get('count') is not None else ''}",
    ]) + "\n")
    return {"fresh": 0, "stale": 1, "error": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
