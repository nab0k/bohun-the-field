"""C4-03 one-off: extract production numbers from new articles. Fetches public pages at run time (texts are not stored
in the repository), relaxed rules v4, retry on 503 with a free fallback, the checker removes a number only on an explicit no."""
import html, json, os, re, sys, time, urllib.error, urllib.request, urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from observation import (CAPACITY_EXTRACT_SYSTEM, CAPACITY_SCHEMA, CAPACITY_VERIFY_SYSTEM, check_evidence, normalized,
                         verify_packet)

UA = "bohun-kb-pilot/1.0 (+https://bohundefence.com)"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
GROQ = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
# Rules v4: these four only warn. Offline replay of run 41862925589: they had dropped 31 labelled-correct numbers and 0 forbidden ones.
WARN = {"PLAN_WITHOUT_FUTURE_WORDING", "METRIC_NOT_IN_QUOTE", "BOUND_NOT_IN_QUOTE", "UNIT_NOT_NEXT_TO_NUMBER"}
MAX_REQUESTS, GAP, WAIT = 40, 13, 40
RETRY = {429, 500, 502, 503, 504}


def fetch(url):
    p = urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": UA}), timeout=15) as r:
            rp.parse(r.read().decode("utf-8", "replace").splitlines())
        if not rp.can_fetch(UA, url):
            return None, "robots"
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 410):
            return None, f"robots_http{exc.code}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
        raw = r.read(2_000_000).decode("utf-8", "replace")
    raw = re.sub(r"(?is)<(script|style|nav|footer|header|aside|form)[^>]*>.*?</\1>", " ", raw)
    paras = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p))).strip()
             for p in re.findall(r"(?is)<(?:p|h1|li)[^>]*>(.*?)</(?:p|h1|li)>", raw)]
    return "\n".join(p for p in paras if len(p) > 40)[:24000], "ok"


def post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "bohun-numbers/1.0", **headers})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


def gemini(key, system, user, max_out):
    reply = post(GEMINI, {"x-goog-api-key": key}, {"systemInstruction": {"parts": [{"text": system}]},
                 "contents": [{"role": "user", "parts": [{"text": user}]}],
                 "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_out, "responseMimeType": "application/json"}})
    return "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])


def groq(key, system, user, max_out):
    reply = post(GROQ, {"Authorization": "Bearer " + key}, {"model": GROQ_MODEL, "temperature": 0.1, "max_tokens": max_out,
                 "response_format": {"type": "json_object"},
                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    return reply["choices"][0]["message"]["content"]


class Router:
    def __init__(self, keys):
        self.keys, self.used, self.log = keys, 0, []

    def ask(self, order, system, user, max_out):
        for name in order:
            for _ in range(3):
                if self.used >= MAX_REQUESTS:
                    return None, "budget"
                self.used += 1
                try:
                    out = json.loads((gemini if name == "gemini" else groq)(self.keys[name], system, user, max_out))
                    self.log.append(name + ":ok")
                    time.sleep(GAP)
                    return out, name
                except urllib.error.HTTPError as exc:
                    self.log.append(f"{name}:{exc.code}:" + exc.read(300).decode("utf-8", "replace").replace(self.keys[name], "[key]"))
                    if exc.code not in RETRY:
                        break
                    time.sleep(WAIT)
                except Exception as exc:
                    self.log.append(f"{name}:{type(exc).__name__}")
                    time.sleep(5)
        return None, "failed"


def soft_verdicts(rows, answer):
    """Remove a number only when the checker says ok=false for it; no verdict keeps it, marked unchecked."""
    said = {}
    for v in (answer or {}).get("verdicts") or [] if isinstance(answer, dict) else []:
        if isinstance(v, dict) and type(v.get("n")) is int and 0 <= v["n"] < len(rows) and type(v.get("ok")) is bool:
            said.setdefault(v["n"], set()).add(v["ok"])
    kept = [dict(r, checker="ok" if said.get(n) == {True} else "unchecked") for n, r in enumerate(rows) if False not in said.get(n, set())]
    return kept, len(rows) - len(kept)


def main():
    keys = {"gemini": os.environ["GEMINI_FREE_API_KEY"], "groq": os.environ["GROQ_API_KEY"]}
    urls = json.load(open(HERE / "urls.json"))
    router, report = Router(keys), {}
    extract_system = CAPACITY_EXTRACT_SYSTEM + "\n\nAnswer JSON only, matching this JSON schema:\n" + json.dumps(CAPACITY_SCHEMA)
    for doc, url in urls.items():
        entry = report.setdefault(doc, {})
        try:
            text, state = fetch(url)
        except Exception as exc:
            text, state = None, type(exc).__name__
        entry["fetch"] = state
        if not text:
            continue
        answer, by = router.ask(["gemini", "groq"], extract_system, json.dumps({"title": doc, "text": text}), 6000)
        entry["extract_by"] = by
        items = (answer or {}).get("observations") if isinstance(answer, dict) else None
        if not isinstance(items, list):
            entry["extract"] = "no_observations"
            continue
        passed, dropped = [], []
        for item in items:
            if not isinstance(item, dict):
                continue
            obs = normalized(item)
            reasons = check_evidence(obs, text)
            hard = [r for r in reasons if r not in WARN]
            (dropped if hard else passed).append({"observation": obs, "reasons": reasons} if hard else dict(obs, warnings=reasons))
        entry.update(passed=passed, dropped=dropped)
        if not passed:
            continue
        verify_system = CAPACITY_VERIFY_SYSTEM + '\n\nAnswer JSON only: {"verdicts":[{"n":<row number>,"ok":true or false}]}'
        verdict, vby = router.ask(["groq", "gemini"], verify_system, json.dumps(verify_packet(passed)), 2000)
        kept, removed = soft_verdicts(passed, verdict)
        entry.update(verify_by=vby, kept=kept, removed_by_checker=removed)
        print(doc, state, by, "passed", len(passed), "dropped", len(dropped), "kept", len(kept))
    Path(sys.argv[1]).write_text(json.dumps({"requests": router.used, "log": router.log, "documents": report}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("requests", router.used, router.log)


if __name__ == "__main__":
    main()
