"""Take one bounded C3-07 snapshot and encrypt it for local review."""

from argparse import Namespace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from collectors.kb_pilot.pilot import select_input
from collectors.synthesis.live import connect

CERT_SHA = "be212c133bee03f63b678a203975c264122b8c8efe4dd9e4d269222cf410df32"


def snapshot(cur, report_path, today):
    cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    cur.execute("SET LOCAL statement_timeout = '20s'")
    args = Namespace(source="items", day_from=(today - timedelta(days=1)).isoformat(),
                     day_to=today.isoformat(), new_only=True, limit=80)
    select_input(cur, args, report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    ids = report["selected_item_ids"] + [row["item_id"] for row in report["exclusions"]]
    if len(set(ids)) != len(ids) or len(ids) != report["window_items"]:
        raise ValueError("input audit accounting mismatch")
    sample = [row for row in report["exclusions"]
              if row["reason"] not in {"already_processed_source", "deferred_by_limit"}]
    sample.sort(key=lambda row: sha256(f"C3-07|{today}|{row['item_id']}".encode()).hexdigest())
    sample = sample[:30]
    if sample:
        cur.execute("""SELECT id, title, url, published_at FROM intelligence.items
                        WHERE id = ANY(%s)""", ([row["item_id"] for row in sample],))
        meta = {iid: {"title": title, "url": url, "published_at": date.isoformat()}
                for iid, title, url, date in cur.fetchall()}
        if len(meta) != len(sample):
            raise ValueError("sample metadata mismatch")
        sample = [{**row, **meta[row["item_id"]]} for row in sample]
    return {**report, "sample": sample, "sample_count": len(sample)}


def main(cert, output):
    if sha256(cert.read_bytes()).hexdigest() != CERT_SHA:
        raise ValueError("unexpected recipient certificate")
    subprocess.run(["openssl", "x509", "-in", str(cert), "-checkend", "300", "-noout"],
                   check=True, stdout=subprocess.DEVNULL)
    with TemporaryDirectory() as tmp:
        with connect() as conn, conn.cursor() as cur:
            report = snapshot(cur, Path(tmp) / "input.json", datetime.now(timezone.utc).date())
        payload = json.dumps(report, ensure_ascii=False).encode()
        with output.open("xb") as result:
            subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-gcm",
                            "-outform", "DER", str(cert)], input=payload, stdout=result, check=True)
    print(f"Encrypted audit ready. Sample size {report['sample_count']}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
    except Exception as exc:
        print(f"C307_AUDIT_FAILED {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
