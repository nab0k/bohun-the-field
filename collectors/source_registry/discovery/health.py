"""Exact-origin delivery evidence. No writes and no connection on import."""
from .policy import Problem, digest, epoch, require, stamp
import statistics


def evaluate(profile, receipt, now):
    connection = profile.get("connection", {})
    if not connection.get("membership_verified") or not connection.get("folder_verified"):
        return {"state": "UNKNOWN", "lifecycle": profile.get("lifecycle", "VALIDATED"),
                "reason": "CONNECTION_NOT_PROVEN", "cycles": []}
    previous = profile.get("delivery", {})
    if receipt is None:
        return {**previous, "state": "UNKNOWN", "lifecycle": profile.get("lifecycle", "CONNECTED"),
                "reason": "DELIVERY_NOT_CHECKED", "cycles": previous.get("cycles", []),
                "checked_at": previous.get("checked_at")}
    cycles = previous.get("cycles", [])
    expected = "feed/" + profile["feed_url"]
    verified = []
    if receipt is not None:
        require(isinstance(receipt, dict) and receipt.get("read_only") is True and
                receipt.get("origin_stream_id") == expected and
                receipt.get("folder") == connection["folder"], "DELIVERY_SCOPE")
        require(isinstance(receipt.get("records"), list) and len(receipt["records"]) <= 20, "DELIVERY_CAP")
        for record in receipt["records"]:
            require(isinstance(record, dict), "DELIVERY_RECORD")
            saved = epoch(record.get("saved_at"))
            require(saved <= now + 60, "DELIVERY_FUTURE")
            if record.get("origin_stream_id") == expected and record.get("folder") == connection["folder"] \
                    and record.get("id") and saved > connection["connected_at"]:
                verified.append({"id": str(record["id"]), "saved_at": saved,
                                 "cycle_id": record.get("collector_cycle_id")})
    known_ids = set(previous.get("record_ids", []))
    new = [r for r in verified if r["id"] not in known_ids]
    known_ids.update(r["id"] for r in new)
    for cycle in set(r["cycle_id"] for r in new if isinstance(r["cycle_id"], str) and r["cycle_id"]):
        rows = [r for r in new if r["cycle_id"] == cycle]
        # Re-observing a cycle never turns one delivery into two.
        if cycle not in {c["id"] for c in cycles}:
            cycles.append({"id": cycle, "first_saved": min(r["saved_at"] for r in rows),
                           "last_saved": max(r["saved_at"] for r in rows)})
    cycles = sorted(cycles, key=lambda c: c["first_saved"])[-20:]
    distinct = any(b["first_saved"] > a["last_saved"] for i, a in enumerate(cycles) for b in cycles[i + 1:])
    if distinct:
        state, lifecycle = "OK", "ACTIVE"
    elif known_ids:
        state, lifecycle = "FIRST_DELIVERY_PROVEN", "CONNECTED"
    else:
        state, lifecycle = ("WAITING_DELIVERY" if now - connection["connected_at"] <= 86400 else "REVIEW_DELIVERY_TIMEOUT"), "CONNECTED"
    last_saved = max([r["saved_at"] for r in verified] + [previous.get("last_saved_at", 0)])
    dates = sorted(set(e["published_epoch"] for e in profile.get("entries", []) if e.get("published_epoch")))
    intervals = [b - a for a, b in zip(dates, dates[1:]) if b > a]
    quiet_after = max(86400, 3 * statistics.median(intervals)) if intervals else 86400
    if known_ids and now - last_saved > quiet_after:
        state = "QUIET"
    return {"state": state, "lifecycle": lifecycle, "record_ids": sorted(known_ids)[-1000:],
            "cycles": cycles, "checked_at": now,
            "last_saved_at": last_saved,
            "reason": "COLLECTOR_CYCLE_UNKNOWN" if known_ids and not cycles else state}


def read_delivery(connect, settings, profile):
    """Injected connector or explicit installed psycopg only; ordinary SELECT path."""
    source_id = settings.get("transport_source_id")
    require(type(source_id) is int and source_id > 0, "TRANSPORT_SOURCE_ID_REQUIRED")
    connection = profile["connection"]
    expected = "feed/" + profile["feed_url"]
    try:
        with connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute("SET LOCAL statement_timeout = '5s'")
                    cur.execute("SELECT name,url FROM intelligence.sources WHERE id=%s", (source_id,))
                    identity = cur.fetchone()
                    require(identity is not None and identity[0] == "Inoreader BOHUN" and
                            identity[1] == "https://www.inoreader.com/", "TRANSPORT_IDENTITY")
                    cur.execute("SELECT id::text, to_jsonb(i)->>'discovered_at', "
                                "raw_data->>'origin_stream_id', raw_data->>'folder' "
                                "FROM intelligence.items i WHERE source_id=%s "
                                "AND raw_data->>'origin_stream_id'=%s AND raw_data->>'folder'=%s "
                                "AND (to_jsonb(i)->>'discovered_at')::timestamptz > %s::timestamptz "
                                "ORDER BY discovered_at DESC LIMIT 20",
                                (source_id, expected, connection["folder"], stamp(connection["connected_at"])))
                    rows = cur.fetchmany(20)
                    # Current collector doesn't persist an independently proven cycle ID.
                    # Do not invent one from this reader's own run or timestamp.
                    records = [{"id": r[0], "saved_at": r[1], "origin_stream_id": r[2],
                                "folder": r[3], "collector_cycle_id": None} for r in rows]
                    return {"read_only": True, "origin_stream_id": expected,
                            "folder": connection["folder"], "records": records}
    except Problem:
        raise
    except Exception:
        raise Problem("DELIVERY_READ_FAILED") from None


def optional_connect(settings):
    """No automatic env/.env lookup, dependency installation, or credential discovery."""
    require(settings.get("enabled") is True and isinstance(settings.get("connection"), dict), "DB_DISABLED")
    params = settings["connection"]
    require(set(params) <= {"host", "port", "dbname", "user", "password", "sslmode", "connect_timeout"}
            and params.get("sslmode") == "verify-full" and params.get("connect_timeout", 20) <= 20,
            "DB_CONNECTION_POLICY")
    try:
        import psycopg
    except ImportError:
        raise Problem("DB_DRIVER_UNAVAILABLE") from None
    return psycopg.connect(**params)
