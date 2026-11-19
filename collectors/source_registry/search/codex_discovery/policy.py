"""Strict data contracts and explainable source admission; no network or models."""
import datetime as dt
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit


class Problem(ValueError):
    """Public error text is a fixed code, never an upstream URL/body/credential."""


def require(condition, code):
    if not condition:
        raise Problem(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def parse_json(raw, limit=1_000_000):
    require(isinstance(raw, bytes) and len(raw) <= limit, "JSON_SIZE")
    def pairs(items):
        result = {}
        for key, val in items:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = val
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Problem("JSON_NONFINITE")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise Problem("JSON_INVALID") from None


def read_file(path, *, limit=1_000_000):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "PATH_ABSOLUTE_REQUIRED")
    require(not any(p.is_symlink() for p in (path, *path.parents)), "PATH_SYMLINK")
    require(path.is_file() and path.stat().st_size <= limit, "INPUT_SIZE_OR_TYPE")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "INPUT_SIZE")
    return raw


def stamp(epoch):
    require(isinstance(epoch, (float, int)) and math.isfinite(epoch), "TIME_INVALID")
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def epoch(value):
    require(isinstance(value, str) and len(value) <= 64, "DATE_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "DATE_TIMEZONE")
        return parsed.timestamp()
    except (ValueError, OverflowError):
        raise Problem("DATE_INVALID") from None


SENSITIVE_KEYS = re.compile(r"^(token|access_token|refresh_token|api_?key|key|auth|secret|signature|sig|password)$", re.I)


def identity_url(value, *, opaque=False):
    require(isinstance(value, str) and 0 < len(value) <= 4096 and
            not any(ord(c) <= 32 or ord(c) == 127 for c in value) and "\\" not in value,
            "URL_INVALID")
    try:
        parts = urlsplit(value)
        port = parts.port
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        raise Problem("URL_INVALID") from None
    require(parts.scheme.lower() == "https" and host and not parts.username and not parts.password,
            "URL_HTTPS_REQUIRED")
    require(port in (None, 443) and host != "localhost" and
            not host.endswith((".local", ".internal", ".localhost")), "URL_PRIVATE")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        require("." in host and re.fullmatch(r"[a-z0-9.-]+", host) is not None, "URL_HOST")
    else:
        require(address.is_global, "URL_PRIVATE")
    if not opaque:
        require(not any(SENSITIVE_KEYS.match(key) for key, _ in parse_qsl(parts.query)), "URL_CAPABILITY")
    authority = "[" + host + "]" if ":" in host else host
    return urlunsplit(("https", authority, parts.path or "/", parts.query, ""))


def load_policy(path):
    raw = read_file(path)
    value = parse_json(raw)
    require(isinstance(value, dict) and value.get("version") == 1, "POLICY_VERSION")
    bounds = {"interval_seconds": (60, 86400), "max_sites": (1, 10),
              "max_requests": (1, 120), "max_bytes": (1024, 20_000_000),
              "max_seconds": (1, 600), "max_api_day": (1, 200),
              "max_add_cycle": (1, 1), "max_add_day": (1, 5),
              "max_folder_day": (1, 5), "snapshot_ttl": (1, 300),
              "max_hits": (1, 100), "max_pages": (1, 2)}
    require(isinstance(value.get("limits"), dict) and set(value["limits"]) == set(bounds), "POLICY_LIMITS")
    for key, (minimum, maximum) in bounds.items():
        n = value["limits"][key]
        require(type(n) is int and minimum <= n <= maximum, "POLICY_LIMIT_VALUE")
    rules = value.get("rules")
    require(isinstance(rules, dict) and all(isinstance(rules.get(k), list) and
            all(isinstance(t, str) and 0 < len(t) < 100 for t in rules[k])
            for k in ("domain", "exclude")), "POLICY_RULES")
    require(isinstance(rules.get("signals"), dict) and rules["signals"] and
            all(isinstance(v, list) and all(isinstance(t, str) and t for t in v)
                for v in rules["signals"].values()), "POLICY_SIGNALS")
    require(value.get("live_inputs") is False and value.get("writer_enabled") is False,
            "POLICY_LIVE_DEFAULTS")
    require(value.get("min_titles") == 10 and value.get("sample_size") == 20 and
            value.get("min_relevant_ratio") == 0.6 and value.get("max_age_days") == 90,
            "POLICY_THRESHOLDS")
    return value, hashlib.sha256(raw).hexdigest()


GROUPS = {
    "MEDIA": "Media / intelligence",
    "GOV": "Governments / procurement / regulation",
    "INDUSTRY": "Industry / corporates",
    "OPPORTUNITIES": "Investments / accelerators / aid / events",
    "RESEARCH": "Additional sources — completion to 100",
}


def route(feed):
    """Versioned explicit route rules, never an unknown-folder fallback."""
    host = urlsplit(feed["feed_url"]).hostname
    title = feed.get("title", "").casefold()
    if host.endswith((".gov", ".mil", ".europa.eu")):
        return GROUPS["GOV"], "route:government-domain"
    matches = []
    for key, terms in (("OPPORTUNITIES", ("opportunities", "funding calls", "grant calls")),
                       ("RESEARCH", ("research", "working papers")),
                       ("INDUSTRY", ("corporate press", "company releases"))):
        if any(term in title for term in terms):
            matches.append(key)
    if len(matches) == 1:
        return GROUPS[matches[0]], "route:" + matches[0].lower()
    if not matches and any(t in title for t in ("news", "journal", "review", "новини", "новости")):
        return GROUPS["MEDIA"], "route:news-title"
    return None, "route:ambiguous-or-unknown"


def assess(feed, policy, now):
    reasons = []
    by_url = {e["url"]: e for e in feed.get("entries", []) if e.get("title") and e.get("url")}
    sample = list({e["title"].strip().casefold(): e for e in by_url.values()}.values())[:20]
    rules = policy["rules"]
    relevant = 0
    for entry in sample:
        text = entry["title"].casefold()
        domain = [t for t in rules["domain"] if t.casefold() in text]
        signals = [k for k, terms in rules["signals"].items() if any(t.casefold() in text for t in terms)]
        excluded = [t for t in rules["exclude"] if t.casefold() in text]
        if domain and signals and not excluded:
            relevant += 1
            reasons.extend(["domain:" + domain[0], "signal:" + signals[0]])
    group, rule = route(feed)
    reasons.append(rule)
    dates = [e["published_epoch"] for e in sample if isinstance(e.get("published_epoch"), (int, float))
             and e["published_epoch"] <= now + 86400]
    ratio = relevant / len(sample) if sample else 0
    if len(sample) < policy["min_titles"]:
        reasons.append("gate:insufficient-titles")
    if ratio < policy["min_relevant_ratio"]:
        reasons.append("gate:domain-signal-ratio")
    if len(dates) < len(sample) / 2 or not dates or now - max(dates) > policy["max_age_days"] * 86400:
        reasons.append("gate:dates-or-freshness")
    lang = feed.get("language", "").lower().split("-")[0]
    if lang not in policy["languages"]:
        reasons.append("gate:language-uncovered")
    if feed.get("access") != "PUBLIC" or feed.get("comment_feed"):
        reasons.append("gate:access-or-comments")
    decision = "AUTO_ADMIT" if group and not any(r.startswith("gate:") for r in reasons) else "REVIEW"
    return {"decision": decision, "group": group, "sample_size": len(sample),
            "relevant_count": relevant, "relevant_ratio": ratio,
            "rule_ids": sorted(set(reasons)), "trust": "NOT_ESTABLISHED"}
