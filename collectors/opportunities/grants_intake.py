"""Pure Grants.gov candidate mapping and BLOCKED, non-executing SQL plans."""
from html.parser import HTMLParser
import json
import re

from collectors.source_registry.onboarding.contracts import canonical, digest, require, sha256

CONTRACT = "bohun-grant-candidate-v1"
PROJECT = "tecrucawzukxqtchpxrh"
LINK = "link_6ada46aa3bd48191a5af0066e127659c"
SOURCE = "grants.gov"
ENDPOINT = "https://api.grants.gov/v1/api/fetchOpportunity"
MAX_TEXT = 65536
APPROVED_QUERY = {"rows": 5, "keyword": "defense", "oppStatuses": "forecasted|posted"}
PROJECTION_FIELDS = set("""projection_version source_namespace native_id record_kind status detail_kind title number agency_name agency_name_status
agency_code open_date_raw close_date_raw description posting_date_raw response_date_raw award_ceiling_raw
award_floor_raw funding_instruments eligibility funding_categories currency timezone field_presence revision_observed
revision_ordering description_extent""".split())


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object"}:
            self.suppressed += 1
        elif not self.suppressed:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object"}:
            self.suppressed = max(0, self.suppressed - 1)
        elif not self.suppressed:
            self.parts.append(" ")

    def handle_data(self, text):
        if not self.suppressed:
            self.parts.append(text)


def clean(value):
    require(isinstance(value, str) and len(value.encode("utf-8")) <= MAX_TEXT and "\0" not in value,
            "TEXT_BOUND_OR_TYPE")
    parser = _Text()
    parser.feed(value)
    text = " ".join("".join(parser.parts).split())
    text = re.sub(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                  "[REDACTED_EMAIL]", text)
    text = re.sub(r"(?<!\w)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\w)",
                  "[REDACTED_PHONE]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED_TOKEN]", text)
    return text


def _field(obj, key, states, target=None):
    name = target or key
    if key not in obj:
        states[name] = "MISSING"
        return None
    value = obj[key]
    if value is None:
        states[name] = "NULL"
        return None
    if not isinstance(value, str):
        states[name] = "INVALID"
        return None
    states[name] = "EMPTY" if not value.strip() else "PRESENT"
    return clean(value)


def _options(obj, key, states):
    if key not in obj or obj[key] is None:
        states[key] = "MISSING" if key not in obj else "NULL"
        return None
    value = obj[key]
    if not isinstance(value, list) or len(value) > 100:
        states[key] = "INVALID"
        return None
    result = []
    for row in value:
        if not isinstance(row, dict):
            states[key] = "INVALID"
            return None
        item = {}
        for name in ("id", "description"):
            v = row.get(name)
            if v is None or (isinstance(v, (str, int)) and type(v) is not bool):
                item[name] = clean(str(v)) if v is not None else None
            else:
                states[key] = "INVALID"
                return None
        result.append(item)
    states[key] = "PRESENT" if result else "EMPTY"
    return result


def native_id(value):
    require(type(value) in (str, int), "INVALID_NATIVE_ID")
    s = str(value)
    require(re.fullmatch(r"[1-9][0-9]{0,15}", s), "INVALID_NATIVE_ID")
    return s


