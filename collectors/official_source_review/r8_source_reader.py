"""BOHUN EXP-009 R8: bounded, read-only UK OCDS window reader.

REVIEW ONLY. No network is initiated except through an injected transport(url)
callback. No database writes, scheduling, checkpoint advancement or publication.
The request dates are UK wall-clock strings, matching the existing PR #77 reader.
Assumption about server time interpretation must be verified around DST.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Callable
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

API = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
TZ = ZoneInfo("Europe/London")
ALLOWED_QUERY = frozenset({"cursor", "limit", "updatedFrom", "updatedTo"})
CURSOR = re.compile(r"[A-Za-z0-9=]{1,300}\Z")
DATE_FMT = "%Y-%m-%dT%H:%M:%S"
MAX_BYTES_PER_PAGE = 8_000_000


class WindowError(ValueError):
    """Fail-closed validation error; never advance any checkpoint."""


@dataclass(frozen=True)
class SourceWindow:
    local_from: str
    local_to: str
    utc_from: datetime
    utc_to: datetime


def _to_utc_strict(text: str) -> datetime:
    if not isinstance(text, str):
        raise WindowError("window_format")
    try:
        naive = datetime.strptime(text, DATE_FMT)
    except ValueError:
        raise WindowError("window_format") from None
    a, b = (naive.replace(tzinfo=TZ, fold=f) for f in (0, 1))
    if a.utcoffset() != b.utcoffset():
        raise WindowError("dst_ambiguous_or_nonexistent")
    utc = a.astimezone(timezone.utc)
    if utc.astimezone(TZ).replace(tzinfo=None) != naive:
        raise WindowError("dst_nonexistent")
    return utc


def make_window(local_from: str, local_to: str) -> SourceWindow:
    """Reject UK clock-change windows rather than give false coverage receipts."""
    a, b = _to_utc_strict(local_from), _to_utc_strict(local_to)
    na, nb = datetime.strptime(local_from, DATE_FMT), datetime.strptime(local_to, DATE_FMT)
    if b <= a or b - a > timedelta(hours=1):
        raise WindowError("window_out_of_bounds")
    if b - a != nb - na:  # Clock changes cannot be represented unambiguously.
        raise WindowError("window_crosses_dst")
    return SourceWindow(local_from, local_to, a, b)


def plan_windows(utc_from: datetime, utc_to: datetime, *, minutes: int = 15,
                 overlap_seconds: int = 1, max_windows: int = 96) -> list[SourceWindow]:
    """Plan only. Real checkpoint advances require a separately approved writer.

    Small overlap mitigates undocumented inclusivity at source boundaries.
    De-duplicate later by (ocid, release.id) and content digest.
    """
    if any(not isinstance(d, datetime) or d.tzinfo is None for d in (utc_from, utc_to)):
        raise WindowError("utc_aware_required")
    if not isinstance(minutes, int) or not 1 <= minutes <= 60:
        raise WindowError("minutes_invalid")
    if not isinstance(overlap_seconds, int) or not 0 <= overlap_seconds <= 30:
        raise WindowError("overlap_invalid")
    if not isinstance(max_windows, int) or max_windows < 1:
        raise WindowError("window_count_invalid")
    a, b = utc_from.astimezone(timezone.utc), utc_to.astimezone(timezone.utc)
    if not a < b or a.microsecond or b.microsecond:
        raise WindowError("utc_interval_invalid")
    windows = []
    cur = a
    while cur < b:
        end = min(cur + timedelta(minutes=minutes), b)
        local_start = max(a, cur - timedelta(seconds=overlap_seconds))
        x = local_start.astimezone(TZ).strftime(DATE_FMT)
        y = end.astimezone(TZ).strftime(DATE_FMT)
        proposed = make_window(x, y)
        if proposed.utc_from != local_start or proposed.utc_to != end:
            raise WindowError("window_roundtrip_mismatch")
        windows.append(proposed)
        if len(windows) > max_windows:
            raise WindowError("too_many_windows")
        cur = end
    return windows


def _url(window: SourceWindow, cursor: str | None = None) -> str:
    query = {"updatedFrom": window.local_from,
             "updatedTo": window.local_to, "limit": "100"}
    if cursor is not None:
        query["cursor"] = cursor
    return API + "?" + urlencode(query)


def _next_cursor(next_link: str, window: SourceWindow) -> str:
    if not isinstance(next_link, str) or not 0 < len(next_link) <= 2048:
        raise WindowError("bad_next_link")
    url = urlsplit(urljoin(API, next_link))
    base = urlsplit(API)
    if (url.scheme != "https" or url.netloc != base.netloc or
            url.path != base.path or url.fragment or url.username or url.password):
        raise WindowError("next_outside_official_api")
    params = parse_qs(url.query, keep_blank_values=True)
    if set(params) - ALLOWED_QUERY or any(len(v) != 1 for v in params.values()):
        raise WindowError("next_scope_unrecognized")
    expected = {"updatedFrom": window.local_from, "updatedTo": window.local_to,
                "limit": "100"}
    for key, value in expected.items():
        if key in params and params[key][0] != value:
            raise WindowError("next_changes_scope")
    token = params.get("cursor", [None])[0]
    if not isinstance(token, str) or not CURSOR.fullmatch(token):
        raise WindowError("bad_cursor")
    return token


def _fingerprint(item: dict) -> str:
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


def read_window(window: SourceWindow, transport: Callable[[str], bytes], *,
                max_pages: int = 25, max_releases: int = 2500,
                max_total_bytes: int = 48_000_000) -> dict:
    """Return a complete in-memory window or fail. Never truncate or auto-commit.

    Transport implementation must enforce timeouts, TLS, 429/503 Retry-After and
    request rate limits. A transport error propagates and invalidates the run.
    """
    if not isinstance(window, SourceWindow) or not callable(transport):
        raise WindowError("bad_arguments")
    if not (1 <= max_pages <= 100 and 1 <= max_releases <= max_pages * 100
            and max_total_bytes > 0):
        raise WindowError("invalid_limits")
    seen: dict[tuple[str, str], str] = {}
    tokens = set()
    collected: list[dict] = []
    total_bytes = 0
    duplicate_count = 0
    cursor = None
    for page_no in range(1, max_pages + 1):
        payload = transport(_url(window, cursor))
        if not isinstance(payload, bytes) or len(payload) > MAX_BYTES_PER_PAGE:
            raise WindowError("response_not_bounded_bytes")
        total_bytes += len(payload)
        if total_bytes > max_total_bytes:
            raise WindowError("total_bytes_limit")
        try:
            doc = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            raise WindowError("invalid_json") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("releases"), list):
            raise WindowError("missing_releases")
        rows = doc["releases"]
        if len(rows) > 100:
            raise WindowError("overfull_page")
        for row in rows:
            if not isinstance(row, dict):
                raise WindowError("bad_release")
            ocid, rid = row.get("ocid"), row.get("id")
            if (not isinstance(ocid, str) or not isinstance(rid, str) or
                    not ocid or not rid or len(ocid) > 512 or len(rid) > 256 or
                    len(ocid) + len(rid) + 2 > 512):
                raise WindowError("invalid_identity")
            key = (ocid, rid)
            try:
                digest = _fingerprint(row)
            except (TypeError, ValueError):
                raise WindowError("not_canonical_json") from None
            if key in seen:
                if seen[key] != digest:
                    raise WindowError("conflicting_release_versions_same_window")
                duplicate_count += 1
            else:
                seen[key] = digest
                collected.append(row)
                if len(collected) > max_releases:
                    raise WindowError("too_many_releases")
        links = doc.get("links", {})
        if links is None:
            links = {}  # Some upstream pages omit pagination metadata.
        if not isinstance(links, dict):
            raise WindowError("bad_links")
        next_link = links.get("next")
        if next_link is None:
            return {
                "complete": True, "window": window,
                "pages": page_no, "unique_releases": len(collected),
                "duplicate_releases": duplicate_count,
                "releases": collected,
                "publication_window_enforced": False,
                "checkpoint_advanced": False,
            }
        next_cursor = _next_cursor(next_link, window)
        if next_cursor in tokens or next_cursor == cursor:
            raise WindowError("cursor_cycle")
        tokens.add(next_cursor)
        cursor = next_cursor
    raise WindowError("incomplete_pagination")


def group_lifecycle(releases: list[dict]) -> dict[str, list[dict]]:
    """Only expose observed release facts; don't infer status from missing rows."""
    grouped: dict[str, list[dict]] = {}
    for row in releases:
        if not isinstance(row, dict) or not isinstance(row.get("ocid"), str):
            raise WindowError("invalid_lifecycle_release")
        tags = row.get("tag", [])
        if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
            raise WindowError("invalid_lifecycle_tag")
        tender = row.get("tender") or {}
        if not isinstance(tender, dict):
            raise WindowError("invalid_tender_metadata")
        grouped.setdefault(row["ocid"], []).append({
            "release_id": row.get("id"),
            "release_date": row.get("date"),
            "observed_tags": tuple(tags),
            "reported_tender_status": tender.get("status"),
            "status_verified": False,
        })
    return grouped
