"""Document inputs for the experiment. All read-only.

- fixtures: JSON files committed under intelligence/fixtures/evidence_phase1/
- staging: rows already stored by the existing pilot in bohun_staging.inoreader_documents,
  read in a READ ONLY transaction (no writes, no schema changes)
- pdf: one public PDF converted with Docling; fetched with the pilot's own
  public-host and robots.txt checks (reused, not reimplemented)
"""
import datetime as dt
import hashlib
import importlib.util
import json
import os
import resource
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "intelligence/fixtures/evidence_phase1"
MIN_FULL_TEXT_CHARS = 300  # same floor as the existing pilot (ORIGINAL_TEXT_TOO_SHORT)


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def text_status(text):
    if not text or not text.strip():
        return "NO_TEXT"
    if len(text) < MIN_FULL_TEXT_CHARS:
        return "TOO_SHORT_NOT_FULL_TEXT"
    return "TEXT_PRESENT_COMPLETENESS_UNVERIFIED"


def load_fixtures(names=None):
    docs = []
    for path in sorted(FIXTURES.glob("*.json")):
        if names and path.stem not in names:
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        if "text" not in d:  # e.g. run-request.json
            continue
        docs.append({
            "document_id": "fixture:" + path.stem,
            "source_url": d["source_url"],
            "text": d["text"],
            "retrieved_at": d.get("retrieved_at"),
            "publication_date": d.get("publication_date"),
            "language": d.get("language", "en"),
            "provenance": d.get("provenance", "SYNTHETIC_TEST_FIXTURE"),
            "gold": d.get("gold"),
            "text_status": text_status(d["text"]),
        })
    return docs


def load_staging(limit=3, title_like=None):
    import psycopg
    conn = psycopg.connect(
        host=os.getenv("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.getenv("SUPABASE_DB_PORT", "5432")), dbname="postgres",
        user=os.getenv("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        password=os.environ["SUPABASE_DB_PASSWORD"], sslmode="require", connect_timeout=20,
    )
    with conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            if cur.fetchone()[0] != "on":
                raise RuntimeError("STAGING_READ_NOT_READ_ONLY")
            sql = ("SELECT document_id, final_url, title, published_at, fetched_at, "
                   "original_visible_text, original_content_sha256 FROM bohun_staging.inoreader_documents ")
            args = []
            if title_like:
                sql += "WHERE title ILIKE %s "
                args.append(f"%{title_like}%")
            sql += "ORDER BY fetched_at DESC LIMIT %s"
            args.append(int(limit))
            cur.execute(sql, args)
            rows = cur.fetchall()
    docs = []
    for doc_id, url, title, published, fetched, text, raw_sha in rows:
        docs.append({
            "document_id": doc_id, "source_url": url, "title": title, "text": text or "",
            "retrieved_at": fetched.isoformat() if fetched else None,
            "publication_date": published.date().isoformat() if published else None,
            "language": None, "provenance": "bohun_staging.inoreader_documents (read-only)",
            "original_raw_html_sha256": (raw_sha or "").strip(), "gold": None,
            "text_status": text_status(text or ""),
            # visible_text() of the publisher page includes navigation/boilerplate.
            "text_extraction": "pilot visible_text(html)",
        })
    return docs


def load_pilot_candidates(document_id):
    """Existing pilot output for one staged document (read-only), used as a baseline."""
    import psycopg
    conn = psycopg.connect(
        host=os.getenv("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.getenv("SUPABASE_DB_PORT", "5432")), dbname="postgres",
        user=os.getenv("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        password=os.environ["SUPABASE_DB_PASSWORD"], sslmode="require", connect_timeout=20,
    )
    with conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            if cur.fetchone()[0] != "on":
                raise RuntimeError("STAGING_READ_NOT_READ_ONLY")
            cur.execute("SELECT model_run_id, model, response_json, validation_json, usage_json, created_at "
                        "FROM bohun_staging.model_candidates WHERE document_id=%s ORDER BY created_at", (document_id,))
            rows = cur.fetchall()
    return [{"model_run_id": r[0], "model": r[1], "response": r[2], "validation": r[3], "usage": r[4],
             "created_at": r[5].isoformat() if r[5] else None} for r in rows]


def _pilot_module():
    path = ROOT / "scripts/inoreader-gemini-staging-pilot.py"
    spec = importlib.util.spec_from_file_location("bohun_staging_pilot", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_public_pdf(url, max_bytes=25_000_000):
    pilot = _pilot_module()
    pilot.assert_public_web_url(url)
    p = urllib.parse.urlparse(url)
    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(urllib.parse.urlunparse((p.scheme, p.netloc, "/robots.txt", "", "", "")))
    robots.read()
    if not robots.can_fetch(pilot.USER_AGENT, url):
        raise RuntimeError("ROBOTS_DISALLOWS_FETCH")
    req = urllib.request.Request(url, headers={"User-Agent": pilot.USER_AGENT, "Accept": "application/pdf"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        pilot.assert_public_web_url(resp.geturl())
        if resp.headers.get_content_type() != "application/pdf":
            raise RuntimeError("NOT_PDF")
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise RuntimeError("PDF_TOO_LARGE")
    return raw


def load_pdf_with_docling(url, workdir):
    """Convert one PDF; returns (document, measurements). Docling is optional."""
    from docling.document_converter import DocumentConverter
    raw = fetch_public_pdf(url)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    pdf_path = workdir / "input.pdf"
    pdf_path.write_bytes(raw)
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    converter = DocumentConverter()
    t_init = time.perf_counter() - t0
    result = converter.convert(str(pdf_path))
    t_conv = time.perf_counter() - t0 - t_init
    text = result.document.export_to_markdown()
    measurements = {
        "docling_init_s": round(t_init, 2), "docling_convert_s": round(t_conv, 2),
        "max_rss_mb_before": round(rss0 / 1024, 1),
        "max_rss_mb_after": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "pdf_bytes": len(raw), "pages": len(getattr(result.document, "pages", {}) or {}),
        "text_chars": len(text),
    }
    doc = {
        "document_id": "pdf:" + hashlib.sha256(raw).hexdigest()[:24], "source_url": url,
        "text": text, "retrieved_at": _now(), "publication_date": None, "language": None,
        "provenance": "public PDF via Docling export_to_markdown", "gold": None,
        "pdf_sha256": hashlib.sha256(raw).hexdigest(), "text_status": text_status(text),
        "text_extraction": "docling export_to_markdown",
    }
    return doc, measurements
