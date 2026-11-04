"""C4-02: free models propose a signal for each synthesis group; a second free model checks it. Nothing is published."""
import base64, hashlib, json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.request, urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
CAP = 4000
UA = "bohun-kb-pilot/1.0 (+https://bohundefence.com)"
SKIP = {"script", "style", "nav", "footer", "header", "aside", "noscript", "form", "svg", "button"}
BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "div", "br", "tr", "section", "article"}
RETRY_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS, WAIT, GAP_WRITE, GAP_CHECK, MAX_REQUESTS = 3, 60, 13, 25, 54

WRITER = """You judge whether a group of defence-industry reports shows a signal. A signal states that a significant change has happened or is forming in a market, with the evidence chain, the mechanism, what is uncertain, and how it could be proved wrong. News reports a fact; several news items about one subject are not automatically a signal.

Rules:
- Use only the given sources. Add nothing from memory.
- Each source's summary is a pointer only. Evidence must be a verbatim quote from that source's text, at most 300 characters.
- Several outlets reporting one event count as one piece of evidence.
- A change needs a baseline: what was true before, for the same buyer, product or units, quoted from a source. If the sources give none, set baseline to null and say so.
- Keep announced, contracted, delivered and demonstrated apart.
- Answering NO_SIGNAL is a good answer when the group does not show a change. Say why.

Answer JSON only:
{"verdict":"SIGNAL or NO_SIGNAL","statement":"one sentence","kind":"state_change or anticipatory or none","subject":"DEMAND, BUDGET_FUNDING, CAPACITY, SUPPLY_CHAIN, TECHNOLOGY, COMPANY_COMPETITION, REGULATION_TRADE, MATERIALS, PARTNERSHIP or none","what_changed":"","baseline":{"source_id":"","quote":"","meaning":""},"evidence":[{"source_id":"","quote":"","stage":"announced, contracted, delivered or demonstrated"}],"mechanism":"","who_should_care":"","strengthens":[""],"weakens":[""],"refuted_by":[""],"unknowns":[""],"confidence":"LOW, MODERATE or HIGH","why_no_signal":""}"""

CHECKER = """You check a proposed defence-market signal against the quotes given. Be strict. Answer JSON only:
{"supported":true or false,"overstated":true or false,"independent_events":<count of separate real events behind the quotes>,"baseline_supports_change":true or false,"reason":"at most two sentences"}"""


