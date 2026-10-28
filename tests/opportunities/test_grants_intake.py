import ast
import contextlib
import copy
import importlib
from pathlib import Path
import re
import unittest
from unittest import mock

# Installs external-I/O tripwires before the production imports.
from test_grants_live import (ContractError, ROOT, canonical, detail, hit, intake, live, response, sha256)


def row(ident=900001, *, source_detail=None, query=None):
    d = detail(ident) if source_detail is None else source_detail
    p = intake.project_record(hit(ident), d)
    return intake.candidate(p, sha256(response(d)), query=live.QUERY if query is None else query)


def plan(rows=None, status="SUCCEEDED"):
    return intake.prepare_plan([row()] if rows is None else rows, fetched_at="2026-10-24T00:00:00Z",
                               run_id=live.APPROVAL, source_status=status)


class IntakeTests(unittest.TestCase):
    def test_metadata_remains_funding_candidate_not_article_or_analytics(self):
        r = row(); p = r["payload"]
        self.assertEqual(p["candidate"]["record_kind"], "FUNDING_OPPORTUNITY")
        self.assertEqual(p["candidate"]["close_date_raw"], "11/01/2026")
        self.assertEqual(p["candidate"]["timezone"], "UNKNOWN")
        self.assertEqual(p["candidate"]["currency"], "UNKNOWN")
        self.assertEqual(p["candidate"]["revision_ordering"], "UNVERIFIED")
        self.assertFalse(r["public_release"]);self.assertEqual(r["qa_status"], "HOLD")
        self.assertEqual(r["claim_count"], 0);self.assertEqual(r["evidence_count"], 0)
        self.assertEqual(p["provenance"]["landing_url_validation"], "UNVERIFIED_UI_LINK")
        self.assertEqual(p["provenance"]["original_url"], live.ORIGIN+live.DETAIL_PATH)
        self.assertNotIn("text/html", r["media_type"])

    def test_source_response_and_sanitized_content_have_distinct_hashes(self):
        r = row()
        self.assertEqual(r["content_sha256"], sha256(r["raw_content"].encode()))
        self.assertNotEqual(r["content_sha256"], r["payload"]["provenance"]["response_sha256"])
        self.assertFalse(r["payload"]["provenance"]["original_response_bytes_retained"])

    def test_grantor_person_is_not_promoted_to_agency(self):
        d=detail();d["synopsis"]["agencyName"]="Synthetic Person Grantor"
        r=row(source_detail=d)
        self.assertIsNone(r["payload"]["candidate"]["agency_name"])
        self.assertEqual(r["payload"]["candidate"]["agency_name_status"],"UNKNOWN_UNRELIABLE_SOURCE_FIELD")
        self.assertNotIn("Synthetic Person Grantor",canonical(r).decode())
        self.assertEqual(r["payload"]["candidate"]["agency_code"],"TEST")

    def test_missing_null_empty_and_invalid_are_not_equated(self):
        d = detail();del d["synopsis"]["agencyName"];d["synopsis"]["awardFloor"] = None
        d["synopsis"]["awardCeiling"] = ""
        p = intake.project_record(hit(), d)
        self.assertEqual(p["field_presence"]["agencyName"], "MISSING")
        self.assertEqual(p["field_presence"]["award_floor_raw"], "NULL")
        self.assertEqual(p["field_presence"]["award_ceiling_raw"], "EMPTY")
        d["synopsis"]["awardCeiling"] = {"invalid": "object"}
        with self.assertRaisesRegex(ContractError,"UNSUPPORTED_FIELD_TYPE"): intake.project_record(hit(),d)

    def test_forecast_missing_description_and_eligibility_stay_explicit(self):
        d = detail();del d["synopsis"];d["forecast"]={"agencyName":"Forecast agency"}
        p = intake.project_record(hit(),d)
        self.assertEqual(p["detail_kind"],"forecast")
        self.assertEqual(p["description_extent"],"NO_DESCRIPTION")
        self.assertEqual(p["field_presence"]["eligibility"] if "eligibility" in p["field_presence"] else
                         p["field_presence"]["applicantTypes"],"MISSING")

    def test_unchanged_candidate_ids_ignore_query_and_fetch_time(self):
        first=row();other=row(query={**live.QUERY,"keyword":"another query"})
        self.assertEqual(first["document_id"],other["document_id"])
        self.assertEqual(first["packet_id"],other["packet_id"])
        p1=plan();p2=intake.prepare_plan([row()],fetched_at="2026-10-25T00:00:00Z",run_id=live.APPROVAL,
                                       source_status="SUCCEEDED")
        self.assertEqual(p1["rows"],p2["rows"])
        with self.assertRaisesRegex(ContractError,"PLAN_QUERY_SCOPE"):plan([other])

    def test_semantic_change_creates_new_version_and_same_batch_collision_stops(self):
        d=detail();d["opportunityTitle"]="Changed title"
        a,b=row(),row(source_detail=d)
        self.assertNotEqual(a["document_id"],b["document_id"])
        with self.assertRaisesRegex(ContractError,"KEY_COLLISION"):plan([a,b])
        self.assertEqual(plan([a,copy.deepcopy(a)])["max_new_rows"]["raw_documents"],1)
        self.assertEqual(plan([a,copy.deepcopy(a)])["row_outcomes"][1]["status"],"DUPLICATE")

    def test_forced_release_source_id_unknown_schema_extra_fields_rejected(self):
        for mutation in ("release","source2","contract","projection_extra","hash"):
            with self.subTest(mutation=mutation):
                r=row()
                if mutation=="release":r["public_release"]=True
                if mutation=="source2":r["source_id"]=2
                if mutation=="contract":r["contract_version"]="inoreader-artifact-v1"
                if mutation=="projection_extra":r["payload"]["candidate"]["execute"]="malicious"
                if mutation=="hash":r["content_sha256"]="0"*64
                with self.assertRaises(ContractError):intake.validate_candidate(r)

    def test_strict_ids_row_count_text_bounds(self):
        for ident in (True,False,"0","001", "1;DROP", None):
            with self.assertRaises(ContractError):intake.native_id(ident)
        with self.assertRaises(ContractError):plan([row()]*6)
        d=detail();d["synopsis"]["synopsisDesc"]="x"*(intake.MAX_TEXT+1)
        with self.assertRaises(ContractError):row(source_detail=d)
        with self.assertRaises(ContractError):intake.sql_literal("bad\0literal")

    def test_write_plan_targets_are_exact_and_default_is_blocked(self):
        p=plan()
        self.assertEqual(p["project_id"],"tecrucawzukxqtchpxrh")
        self.assertEqual(p["status"],"BLOCKED_CONSUMER_CHECK")
        self.assertFalse(p["executed"])
        self.assertEqual(p["tables"],["bohun_private.sources","bohun_staging.raw_documents","bohun_staging.contract_packets"])
        self.assertEqual(p["max_new_rows"],{"publisher":1,"raw_documents":1,"packets":1})
        sql=intake.render_sql(p)
        self.assertIn("RAISE EXCEPTION 'BLOCKED_CONSUMER_CHECK_AND_WRITE_APPROVAL'",sql)
        self.assertTrue(sql.endswith("ROLLBACK;\n"))
        self.assertNotIn("COMMIT;",sql)
        self.assertNotIn("UPDATE ",sql);self.assertNotIn("DELETE ",sql)
        for forbidden in ("intelligence.items","exp009.jobs","model_candidates","bohun_uk_intake","CREATE TABLE"):
            self.assertNotIn(forbidden,sql)

    def test_partial_or_empty_source_never_emits_dml(self):
        for p in (plan([],"EMPTY"),plan(status="PARTIAL"),plan([],"FAILED")):
            self.assertEqual(p["status"],"BLOCKED_SOURCE_RESULT")
            self.assertNotIn("INSERT INTO",intake.render_sql(p))

    def test_sql_literals_cannot_escape_into_statements_or_dollar_block(self):
        d=detail();d["opportunityTitle"]="O'Reilly'; DROP TABLE public.secret; -- $grants_gate$ \\ text"
        sql=intake.render_sql(plan([row(source_detail=d)]))
        self.assertIn("O''Reilly",sql)
        outside_literals=re.sub(r"'(?:''|[^'])*'","''",sql)
        self.assertNotIn("DROP TABLE",outside_literals)
        self.assertIn("standard_conforming_strings = on",sql)
        # Dollar-quoted DO body is constant and precedes any API data literal.
        body=sql.split("DO $grants_gate$",1)[1].split("$grants_gate$;",1)[0]
        self.assertNotIn("O'Reilly",body)

    def test_module_imports_do_not_write_read_local_files_or_resolve_paths(self):
        with mock.patch.object(Path,"write_text",side_effect=AssertionError("IMPORT_WRITE")), \
             mock.patch.object(Path,"read_bytes",side_effect=AssertionError("IMPORT_READ")), \
             mock.patch.object(Path,"mkdir",side_effect=AssertionError("IMPORT_MKDIR")), \
             mock.patch.object(Path,"resolve",side_effect=AssertionError("IMPORT_RESOLVE")):
            importlib.reload(intake);importlib.reload(live)

    def test_prepare_module_has_no_database_network_or_process_calls(self):
        tree=ast.parse(Path(intake.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                self.assertTrue(all(a.name in {"json","re"} for a in node.names))
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                self.assertNotIn(node.func.attr,{"connect","cursor","execute","commit","Popen","system","urlopen"})
