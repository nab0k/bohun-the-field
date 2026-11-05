"""C1-02 one-off: classify data/sources/registry.csv with free Gemini. Read-only: nothing is written anywhere but the output file."""
import csv, html, json, os, re, sys, time, urllib.error, urllib.request, urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from collectors.source_registry.classification import PROMPT, classify, prompt_packet, validated_source

URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
UA = "bohun-kb-pilot/1.0 (+https://bohundefence.com)"
BATCH, GAP, WAIT, ATTEMPTS, MAX_REQUESTS, CAP = 15, 13, 60, 3, 16, 1_000_000
RETRY = {429, 500, 502, 503, 504}
SYSTEM = PROMPT + '\n\nYou receive a JSON list of sources. Return {"items":[one object as above per source]}.'


def https(url):
    return "https://" + url[7:] if url.startswith("http://") else url


def allowed(url):
    parts = urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(f"{parts.scheme}://{parts.netloc}/robots.txt", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            rp.parse(r.read(200_000).decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as exc:
        return exc.code in (404, 410)
    except Exception:
        return False
    return rp.can_fetch(UA, url)


def fetch(url):
    if not allowed(url):
        return None, "robots"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(CAP).decode("utf-8", "replace"), "ok"
    except urllib.error.HTTPError as exc:
        return None, f"http{exc.code}"
    except Exception as exc:
        return None, type(exc).__name__


def clean(text):
    text = re.sub(r"<!\[CDATA\[|\]\]>", "", text)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()[:480]


def from_feed(body):
    titles = [clean(t) for t in re.findall(r"(?is)<title[^>]*>(.*?)</title>", body)]
    desc = re.search(r"(?is)<(?:description|subtitle)[^>]*>(.*?)</(?:description|subtitle)>", body)
    return (clean(desc.group(1)) if desc else ""), [t for t in titles[1:21] if t]


def from_home(body):
    head = body[:200_000]
    found = []
    for pattern in (r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
                    r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description',
                    r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)'):
        match = re.search(pattern, head)
        if match:
            found.append(clean(match.group(1)))
            break
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", head)
    if title:
        found.append(clean(title.group(1)))
    heads = [clean(h) for h in re.findall(r"(?is)<h[12][^>]*>(.*?)</h[12]>", head)]
    return " | ".join(found)[:500], [h for h in heads if h][:20]


def gemini(key, user):
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4000, "responseMimeType": "application/json"}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=180) as r:
        reply = json.load(r)
    return "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])


def main():
    key = os.environ["GEMINI_FREE_API_KEY"]
    rows = list(csv.DictReader(open(ROOT / "data/sources/registry.csv", encoding="utf-8")))
    sources, meta = [], {}
    for row in rows:
        feed, home = https(row["feed_url"].strip()), https(row["homepage_url"].strip())
        desc, titles, delivery, state = "", [], [], "no_url"
        if feed:
            body, state = fetch(feed)
            if body:
                desc, titles = from_feed(body)
                if titles:
                    delivery = [{"method": "rss", "status": "confirmed", "ref": "probe-20261105-" + row["source_id"]}]
        if not titles and home:
            body, state = fetch(home)
            if body:
                desc, titles = from_home(body)
        url = feed or home or "https://invalid.example/"
        src = {"source_id": row["source_id"], "title": row["name"].strip()[:480] or row["source_id"], "description": desc,
               "sample_titles": titles, "url": url, "delivery_evidence": delivery}
        try:
            validated_source(src)
        except Exception as exc:
            meta[row["source_id"]] = {"error": str(exc)}
            continue
        sources.append(src)
        meta[row["source_id"]] = {"fetch": state, "titles": len(titles)}
        time.sleep(0.3)
    props, used, log = {}, 0, []
    for start in range(0, len(sources), BATCH):
        chunk = sources[start:start + BATCH]
        user = json.dumps([prompt_packet(validated_source(s)) for s in chunk], ensure_ascii=False)
        for attempt in range(ATTEMPTS):
            if used >= MAX_REQUESTS:
                break
            used += 1
            try:
                items = json.loads(gemini(key, user)).get("items", [])
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get("source_id"), str):
                        props[item["source_id"]] = item
                log.append("ok")
                break
            except urllib.error.HTTPError as exc:
                log.append(exc.code)
                if exc.code not in RETRY:
                    break
                time.sleep(WAIT)
            except Exception as exc:
                log.append(type(exc).__name__)
        time.sleep(GAP)
    out = []
    for src in sources:
        c = classify(src, propose=lambda packet, sid=src["source_id"]: props[sid])
        out.append({**c.json_dict(), "name": src["title"], "url": src["url"], "description": src["description"][:300],
                    "sample": src["sample_titles"][:3], **meta[src["source_id"]]})
    Path(sys.argv[1]).write_text(json.dumps({"requests": used, "log": log, "errors": {k: v for k, v in meta.items() if "error" in v},
                                             "results": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    states = {}
    for row in out:
        states[row["class_status"]] = states.get(row["class_status"], 0) + 1
    print("rows", len(rows), "classified", len(out), "requests", used, log, states)


if __name__ == "__main__":
    main()
