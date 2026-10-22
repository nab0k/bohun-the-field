"""EXP-012 DISCOVERY-ENRICH-03: HTTPS source captures + CMS AES-GCM archive.

Read-only public sources. This module does not write company/Supabase data,
does not verify legal identity, and never uploads plaintext to artifacts.
Recipient's RSA private key is deliberately NOT held by this module.
"""
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import shutil
import ssl
import stat
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .evidence_review import _private_root, _public_https, load_hints, MAX_CAPTURE_BYTES
from .enrich_pilot import REPO_ROOT

HERE = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CERT = HERE / "scripts" / "exp013_cms.crt"
MAX_TOTAL = 45 * 1024 * 1024
MAX_FILES = 80
_ALLOWED_EXT = {".html", ".htm", ".txt", ".pdf"}


class SourceCaptureError(RuntimeError):
    pass


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, first_host):
        super().__init__()
        self.first_host = first_host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_https(newurl)
        if urlsplit(newurl).hostname.lower() != self.first_host:
            raise SourceCaptureError("cross-host redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_https_exact(url, *, timeout=12, limit=MAX_CAPTURE_BYTES, opener_factory=None):
    """Fetch only the predeclared exact HTTPS URL; fail closed on cross-host redirects.

    An allow-list of URLs comes from the checked-in 20-case manifest.
    The code does not spider links or follow arbitrary sites.
    """
    _public_https(url)
    if not 0 < timeout <= 30 or not 0 < limit <= MAX_CAPTURE_BYTES:
        raise ValueError("source fetch bounds invalid")
    hostname = urlsplit(url).hostname.lower()
    opener = (opener_factory(hostname) if opener_factory else
              urllib.request.build_opener(_SameHostRedirect(hostname)))
    request = urllib.request.Request(url, headers={
        "User-Agent": "BohundefenceEvidenceResearch/0.1 (+https://bohundefence.com)",
        "Accept": "text/html,text/plain,application/pdf;q=0.5"
    })
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        _public_https(final_url)
        if urlsplit(final_url).hostname.lower() != hostname:
            raise SourceCaptureError("response final host differs from allow-list")
        status = getattr(response, "status", 200)
        if status != 200:
            raise SourceCaptureError("source HTTP status not 200")
        ctype = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype not in {"text/html", "text/plain", "application/xhtml+xml", "application/pdf"}:
            raise SourceCaptureError("unsupported source content type")
        body = response.read(limit + 1)
    if not body or len(body) > limit:
        raise SourceCaptureError("source empty or exceeds maximum bytes")
    return body, ctype, final_url


def collect_twenty(private_dir, *, hints=None, fetcher=None):
    """One successful source capture per case, as available; missing cases stay OPEN.

    Returned stats are counts only. The private manifest has URLs/IDs/checksums,
    not automatically extracted legal claims or verified company facts.
    """
    cases = load_hints() if hints is None else hints
    if len(cases) != 20 or len({c["candidate_id"] for c in cases}) != 20:
        raise ValueError("expected twenty distinct candidate source hints")
    root = _private_root(private_dir)
    snapshots = _private_root(root / "source_snapshots")
    if any(snapshots.iterdir()):
        raise ValueError("source snapshots directory must be empty for this run")
    results = []
    getter = fetcher or fetch_https_exact
    for case in sorted(cases, key=lambda x: x["candidate_id"]):
        errors = []
        result = {"candidate_id": int(case["candidate_id"]),
                  "research_focus": case["review_focus"],
                  "state": "source_unavailable",
                  "identity_verified": False, "legal_status_verified": False}
        for url in case["source_urls"]:
            _public_https(url)
            try:
                raw, ctype, final_url = getter(url)
                if not isinstance(raw, bytes) or len(raw) > MAX_CAPTURE_BYTES or len(raw) == 0:
                    raise SourceCaptureError("invalid snapshot bytes")
                if ctype not in {"text/html", "text/plain", "application/xhtml+xml", "application/pdf"}:
                    raise SourceCaptureError("unsupported source content type")
                if urlsplit(final_url).hostname.lower() != urlsplit(url).hostname.lower():
                    raise SourceCaptureError("unsafe final URL")
                suffix = ".pdf" if ctype == "application/pdf" else ".html"
                digest = hashlib.sha256(raw).hexdigest()
                relative = "source_snapshots/" + str(case["candidate_id"]) + "-" + digest + suffix
                snapshot = root / relative
                with snapshot.open("xb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(snapshot, 0o600)
                result.update(state="snapshot_captured_unverified",
                              original_url=url, final_url=final_url, mime=ctype,
                              bytes=len(raw), content_sha256=digest, archive_path=relative,
                              captured_at=dt.datetime.now(dt.timezone.utc).isoformat())
                break
            except (ValueError, SourceCaptureError, OSError, urllib.error.URLError) as exc:
                errors.append(type(exc).__name__)
        if result["state"] != "snapshot_captured_unverified":
            result["failure_categories"] = errors[:3]
        results.append(result)
    manifest_file = root / "source_snapshot_manifest.json"
    if manifest_file.exists() or manifest_file.is_symlink():
        raise ValueError("existing source manifest must not be replaced")
    with manifest_file.open("x", encoding="utf-8") as output:
        json.dump({"version": 1, "source": "DISCOVERY-ENRICH-03",
                   "legal_identity_verified": False, "results": results},
                  output, sort_keys=True, ensure_ascii=False, indent=2)
    os.chmod(manifest_file, 0o600)
    return {"total": len(results),
            "captured": sum(x["state"] == "snapshot_captured_unverified" for x in results),
            "unavailable": sum(x["state"] != "snapshot_captured_unverified" for x in results),
            "legal_identities_verified": 0, "new_company_rows": 0}


def encrypted_export(private_dir, cipher_dir, *, cert_path=DEFAULT_CERT):
    """Archive approved private research files and CMS AES-256-GCM encrypt in memory.

    No tar/plaintext intermediate files on disk. Persistent remote storage is
    intentionally out of scope and must be separately authorized/configured.
    """
    root = pathlib.Path(private_dir).expanduser()
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError("private source directory required")
    resolved = root.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("source evidence inside Git checkout forbidden")
    dest = _private_root(cipher_dir)
    if dest == resolved or dest in resolved.parents or resolved in dest.parents:
        raise ValueError("encrypted output must be a distinct directory")
    cert = pathlib.Path(cert_path)
    if not cert.is_file():
        raise ValueError("recipient certificate is unavailable")
    cert_bytes = cert.read_bytes()
    if b"BEGIN CERTIFICATE" not in cert_bytes or b"PRIVATE KEY" in cert_bytes:
        raise ValueError("recipient must be a public X509 certificate")
    subprocess.run(["openssl", "x509", "-checkend", "0", "-noout", "-in", str(cert)],
                   check=True, capture_output=True, timeout=15)

    file_entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink inside evidence directory prohibited")
        if path.is_dir():
            continue
        if not path.is_file() or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("non-regular evidence item prohibited")
        rel = path.relative_to(root).as_posix()
        if not (path.name.startswith(("research", "aggregate"))
                or path.name in {"source_snapshot_manifest.json",
                                 "review20_unverified.jsonl", "evidence_chain.jsonl"}
                or rel.startswith("source_snapshots/") and path.suffix in _ALLOWED_EXT):
            raise ValueError("unexpected evidence file in bundle")
        if path.stat().st_size > MAX_CAPTURE_BYTES:
            raise ValueError("single evidence file too large")
        file_entries.append((rel, path))
    if not file_entries or len(file_entries) > MAX_FILES:
        raise ValueError("no evidence or too many source files")
    if sum(p.stat().st_size for _, p in file_entries) > MAX_TOTAL:
        raise ValueError("evidence source bytes exceed bound")

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for rel, path in file_entries:
            data = path.read_bytes()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mode = 0o600
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    plaintext = stream.getvalue()
    try:
        args = ["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm",
                "-outform", "DER", str(cert)]
        completed = subprocess.run(args, input=plaintext, capture_output=True,
                                   check=True, timeout=40)
        cipher = completed.stdout
    finally:
        del plaintext
        stream.close()
    if not cipher:
        raise RuntimeError("CMS encryption produced no ciphertext")
    digest = hashlib.sha256(cipher).hexdigest()
    outfile = dest / ("evidence-" + digest + ".cms.der")
    fd = os.open(outfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as file:
            file.write(cipher)
            file.flush()
            os.fsync(file.fileno())
    finally:
        os.close(fd)
    report = {"envelope": "CMS-AES-256-GCM", "cipher_sha256": digest,
              "cipher_bytes": len(cipher),
              "file_count": len(file_entries),
              "recipient_cert_sha256": hashlib.sha256(cert_bytes).hexdigest(),
              "identity_confirmations": 0, "company_writes": 0,
              "archive_storage": "local_encrypted_only",
              "durable_remote_copy_verified": False}
    manifest = dest / ("manifest-" + digest + ".json")
    with manifest.open("x", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    os.chmod(manifest, 0o600)
    return report


def main(argv=None):
    """Public-source capture and recipient encryption. Logs only aggregate counts."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", required=True, type=pathlib.Path)
    parser.add_argument("--encrypted-dir", required=True, type=pathlib.Path)
    parser.add_argument("--cert", type=pathlib.Path, default=DEFAULT_CERT)
    parser.add_argument("--capture20", action="store_true")
    args = parser.parse_args(argv)
    if not args.capture20:
        parser.error("only bounded --capture20 is currently supported")
    findings = collect_twenty(args.private_dir)
    archive = encrypted_export(args.private_dir, args.encrypted_dir, cert_path=args.cert)
    # No private URLs, company names, LEIs or source bytes in stdout.
    print(json.dumps({
        "stage": "DISCOVERY-ENRICH-03", "source_capture": findings,
        "encrypted_archive": archive, "verified_identities": 0,
        "legal_lifecycle_verified": 0, "published": 0
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