def post(url, headers, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "bohun-signals-probe/1.0", **headers})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def gemini(key, system, user):
    reply = post(GEMINI_URL, {"x-goog-api-key": key},
                 {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": {"temperature": 0.2, "maxOutputTokens": 3000, "responseMimeType": "application/json"}})
    return "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])


def groq(key, system, user):
    reply = post(GROQ_URL, {"Authorization": "Bearer " + key},
                 {"model": GROQ_MODEL, "temperature": 0.1, "max_tokens": 900, "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    return reply["choices"][0]["message"]["content"]


class Trial:
    def __init__(self, keys, write=gemini, check=groq, sleep=time.sleep):
        self.keys, self.write, self.check, self.sleep, self.requests = keys, write, check, sleep, 0

    def call(self, fn, key, system, user, gap):
        log = []
        for number in range(ATTEMPTS):
            if self.requests >= MAX_REQUESTS:
                return None, log + ["budget"]
            self.requests += 1
            try:
                text = fn(key, system, user)
                self.sleep(gap)
                return json.loads(text), log + ["ok"]
            except urllib.error.HTTPError as exc:
                log.append(exc.code)
                if exc.code not in RETRY_STATUS:
                    break
            except (urllib.error.URLError, TimeoutError) as exc:
                log.append(type(exc).__name__)
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                log.append("bad_reply:" + type(exc).__name__)
            if number < ATTEMPTS - 1:
                self.sleep(WAIT)
        return None, log

    def run(self, packet):
        out = {}
        for group, sources in packet.items():
            proposal, write_log = self.call(self.write, self.keys["gemini"], WRITER, json.dumps(sources, ensure_ascii=False), GAP_WRITE)
            entry = {"proposal": proposal, "write_log": write_log}
            if proposal and proposal.get("verdict") == "SIGNAL":
                view = {k: proposal.get(k) for k in ("statement", "what_changed", "baseline", "evidence")}
                view["reports"] = [{"source_id": s["source_id"], "title": s["title"], "outlet": s["outlet"]} for s in sources]
                entry["check"], entry["check_log"] = self.call(self.check, self.keys["groq"], CHECKER, json.dumps(view, ensure_ascii=False), GAP_CHECK)
            out[group] = entry
        return {"groups": out, "requests": self.requests}


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP: self.skip += 1
        if tag in BLOCK: self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip: self.skip -= 1
        if tag in BLOCK: self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def article(html, title):
    parser = Text(); parser.feed(html)
    text = re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t]+", " ", "".join(parser.parts))).strip()
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) >= 40]
    keys = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", title)][:6]
    start = next((i for i, l in enumerate(lines) if sum(k in l.lower() for k in keys) >= 2), 0)
    return "\n".join(lines[start:])[:CAP]


def fetch(url):
    """One public GET, robots first. A block stays a block."""
    split = urlsplit(url)
    robots = urllib.robotparser.RobotFileParser()
    try:
        request = urllib.request.Request(f"{split.scheme}://{split.netloc}/robots.txt", headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=30) as reply:
            robots.parse(reply.read(200000).decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return None, f"robots_{exc.code}"
        robots.allow_all = True
    except Exception as exc:
        return None, "robots_" + type(exc).__name__
    if not robots.can_fetch(UA, url):
        return None, "robots_disallowed"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as reply:
            return reply.read(4000000).decode("utf-8", "replace"), "ok"
    except urllib.error.HTTPError as exc:
        return None, f"http_{exc.code}"
    except Exception as exc:
        return None, type(exc).__name__


def load(meta, get=fetch):
    packet, fetched = {}, {}
    for group, sources in meta.items():
        packet[group] = []
        for source in sources:
            html, status = get(source["url"])
            fetched[source["source_id"]] = status
            if html:
                packet[group].append({k: source[k] for k in ("source_id", "synthesis", "outlet", "date", "title", "summary")}
                                     | {"text": article(html, source["title"])})
    return packet, fetched


def encrypt(report, output):
    cert = base64.b64decode(os.environ["SIGNALS_RECIPIENT_B64"], validate=True)
    if hashlib.sha256(cert).hexdigest() != os.environ["SIGNALS_RECIPIENT_SHA256"] or b"PRIVATE KEY" in cert:
        raise ValueError("certificate integrity")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recipient.pem"
        path.write_bytes(cert)
        done = subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-cbc", "-outform", "DER", str(path)],
                              input=json.dumps(report, ensure_ascii=False).encode(), capture_output=True, timeout=30)
    if done.returncode or not done.stdout:
        raise RuntimeError("encryption failed")
    Path(output).write_bytes(done.stdout)


def main():
    try:
        if len(sys.argv) != 2 or os.environ.get("SIGNALS_FREE_ACCOUNTS_APPROVED") != "true":
            raise ValueError("approval missing")
        packet, fetched = load(json.loads((Path(__file__).parent / "groups.json").read_text(encoding="utf-8")))
        report = Trial({"gemini": os.environ["GEMINI_FREE_API_KEY"], "groq": os.environ["GROQ_API_KEY"]}).run(packet)
        report.update(fetched=fetched, packet=packet)
        encrypt(report, sys.argv[1])
    except Exception as exc:
        print("PROBE stopped:", type(exc).__name__, file=sys.stderr)
        return 1
    print("PROBE requests", report["requests"], {g: e["write_log"] for g, e in report["groups"].items()}, sum(v == "ok" for v in report["fetched"].values()), "of", len(report["fetched"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
