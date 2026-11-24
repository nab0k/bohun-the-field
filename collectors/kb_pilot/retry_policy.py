"""Retry timing for the opt-in D2RC admission queue."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re


DEFAULT_DELAY = timedelta(minutes=15)
DAY = timedelta(days=1)
GROQ_RESET = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?")


def retry_after(value, now):
    if not value:
        return timedelta(0)
    try:
        if value.isascii() and value.isdecimal():
            return timedelta(seconds=min(int(value), 86400))
        date = parsedate_to_datetime(value)
        return max(date - now, timedelta(0))
    except (TypeError, ValueError, OverflowError):
        return timedelta(0)


def route_block(provider, error, now):
    if error.code == 402:
        return "billing_blocked", None
    if error.code != 429:
        return None
    headers = {key.lower(): value for key, value in (error.headers or {}).items()}
    delay = retry_after(headers.get("retry-after"), now)
    if provider == "groq" and headers.get("x-ratelimit-remaining-requests") == "0":
        reset = GROQ_RESET.fullmatch(headers.get("x-ratelimit-reset-requests", ""))
        if reset and any(reset.groups()):
            hours, minutes, seconds = (float(value or 0) for value in reset.groups())
            try:
                window = timedelta(hours=hours, minutes=minutes, seconds=seconds)
            except OverflowError:
                window = None
            if window is not None and timedelta(0) < window <= DAY:
                return "daily_quota", now + max(window, delay, timedelta(seconds=1))
        # The limit is confirmed even when an exact reset is missing.
        return "daily_quota", now + max(DAY, delay)
    return "rate_limited", now + (delay or DEFAULT_DELAY)


def retry_due(block, now):
    return block[1] is not None and block[1] <= now


def queue_wait(blocks, now):
    if any(block is None for block in blocks) or not blocks:
        return "reviewer_unavailable", now + DEFAULT_DELAY
    timed = [block for block in blocks if block[1] is not None]
    if timed:
        return min(timed, key=lambda block: block[1])
    # No HTTP call is made while billing is blocked. Periodically reconsider the
    # version so an explicitly restored or newly configured route can pick it up.
    return "billing_blocked", now + DEFAULT_DELAY


def utc_now():
    return datetime.now(timezone.utc)