def project_record(hit, detail):
    """No raw API objects escape this allowlist. Descriptions remain unverified projections."""
    require(isinstance(hit, dict) and isinstance(detail, dict), "RECORD_SHAPE")
    ident = native_id(hit.get("id"))
    require(ident == native_id(detail.get("id")), "SEARCH_DETAIL_ID_MISMATCH")
    require(isinstance(hit.get("oppStatus"), str) and hit["oppStatus"] in {"posted", "forecasted"}, "NONPUBLIC_STATUS")
    for name in ("synopsis", "forecast"):
        if name in detail:
            require(detail[name] is None or isinstance(detail[name], dict), "DETAIL_CONTAINER")
    if "draftMode" in detail:
        require(detail["draftMode"] == "N" or detail["draftMode"] is False, "DRAFT_OR_UNKNOWN_FLAG")
    if "listed" in detail:
        require(detail["listed"] == "L", "UNLISTED_OR_UNKNOWN_FLAG")
    states = {}
    kind = "synopsis" if isinstance(detail.get("synopsis"), dict) and detail["synopsis"] else "forecast"
    body = detail.get(kind)
    if body is None:
        body = {}
    require(isinstance(body, dict), "DETAIL_CONTAINER")
    # Live evidence: synopsis.agencyName can contain a grantor person's name.
    # Do not promote that field into an organizational identity or retain the name.
    agency_value = body.get("agencyName")
    states["agencyName"] = "MISSING" if "agencyName" not in body else "NULL" if agency_value is None else (
        "EMPTY" if agency_value == "" else "PRESENT" if isinstance(agency_value, str) else "INVALID")
    result = {"projection_version": "grants-projection-v2", "source_namespace": SOURCE, "native_id": ident,
              "record_kind": "FUNDING_OPPORTUNITY", "status": hit["oppStatus"], "detail_kind": kind,
              "title": _field(detail, "opportunityTitle", states, "title"),
              "number": _field(detail, "opportunityNumber", states, "number"),
              "agency_name": None, "agency_name_status": "UNKNOWN_UNRELIABLE_SOURCE_FIELD",
              "agency_code": _field(detail, "owningAgencyCode", states, "agency_code"),
              "open_date_raw": _field(hit, "openDate", states, "open_date_raw"),
              "close_date_raw": _field(hit, "closeDate", states, "close_date_raw"),
              "description": _field(body, "synopsisDesc" if kind == "synopsis" else "forecastDesc",
                                    states, "description"),
              "posting_date_raw": _field(body, "postingDate", states, "posting_date_raw"),
              "response_date_raw": _field(body, "responseDateDesc", states, "response_date_raw"),
              "award_ceiling_raw": _field(body, "awardCeiling", states, "award_ceiling_raw"),
              "award_floor_raw": _field(body, "awardFloor", states, "award_floor_raw"),
              "funding_instruments": _options(body, "fundingInstruments", states),
              "eligibility": _options(body, "applicantTypes", states),
              "funding_categories": _options(body, "fundingActivityCategories", states),
              "currency": "UNKNOWN", "timezone": "UNKNOWN", "field_presence": states}
    require(isinstance(result["title"], str) and bool(result["title"].strip()), "TITLE_MISSING_OR_INVALID")
    revision = detail.get("revision")
    states["revision"] = "MISSING" if "revision" not in detail else "NULL" if revision is None else (
        "PRESENT" if type(revision) in (str, int) else "INVALID")
    result["revision_observed"] = clean(str(revision)) if states["revision"] == "PRESENT" else None
    result["revision_ordering"] = "UNVERIFIED"
    result["description_extent"] = "SANITIZED_API_DESCRIPTION" if result["description"] else "NO_DESCRIPTION"
    require("INVALID" not in states.values(), "UNSUPPORTED_FIELD_TYPE")
    return result


def candidate(projection, response_hash, *, query):
    require(isinstance(projection, dict) and set(projection) == PROJECTION_FIELDS, "PROJECTION_FIELDS")
    native_id(projection["native_id"])
    require(projection["source_namespace"] == SOURCE, "SOURCE_NAMESPACE")
    require(projection["projection_version"] == "grants-projection-v2" and projection["agency_name"] is None
            and projection["agency_name_status"] == "UNKNOWN_UNRELIABLE_SOURCE_FIELD", "AGENCY_IDENTITY_UNVERIFIED")
    require(projection["record_kind"] == "FUNDING_OPPORTUNITY" and projection["status"] in {"posted", "forecasted"}
            and projection["currency"] == "UNKNOWN" and projection["timezone"] == "UNKNOWN", "PROJECTION_CONTRACT")
    require(isinstance(query, dict) and set(query) == set(APPROVED_QUERY), "QUERY_FIELDS")
    require(re.fullmatch(r"[0-9a-f]{64}", response_hash), "RESPONSE_HASH")
    content = canonical(projection).decode("utf-8")
    content_hash = sha256(content.encode())
    document_id = "grants.gov:" + projection["native_id"] + ":" + content_hash
    payload = {"contract_version": CONTRACT, "candidate": projection,
               "provenance": {"source_profile": "SC-GRANTS-01", "source_slot": "S04",
                              "document_id": document_id, "content_sha256": content_hash,
                              "response_sha256": response_hash, "original_response_bytes_retained": False,
                              "original_url": ENDPOINT, "discovery_query": dict(query),
                              "landing_url": "https://www.grants.gov/search-results-detail/" + projection["native_id"],
                              "landing_url_validation": "UNVERIFIED_UI_LINK"},
               "qa_status": "HOLD", "public_release": False, "rights": "UNKNOWN",
               "relevance": "UNVERIFIED", "coverage": "UNVERIFIED", "internal_only": True,
               "limitations": ["KEYWORD_SAMPLE_FIRST_PAGE", "SOURCE_RESPONSE_PROJECTION_ONLY",
                               "API_DESCRIPTION_NOT_FULL_ANNOUNCEMENT", "NO_VERIFIED_REVISION_ORDERING",
                               "SEARCH_AND_DETAIL_NOT_ATOMIC", "AGENCY_NAME_UNVERIFIED"]}
    packet_id = digest([document_id, CONTRACT])
    return {"document_id": document_id, "source_id": SOURCE, "external_id": projection["native_id"],
            "content_sha256": content_hash, "raw_content": content, "media_type": "application/json",
            "original_url": ENDPOINT, "packet_id": packet_id, "contract_version": CONTRACT,
            "payload": payload, "payload_sha256": digest(payload), "qa_status": "HOLD",
            "public_release": False, "evidence_count": 0, "claim_count": 0}


