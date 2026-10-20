#!/usr/bin/env python3
"""EXP-013: how much full text does Inoreader `summary.content` already give? One read-only request.

Prints ONLY aggregate numbers (no titles, no text, no credentials): items, how many have a body, text-length
buckets, truncation markers, and per-domain counts of FULL / PARTIAL / EMPTY. Nothing is stored or written.
Owner consent: D-20261019-EXP013-PROBE (one run, n=100). Uses the same INOREADER_* secrets as the
scheduled sync; the token response is never printed.
"""
import collections
import html
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request

API = "https://www.inoreader.com/reader/api/0/"
TOKEN = "https://www.inoreader.com/oauth2/token"
USER_AGENT = "Bohundefence-Collector/1.0 (EXP-013 probe)"
STREAM = "user/-/state/com.google/reading-list"
FULL_MIN = 1500  # characters of plain text treated as a likely full article
TRUNCATION = re.compile(r"read (the )?(full|more)|continue reading|read \d+ (remaining|more) paragraphs|"
                        r"\[(…|\.\.\.)\]|(…|\.\.\.)\s*$|weiterlesen|lire la suite|читать далее|читати далі", re.I)
_TAG = re.compile(r"<[^>]+>")


def plain(content):
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", content or ""))).strip()


def classify(item):
    text = plain((item.get("summary") or {}).get("content"))
    if not text:
        return "EMPTY", 0, False
    truncated = bool(TRUNCATION.search(text[-200:]))
    if len(text) >= FULL_MIN and not truncated:
        return "FULL", len(text), truncated
    return "PARTIAL", len(text), truncated


def domain(item):
    for link in (item.get("canonical") or []) + (item.get("alternate") or []):
        host = urllib.parse.urlsplit(link.get("href") or "").hostname
        if host:
            return host[4:] if host.startswith("www.") else host
    return "unknown"


def summarize(items):
    classes, lengths, marks = collections.Counter(), [], 0
    per_domain = collections.defaultdict(collections.Counter)
    for item in items:
        kind, length, truncated = classify(item)
        classes[kind] += 1
        marks += int(truncated)
        if length:
            lengths.append(length)
        per_domain[domain(item)][kind] += 1
    buckets = collections.Counter(
        "<300" if n < 300 else "300-1499" if n < 1500 else "1500-3999" if n < 4000 else ">=4000" for n in lengths)
    top = sorted(per_domain.items(), key=lambda kv: -sum(kv[1].values()))[:30]
    return {"items": len(items), "classes": dict(classes), "truncation_markers": marks,
            "length_median": int(statistics.median(lengths)) if lengths else 0,
            "length_buckets": dict(buckets), "domains_total": len(per_domain),
            "top_domains": [{"domain": d, **dict(c)} for d, c in top]}


def _post(url, form):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST", headers={
        "User-Agent": USER_AGENT, "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


def _get(url, token):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def main():
    keys = ("INOREADER_CLIENT_ID", "INOREADER_CLIENT_SECRET", "INOREADER_REFRESH_TOKEN")
    env = {k: os.environ.get(k) for k in keys}
    if not all(env.values()):
        print(json.dumps({"status": "error", "error": "missing INOREADER_* secrets"}))
        return 1
    token = _post(TOKEN, {"client_id": env["INOREADER_CLIENT_ID"], "client_secret": env["INOREADER_CLIENT_SECRET"],
                          "refresh_token": env["INOREADER_REFRESH_TOKEN"], "grant_type": "refresh_token"})
    access = token.get("access_token")
    rotated = bool(token.get("refresh_token")) and token.get("refresh_token") != env["INOREADER_REFRESH_TOKEN"]
    del token
    if not access:
        print(json.dumps({"status": "error", "error": "no access_token"}))
        return 1
    url = API + "stream/contents/" + urllib.parse.quote(STREAM, safe="/") + "?" + urllib.parse.urlencode({"n": 100})
    data = _get(url, access)
    out = {"status": "ok", "stream": STREAM, "n_requested": 100, "refresh_token_rotated": rotated,
           **summarize(data.get("items") or [])}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
