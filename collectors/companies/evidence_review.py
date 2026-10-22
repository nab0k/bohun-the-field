"""DISCOVERY-ENRICH-02: evidence review inside existing EXP-012, no DB writes.

Private local journal of *observations*, not an entity-resolution engine.
Source hints never become verified claims. Hash chain detects accidental edits,
but is NOT encryption or an authenticated signature.
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import stat
from urllib.parse import urlsplit

from .enrich_pilot import REPO_ROOT, SOURCE

FOCI = {"registry_identifier", "legal_entity_alias", "brand_vs_legal_entity",
        "subsidiary_vs_group", "lifecycle_review"}
KINDS = {"unknown", "brand", "corporate_group", "subsidiary", "legal_entity"}
LIFECYCLE = {"unknown", "active", "insolvency", "liquidation", "dissolved"}
HINT_FILE = pathlib.Path(__file__).parent / "data" / "enrich01_review20_hints.json"
MAX_CAPTURE_BYTES = 5 * 1024 * 1024


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _public_https(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("invalid source URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("only public HTTPS source URLs without credentials are permitted")
    host = parsed.hostname.lower()
    if (host in {"localhost", "localhost.localdomain"} or host.endswith(".local")
            or host.endswith(".internal") or host.endswith(".localhost")
            or host.replace(".", "").isdigit() or ":" in host):
        raise ValueError("local or numeric source host prohibited")
    return url


def _private_root(folder):
    folder = pathlib.Path(folder).expanduser()
    if not folder.is_absolute():
        raise ValueError("private evidence directory must be absolute")
    root = folder.resolve()
    if root == REPO_ROOT or REPO_ROOT in root.parents:
        raise ValueError("cannot write evidence within Git checkout")
    # Deny symlinked path components, not merely symlinked final file.
    for component in (folder, *folder.parents):
        if component.exists() and component.is_symlink():
            raise ValueError("symlinked evidence path prohibited")
    previous = os.umask(0o077)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise ValueError("evidence path is not a directory")
        os.chmod(root, 0o700)
    finally:
        os.umask(previous)
    return root


def load_hints():
    hints = json.loads(HINT_FILE.read_text(encoding="utf-8"))
    if hints.get("kind") != "unverified_source_hints":
        raise ValueError("hints incorrectly marked as evidence")
    entries = hints["entries"]
    if len(entries) != 20 or len({e["candidate_id"] for e in entries}) != 20:
        raise ValueError("expected exactly 20 distinct review cases")
    for entry in entries:
        if entry["review_focus"] not in FOCI or not entry["source_urls"]:
            raise ValueError("invalid review case")
        for url in entry["source_urls"]:
            _public_https(url)
    return entries


def review_packets(research_records, hints=None):
    """Build 20 link-only review packets; no inference of legal identity."""
    hints = load_hints() if hints is None else hints
    if len(research_records) != 81:
        raise ValueError("expected the frozen 81 research records")
    by_id = {int(r["candidate_id"]): r for r in research_records}
    if len(by_id) != 81:
        raise ValueError("duplicate frozen candidates")
    if len(hints) != 20 or len({e["candidate_id"] for e in hints}) != 20:
        raise ValueError("incorrect number of source hints")
    cases = []
    for entry in sorted(hints, key=lambda e: e["candidate_id"]):
        candidate_id = int(entry["candidate_id"])
        row = by_id.get(candidate_id)
        if row is None or row.get("source") != SOURCE:
            raise ValueError("source-hint candidate not present in frozen cohort")
        urls = [_public_https(url) for url in entry["source_urls"]]
        if entry["review_focus"] not in FOCI:
            raise ValueError("invalid review focus")
        cases.append({
            "candidate_id": candidate_id,
            "intake_status": row["original_status"],
            "source_country_source_only": row.get("reported_country_source_only"),
            "research_focus": entry["review_focus"],
            "source_links": [{"url": url, "state": "unfetched_hint", "quote_verified": False}
                             for url in urls],
            "identity_kind": "unknown", "lifecycle_status": "unknown",
            "official_site_found": False, "registry_identifier_found": False,
            "exhibitor_to_legal_entity_verified": False,
            "review_status": "unreviewed",
            "identity_confirmed": False, "publishable": False,
            "company_write_allowed": False,
        })
    return cases


def capture_receipt(case, url, raw_bytes, exact_quote, *, kind="unknown",
                    lifecycle="unknown", source_as_of=None):
    """Validate a user-provided source capture and quote, without fetching the URL.

    The source's legal assertions, if any, remain UNVERIFIED HYPOTHESES.
    Full response content is not written to the private evidence ledger.
    """
    if case.get("review_status") != "unreviewed" or case.get("identity_confirmed"):
        raise ValueError("source capture may not promote review status")
    if url not in {x["url"] for x in case["source_links"]}:
        raise ValueError("source URL must be a predeclared hint")
    _public_https(url)
    if kind not in KINDS or lifecycle not in LIFECYCLE:
        raise ValueError("unsupported claimed identity/lifecycle category")
    if not isinstance(raw_bytes, bytes) or not 1 <= len(raw_bytes) <= MAX_CAPTURE_BYTES:
        raise ValueError("capture must contain bounded source bytes")
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("source capture must be UTF-8 text") from exc
    if not isinstance(exact_quote, str) or not 8 <= len(exact_quote) <= 500 or exact_quote not in decoded:
        raise ValueError("exact quote is not present in captured bytes")
    if source_as_of:
        try:
            dt.date.fromisoformat(source_as_of)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid source-as-of date") from exc
    receipt = {
        "candidate_id": int(case["candidate_id"]),
        "source_url": url, "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "capture_bytes": len(raw_bytes), "quote": exact_quote,
        "quote_sha256": hashlib.sha256(exact_quote.encode("utf-8")).hexdigest(),
        "claimed_identity_kind": kind, "claimed_lifecycle": lifecycle,
        "source_as_of": source_as_of, "provenance": "provided_source_capture",
        "quote_present_in_capture": True,
        "registry_identifier_correlated": False,
        "exhibitor_identity_verified": False,
        "review_status": "unreviewed", "publication_allowed": False,
    }
    receipt["event_key"] = _digest({k: v for k, v in receipt.items()
                                      if k not in {"captured_at", "event_key"}})
    return receipt


def verify_ledger(path):
    """Detect truncation/accidental rewriting of append-only private JSONL.

    Does not provide cryptographic authenticity without a separately held key.
    """
    if not path.exists():
        return [], "0" * 64
    if path.is_symlink():
        raise ValueError("symlink ledger prohibited")
    seen = set()
    prev = "0" * 64
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for seq, line in enumerate(stream, start=1):
            record = json.loads(line)
            if (record.get("seq") != seq or record.get("previous_sha256") != prev
                    or record.get("sha256") != _digest({k: v for k, v in record.items()
                                                        if k != "sha256"})):
                raise ValueError("private evidence chain integrity failure")
            payload = record["receipt"]
            if payload.get("event_key") in seen:
                raise ValueError("duplicate private evidence event")
            seen.add(payload.get("event_key"))
            prev = record["sha256"]
            records.append(record)
    return records, prev


def append_private_receipt(root_dir, receipt):
    """Append or idempotently reuse an existing receipt; private file mode 0600.

    One local writer at a time. Remote concurrent writers require an actual
    transactional service and must not use this file journal.
    """
    if not isinstance(receipt, dict):
        raise ValueError("evidence receipt must be a dict")
    if (receipt.get("review_status") != "unreviewed" or
            receipt.get("exhibitor_identity_verified") is not False or
            receipt.get("registry_identifier_correlated") is not False or
            receipt.get("publication_allowed") is not False):
        raise ValueError("verified/publishable evidence is forbidden in research ledger")
    _public_https(receipt.get("source_url"))
    if (receipt.get("event_key") != _digest({k: v for k, v in receipt.items()
                                            if k not in {"event_key", "captured_at"}}) or
            receipt.get("quote_sha256") != hashlib.sha256(
                str(receipt.get("quote", "")).encode("utf-8")).hexdigest() or
            not receipt.get("quote_present_in_capture")):
        raise ValueError("receipt hash or quote metadata invalid")
    root = _private_root(root_dir)
    path = root / "evidence_chain.jsonl"
    existing, prev = verify_ledger(path)
    if any(x["receipt"]["event_key"] == receipt["event_key"] for x in existing):
        return {"status": "already_present", "count": len(existing)}
    record = {"seq": len(existing) + 1, "previous_sha256": prev, "receipt": receipt}
    record["sha256"] = _digest(record)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("evidence journal must be a regular file")
        if os.fstat(fd).st_uid != os.getuid():
            raise ValueError("evidence journal owned by another user")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "ab", closefd=False) as stream:
            stream.write(_canonical(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return {"status": "appended", "count": len(existing) + 1}


def persist_review_packets(root_dir, packets):
    """Create (never silently replace) unverified link-only packets."""
    root = _private_root(root_dir)
    path = root / "review20_unverified.jsonl"
    serialized = b"".join(_canonical(x) + b"\n" for x in packets)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != serialized:
            raise ValueError("existing review packets differ, refusing overwrite")
        return len(packets)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return len(packets)