def validate_candidate(row):
    require(isinstance(row, dict) and set(row) == {"document_id", "source_id", "external_id", "content_sha256",
            "raw_content", "media_type", "original_url", "packet_id", "contract_version", "payload",
            "payload_sha256", "qa_status", "public_release", "evidence_count", "claim_count"}, "CANDIDATE_FIELDS")
    p = row["payload"]
    require(isinstance(p, dict) and isinstance(p.get("candidate"), dict) and isinstance(p.get("provenance"), dict),
            "CANDIDATE_PAYLOAD")
    rebuilt = candidate(p["candidate"], p["provenance"]["response_sha256"], query=p["provenance"]["discovery_query"])
    require(row == rebuilt, "CANDIDATE_MISMATCH")


def prepare_plan(rows, *, fetched_at, run_id, source_status):
    require(isinstance(rows, list) and len(rows) <= 5, "ROW_LIMIT")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id), "RUN_ID")
    from collectors.source_registry.onboarding.contracts import timestamp
    timestamp(fetched_at)
    seen, outcomes, unique = {}, [], []
    for row in rows:
        validate_candidate(row)
        require(row["payload"]["provenance"]["discovery_query"] == APPROVED_QUERY, "PLAN_QUERY_SCOPE")
        key = row["external_id"]
        if key in seen:
            require(seen[key] == row["content_sha256"], "KEY_COLLISION")
            outcomes.append({"external_id": key, "status": "DUPLICATE"})
            continue
        seen[key] = row["content_sha256"]
        unique.append(row)
        outcomes.append({"external_id": key, "status": "CANDIDATE"})
    return {"schema": "bohun-grants-write-plan-v1", "project_id": PROJECT, "link_id": LINK,
            "status": "BLOCKED_CONSUMER_CHECK" if source_status == "SUCCEEDED" and unique else "BLOCKED_SOURCE_RESULT",
            "executed": False, "source_status": source_status, "fetched_at": fetched_at, "run_id": run_id,
            "tables": ["bohun_private.sources", "bohun_staging.raw_documents", "bohun_staging.contract_packets"],
            "max_new_rows": {"publisher": 1 if unique else 0, "raw_documents": len(unique), "packets": len(unique)},
            "row_outcomes": outcomes, "rows": unique, "public_release": False,
            "requires": ["FRESH_TARGET_IDENTITY_ACL_CONSTRAINT_CHECK", "CONSUMER_CHECK",
                         "OWNER_APPROVAL_OF_EXACT_WRITE_PACKAGE"], "database_writes": 0}


def sql_literal(value):
    require(isinstance(value, str) and "\0" not in value and len(value.encode()) <= 1048576, "SQL_LITERAL")
    return "'" + value.replace("'", "''") + "'"


