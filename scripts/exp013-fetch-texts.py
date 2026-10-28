#!/usr/bin/env python3
"""EXP-013 stage A: fetch Inoreader texts for the local analyst stand and encrypt them (OpenSSL CMS).

One owner-approved run (D-20261019-EXP013-G2G3), n=100, reading-list. The plaintext JSONL exists only in a
temporary file of the runner and is deleted after encryption with the public certificate
scripts/exp013_cms.crt (the private key stays on the owner's Mac). stdout carries aggregate numbers only.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("probe", HERE / "inoreader-fulltext-probe.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

FIELDS_NOTE = "id,title,url,domain,feed,published,content_html,text_len,class"


def record(item):
    kind, length, _ = probe.classify(item)
    url = next((l.get("href") for l in (item.get("canonical") or []) + (item.get("alternate") or [])
                if l.get("href")), None)
    return {"id": item.get("id"), "title": item.get("title"), "url": url, "domain": probe.domain(item),
            "feed": (item.get("origin") or {}).get("title"), "published": item.get("published"),
            "content_html": (item.get("summary") or {}).get("content"), "text_len": length, "class": kind}


def encrypt(plain_path, cert, out_path):
    subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-cbc", "-in", str(plain_path),
                    "-outform", "DER", "-out", str(out_path), str(cert)], check=True, capture_output=True)


def build(items, cert, out_dir):
    """Write texts.cms.der + manifest.json into out_dir; return the public summary (numbers only)."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [record(i) for i in items]
    fd, tmp = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        encrypt(tmp, cert, out_dir / "texts.cms.der")
    finally:
        os.remove(tmp)
    cipher = (out_dir / "texts.cms.der").read_bytes()
    classes = {}
    for r in rows:
        classes[r["class"]] = classes.get(r["class"], 0) + 1
    manifest = {"records": len(rows), "classes": classes, "fields": FIELDS_NOTE,
                "cipher_sha256": hashlib.sha256(cipher).hexdigest(), "cipher_bytes": len(cipher),
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cert", default=str(HERE / "exp013_cms.crt"))
    ap.add_argument("-n", type=int, default=100)
    args = ap.parse_args(argv)
    keys = ("INOREADER_CLIENT_ID", "INOREADER_CLIENT_SECRET", "INOREADER_REFRESH_TOKEN")
    env = {k: os.environ.get(k) for k in keys}
    if not all(env.values()):
        print(json.dumps({"status": "error", "error": "missing INOREADER_* secrets"}))
        return 1
    token = probe._post(probe.TOKEN, {"client_id": env["INOREADER_CLIENT_ID"],
                                      "client_secret": env["INOREADER_CLIENT_SECRET"],
                                      "refresh_token": env["INOREADER_REFRESH_TOKEN"],
                                      "grant_type": "refresh_token"})
    access = token.get("access_token")
    del token
    if not access:
        print(json.dumps({"status": "error", "error": "no access_token"}))
        return 1
    url = (probe.API + "stream/contents/" + urllib.parse.quote(probe.STREAM, safe="/") + "?"
           + urllib.parse.urlencode({"n": min(args.n, 100)}))
    items = probe._get(url, access).get("items") or []
    manifest = build(items, args.cert, args.out)
    print(json.dumps({"status": "ok", **manifest}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
