"""EXP-009 stage 1: sanitize one UK FTS OCDS release and extract tender fields.

Pure and offline. D3: parties[].contactPoint (names, e-mails, phones of people)
is removed before storage and before hashing. D4: extract() returns only the
fields present in the release; an absent or null field must not erase the
previous value. D9: notice_url only from documents[tenderNotice] on FTS.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

import normalize

NOTICE_URL_PREFIX = "https://www.find-tender.service.gov.uk/Notice/"
MAX_DESCRIPTION = 20000
MAX_RELEASE_BYTES = 65536
MAX_TEXT = 2000
TAGS = ("tender", "tenderUpdate")
CPV_RE = re.compile(r"^[0-9]{8}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# Columns of bohun_uk_intake.notices filled from the source (whitelist for SQL).
FIELD_COLUMNS = (
    "title", "description", "tender_status", "procurement_method", "main_category",
    "value_amount", "value_amount_gross", "value_currency", "tender_deadline",
    "tender_deadline_raw",
    "buyer_id", "buyer_name", "buyer_country", "cpv_codes", "lots_count",
    "above_threshold", "notice_url",
)


class FieldError(ValueError):
    pass


def sanitize(release):
    """Copy of the release without personal contact points; never mutates input."""
    if not isinstance(release, dict):
        raise FieldError("release_not_object")
    clean = copy.deepcopy(release)
    parties = clean.get("parties")
    if parties is not None:
        if not isinstance(parties, list):
            raise FieldError("bad_parties")
        for party in parties:
            if not isinstance(party, dict):
                raise FieldError("bad_party")
            party.pop("contactPoint", None)
    return clean


def release_hash(clean):
    return normalize._hash(clean)


def release_tag(release):
    tags = release.get("tag")
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise FieldError("bad_tags")
    found = [t for t in TAGS if t in tags]
    if len(found) != 1:
        return None  # award/planning/other or ambiguous: outside stage 1
    return found[0]


def parse_time(value, code):
    """RFC3339 with offset only; a naive time is not guessed."""
    if not isinstance(value, str) or not value.strip():
        raise FieldError(code)
    try:
        d = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise FieldError(code) from None
    if d.tzinfo is None:
        raise FieldError(code)
    return d.astimezone(timezone.utc)


def _text(value, code, cap=MAX_TEXT):
    if not isinstance(value, str):
        raise FieldError(code)
    value = value.strip()
    if len(value) > cap:
        raise FieldError(code + "_too_long")
    return value


def _money(value, code):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise FieldError(code)
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise FieldError(code) from None
    if not d.is_finite() or d < 0:
        raise FieldError(code)
    return d


def _cpv(tender):
    codes = set()
    nodes = [tender.get("classification")]
    for item in tender.get("items") or []:
        if not isinstance(item, dict):
            raise FieldError("bad_item")
        nodes.append(item.get("classification"))
        extra = item.get("additionalClassifications") or []
        if not isinstance(extra, list):
            raise FieldError("bad_classifications")
        nodes.extend(extra)
    for node in nodes:
        if node is None:
            continue
        if not isinstance(node, dict):
            raise FieldError("bad_classification")
        if node.get("scheme") == "CPV":
            code = str(node.get("id", "")).strip()
            if not CPV_RE.fullmatch(code):
                raise FieldError("bad_cpv")
            codes.add(code)
    return sorted(codes)


def extract(release):
    """Fields present in this release only (D4). Raises on malformed known fields."""
    tender = release.get("tender")
    if not isinstance(tender, dict):
        raise FieldError("tender_missing")
    out = {}

    def put(column, value):
        if value is not None and value != "":
            out[column] = value

    for column, key in (("title", "title"), ("tender_status", "status"),
                        ("procurement_method", "procurementMethod"),
                        ("main_category", "mainProcurementCategory")):
        if tender.get(key) is not None:
            put(column, _text(tender[key], "bad_" + column))
    if tender.get("description") is not None:
        put("description", _text(tender["description"], "bad_description", MAX_DESCRIPTION))

    value = tender.get("value")
    if value is not None:
        if not isinstance(value, dict):
            raise FieldError("bad_value")
        if value.get("amount") is not None:
            put("value_amount", _money(value["amount"], "bad_value_amount"))
        if value.get("amountGross") is not None:
            put("value_amount_gross", _money(value["amountGross"], "bad_value_amount_gross"))
        if value.get("currency") is not None:
            currency = _text(value["currency"], "bad_currency")
            if not CURRENCY_RE.fullmatch(currency):
                raise FieldError("bad_currency")
            put("value_currency", currency)

    period = tender.get("tenderPeriod")
    if period is not None:
        if not isinstance(period, dict):
            raise FieldError("bad_tender_period")
        if period.get("endDate") is not None:
            # Real data has date-only deadlines ("2026-10-26"): keep the source
            # string, fill the timestamp only when an offset makes it exact.
            raw = _text(period["endDate"], "bad_tender_deadline", 40)
            put("tender_deadline_raw", raw)
            try:
                put("tender_deadline", parse_time(raw, "bad_tender_deadline"))
            except FieldError:
                pass

    buyer = release.get("buyer")
    if buyer is not None:
        if not isinstance(buyer, dict):
            raise FieldError("bad_buyer")
        if buyer.get("id") is not None:
            put("buyer_id", _text(buyer["id"], "bad_buyer_id", 256))
        if buyer.get("name") is not None:
            put("buyer_name", _text(buyer["name"], "bad_buyer_name", 512))
        for party in release.get("parties") or []:
            if (isinstance(party, dict) and buyer.get("id") is not None
                    and party.get("id") == buyer.get("id")):
                address = party.get("address") or {}
                if isinstance(address, dict) and address.get("countryName") is not None:
                    put("buyer_country", _text(address["countryName"], "bad_buyer_country", 128))

    cpv = _cpv(tender)
    if cpv:
        out["cpv_codes"] = cpv
    lots = tender.get("lots")
    if lots is not None:
        if not isinstance(lots, list):
            raise FieldError("bad_lots")
        out["lots_count"] = len(lots)
    if tender.get("aboveThreshold") is not None:
        if not isinstance(tender["aboveThreshold"], bool):
            raise FieldError("bad_above_threshold")
        out["above_threshold"] = tender["aboveThreshold"]
    for doc in tender.get("documents") or []:
        if not isinstance(doc, dict):
            raise FieldError("bad_document")
        url = doc.get("url")
        if (doc.get("documentType") == "tenderNotice" and isinstance(url, str)
                and url.startswith(NOTICE_URL_PREFIX) and len(url) <= 300
                and not any(c.isspace() for c in url)):
            out["notice_url"] = url
            break
    if not set(out) <= set(FIELD_COLUMNS):
        raise FieldError("unknown_field_column")
    return out