def render_sql(plan):
    """Review-only SQL. An unconditional guard prevents accidental execution of this draft."""
    require(plan["schema"] == "bohun-grants-write-plan-v1" and plan["project_id"] == PROJECT
            and plan["executed"] is False and len(plan["rows"]) <= 5, "PLAN_CONTRACT")
    lines = ["-- NOT EXECUTED. REVIEW ONLY. BLOCKED_CONSUMER_CHECK / OWNER_WRITE_APPROVAL_REQUIRED.",
             "BEGIN;", "SET LOCAL search_path = pg_catalog, pg_temp;",
             "SET LOCAL standard_conforming_strings = on;", "SET LOCAL lock_timeout = '5s';",
             "SET LOCAL statement_timeout = '10s';",
             "DO $grants_gate$ BEGIN RAISE EXCEPTION 'BLOCKED_CONSUMER_CHECK_AND_WRITE_APPROVAL'; END $grants_gate$;"]
    if not plan["rows"] or plan["source_status"] != "SUCCEEDED":
        return "\n".join(lines + ["-- No candidate rows. No source write.", "ROLLBACK;", ""])
    meta = canonical({"profile_ref": "SC-GRANTS-01", "planning_slot": "S04", "transport": "official_api",
                      "candidate_contract": CONTRACT, "qa_status": "HOLD", "public_release": False}).decode()
    lines.append("SELECT 1/(CASE WHEN EXISTS(SELECT 1 FROM bohun_private.sources WHERE domain='grants.gov' AND "
                 "(website_url<>'https://www.grants.gov/' OR name IS DISTINCT FROM 'Grants.gov' OR "
                 "category IS DISTINCT FROM 'funding' OR country_code IS DISTINCT FROM 'US' OR "
                 "validation_status<>'candidate' OR relevance<>'unreviewed')) THEN 0 ELSE 1 END) AS publisher_guard;")
    lines.append("INSERT INTO bohun_private.sources(domain,website_url,name,category,country_code,origin_url,metadata) "
                 "SELECT 'grants.gov','https://www.grants.gov/','Grants.gov','funding','US'," + sql_literal(ENDPOINT) +
                 "," + sql_literal(meta) + "::jsonb WHERE NOT EXISTS (SELECT 1 FROM bohun_private.sources WHERE domain='grants.gov');")
    # All untrusted values are ordinary SQL literals, NEVER embedded in dollar-quoted DO bodies.
    for row in plan["rows"]:
        validate_candidate(row)
        vals = [row[k] for k in ("document_id", "source_id", "external_id", "content_sha256", "media_type", "original_url", "raw_content")]
        cols = "document_id,source_id,external_id,content_sha256,media_type,original_url,raw_content"
        values = ",".join(sql_literal(v) for v in vals)
        # Collision assertions are inside the transaction and do not modify any existing row.
        lines.append("WITH expected(" + cols + ") AS (VALUES (" + values + ")) "
                     "SELECT 1/(CASE WHEN EXISTS(SELECT 1 FROM expected e JOIN bohun_staging.raw_documents d "
                     "ON d.document_id=e.document_id OR (d.source_id=e.source_id AND d.external_id=e.external_id "
                     "AND d.content_sha256=e.content_sha256) WHERE d.document_id<>e.document_id OR d.raw_content<>e.raw_content "
                     "OR d.content_sha256<>e.content_sha256 OR d.source_id<>e.source_id OR d.external_id<>e.external_id) "
                     "THEN 0 ELSE 1 END) AS raw_collision_guard;")
        lines.append("INSERT INTO bohun_staging.raw_documents(" + cols + ",fetched_at) VALUES (" + values + "," +
                     sql_literal(plan["fetched_at"]) + "::timestamptz) ON CONFLICT DO NOTHING;")
        payload = canonical(row["payload"]).decode()
        lines.append("SELECT 1/(CASE WHEN EXISTS(SELECT 1 FROM bohun_staging.contract_packets WHERE "
                     "(packet_id=" + sql_literal(row["packet_id"]) + " OR (document_id=" + sql_literal(row["document_id"]) +
                     " AND contract_version=" + sql_literal(CONTRACT) + ")) AND (packet_id<>" + sql_literal(row["packet_id"]) +
                     " OR payload<>" + sql_literal(payload) + "::jsonb OR qa_status<>'HOLD' OR public_release<>false)) "
                     "THEN 0 ELSE 1 END) AS packet_collision_guard;")
        lines.append("INSERT INTO bohun_staging.contract_packets(packet_id,contract_version,document_id,payload,qa_status,"
                     "public_release,evidence_count,claim_count,last_run_id,last_seen_at) VALUES (" +
                     ",".join(sql_literal(v) for v in (row["packet_id"], CONTRACT, row["document_id"], payload)) +
                     "::jsonb,'HOLD',false,0,0," + sql_literal(plan["run_id"]) + "," + sql_literal(plan["fetched_at"]) +
                     "::timestamptz) ON CONFLICT DO NOTHING;")
    lines.extend(["-- Read-back and exact publisher compatibility verification required before any approved COMMIT.",
                  "ROLLBACK;", ""])
    return "\n".join(lines)
