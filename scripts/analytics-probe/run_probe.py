"""C5-01: one analytic draft from published syntheses on free models. Nothing is published; the result is encrypted."""
import base64, hashlib, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
RETRY_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS = 3
WAIT = 60

SYSTEM = """You write one analytic article for BOHUN, a defence-industry publication. The reader is a business development manager or buyer at a defence or dual-use supplier who decides on partnerships, procurement and supply.

Use ONLY the syntheses given in the input. Do not add any fact, number, date, company or programme from memory. Some Context lines may belong to another story: ignore any line that does not concern the item's own subject.

Write in English, 600 to 900 words, in this order:
1. Headline: states the finding, not the topic.
2. Bottom line: two sentences saying what is changing and for whom it matters.
3. What changed: the evidence across the syntheses, compared with each other.
4. How we got here: only dated history found in the input.
5. What comes next: clearly marked as assessment, with the reasoning.
6. Who should act and how: concrete for suppliers and buyers.
7. What would prove this wrong: at least two observable events.
8. What we do not know: gaps in the evidence.

Every factual sentence ends with its citation, such as [syn-383]. A claim drawn from several items cites each. Sentences of your own judgement start with "Assessment:" and carry no citation. Separate what was announced, contracted, delivered and demonstrated; never upgrade one to another. Do not call a trend established if the evidence is thin; say how thin it is. Plain language, no hype."""


def post(url, headers, body, timeout=240):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "bohun-analytics-probe/1.0", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def gemini(key, user):
    reply = post(GEMINI_URL.format(GEMINI_MODEL), {"x-goog-api-key": key},
                 {"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": {"temperature": 0.3, "maxOutputTokens": 6000}})
    candidate = reply["candidates"][0]
    return "".join(p.get("text", "") for p in candidate["content"]["parts"]), candidate.get("finishReason"), reply.get("usageMetadata")


def groq(key, user):
    reply = post(GROQ_URL, {"Authorization": "Bearer " + key},
                 {"model": GROQ_MODEL, "temperature": 0.3, "max_tokens": 2600,
                  "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]})
    choice = reply["choices"][0]
    return choice["message"]["content"], choice.get("finish_reason"), reply.get("usage")


def attempt(call, key, user, sleep=time.sleep):
    log = []
    for number in range(1, ATTEMPTS + 1):
        started = time.monotonic()
        try:
            text, finish, usage = call(key, user)
            log.append({"status": "ok", "seconds": round(time.monotonic() - started, 1)})
            return {"text": text, "finish": finish, "usage": usage, "attempts": log}
        except urllib.error.HTTPError as exc:
            log.append({"status": "http", "code": exc.code})
            if exc.code not in RETRY_STATUS:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            log.append({"status": type(exc).__name__})
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            log.append({"status": "bad_reply", "error": type(exc).__name__})
            break
        if number < ATTEMPTS:
            sleep(WAIT)
    return {"text": None, "attempts": log}


def run(pack, keys, call_gemini=gemini, call_groq=groq, sleep=time.sleep):
    return {"pack_sha256": hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest(),
            "gemini_full": attempt(call_gemini, keys["gemini"], json.dumps(pack["full"], ensure_ascii=False), sleep),
            "groq_no_context": attempt(call_groq, keys["groq"], json.dumps(pack["no_context"], ensure_ascii=False), sleep)}


def encrypt(report, cert_b64, cert_sha, output):
    cert = base64.b64decode(cert_b64, validate=True)
    if hashlib.sha256(cert).hexdigest() != cert_sha or b"PRIVATE KEY" in cert:
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
        if len(sys.argv) != 2 or os.environ.get("ANALYTICS_FREE_ACCOUNTS_APPROVED") != "true":
            raise ValueError("approval missing")
        pack = json.loads((HERE / "pack.json").read_text(encoding="utf-8"))
        report = run(pack, {"gemini": os.environ["GEMINI_FREE_API_KEY"], "groq": os.environ["GROQ_API_KEY"]})
        encrypt(report, os.environ["ANALYTICS_RECIPIENT_B64"], os.environ["ANALYTICS_RECIPIENT_SHA256"], sys.argv[1])
    except Exception as exc:
        print("PROBE stopped:", type(exc).__name__, file=sys.stderr)
        return 1
    print("PROBE", {k: [a.get("status") for a in report[k]["attempts"]] for k in ("gemini_full", "groq_no_context")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
