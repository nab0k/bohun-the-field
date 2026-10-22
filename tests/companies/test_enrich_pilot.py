"""Offline tests for the existing EXP-012 enrichment pilot. No external calls."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from collectors.companies import enrich_pilot as pilot
from collectors.companies.sources.gleif import Gleif


def synthetic_rows():
    m = pilot.load_manifest()
    rows = []
    for status, ids in m["groups"].items():
        for candidate_id in ids:
            rows.append({"id": candidate_id, "name": f"Fictional Supplier {candidate_id}",
                         "status": status, "source": pilot.SOURCE,
                         "source_key": f"synthetic-{candidate_id}",
                         "reported_country": "FINLAND",
                         "matched_company_id": f"test_company_{candidate_id}" if status == "candidate_existing" else None,
                         "possible_matches": 2 if status == "hold" else 0})
    # Frozen real cohort contains 25 alternative links across 11 holds.
    hold = [row for row in rows if row["status"] == "hold"]
    for i in range(3):
        hold[i]["possible_matches"] = 3
    return rows


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.queries = []

    def get_json(self, url, accept=None):
        self.queries.append(url)
        page = int(url.split("page[number]=")[1].split("&")[0])
        return self.pages.get(page, {"data": [], "links": {}})


def lei_record(lei, name, country="US"):
    return {"id": lei, "attributes": {"entity": {"legalName": {"name": name},
             "legalAddress": {"country": country}, "status": "ACTIVE"}}}


class TestIntegratedPilot(unittest.TestCase):
    def test_locked_cohort(self):
        self.assertEqual({g: len(x) for g, x in pilot.load_manifest()["groups"].items()},
                         pilot.SIZES)
        self.assertTrue(pilot.validate_rows(synthetic_rows(), pilot.load_manifest()))

    def test_no_country_inference(self):
        r = pilot.research_record(synthetic_rows()[0])
        self.assertEqual(r["reported_country_source_only"], "FINLAND")
        self.assertNotIn("legal_country", r)
        self.assertFalse(r["identity_confirmed"])

    def test_holds_require_ambiguity(self):
        x = synthetic_rows()
        item = next(row for row in x if row["status"] == "hold")
        item["possible_matches"] = 1
        with self.assertRaisesRegex(ValueError, "HOLD"):
            pilot.validate_rows(x, pilot.load_manifest())

    def test_status_change_rejected(self):
        x = synthetic_rows()
        x[0]["status"] = "hold"
        with self.assertRaises(ValueError):
            pilot.validate_rows(x, pilot.load_manifest())

    def test_ledger_detection(self):
        x = synthetic_rows()[:-1]
        with self.assertRaises(ValueError):
            pilot.validate_rows(x, pilot.load_manifest())

    def test_stratified_smoke(self):
        x = synthetic_rows()
        choice = pilot.prioritized_probe_ids(x, 5)
        selected = [row["status"] for row in x if row["id"] in choice]
        self.assertEqual(selected.count("discovered"), 2)
        self.assertEqual(selected.count("candidate_existing"), 2)
        self.assertEqual(selected.count("hold"), 1)

    def test_gleif_multiple_exact_not_unique(self):
        name = "Fictional Supplier"
        data = {"data": [lei_record("A"*20, name), lei_record("B"*20, name)],
                "meta": {"pagination": {"total": 2}}}
        cli = Gleif(FakeHttp({1: data}))
        lead = pilot.registry_probe({"name": name}, cli)
        self.assertEqual(lead["status"], "ambiguous_registry_leads")

    def test_gleif_pagination(self):
        name = "Fictional Supplier"
        pages = {
            1: {"data": [lei_record("A"*20, name)], "links": {"next": "next"},
                "meta": {"pagination": {"total": 2}}},
            2: {"data": [lei_record("B"*20, name)], "links": {},
                "meta": {"pagination": {"total": 2}}}}
        http = FakeHttp(pages)
        result = pilot.registry_probe({"name": name}, Gleif(http))
        self.assertEqual(result["status"], "ambiguous_registry_leads")
        self.assertEqual(len(http.queries), 2)

    def test_partial_registry_cannot_confirm_unique(self):
        name = "Fictional Supplier"
        full = [lei_record("A"*20, name)] + [
            lei_record(f"{i:020d}", f"Other {i}") for i in range(99)]
        pages = {p: {"data": full, "links": {"next": "next"},
                    "meta": {"pagination": {"total": 600}}} for p in (1, 2, 3, 4)}
        result = pilot.registry_probe({"name": name}, Gleif(FakeHttp(pages)))
        self.assertEqual(result["status"], "registry_search_incomplete")
        self.assertFalse(result.get("identity_confirmed", False))

    def test_empty_page_with_claimed_more_is_incomplete(self):
        name = "Fictional Supplier"
        pages = {1: {"data": [lei_record("A"*20, name)], "links": {"next": "next"},
                     "meta": {"pagination": {"total": 3}}},
                 2: {"data": [], "links": {},
                     "meta": {"pagination": {"total": 3}}}}
        result = pilot.registry_probe({"name": name}, Gleif(FakeHttp(pages)))
        self.assertEqual(result["status"], "registry_search_incomplete")

    def test_registry_name_search_not_filtered_by_event_country(self):
        row = {"name": "Fictional Supplier", "reported_country": "FINLAND"}
        http = FakeHttp({1: {"data": [lei_record("A"*20, row["name"], "US")],
                            "meta": {"pagination": {"total": 1}}}})
        result = pilot.registry_probe(row, Gleif(http))
        self.assertEqual(result["status"], "single_exact_lei_candidate")
        self.assertEqual(result["registry_country"], "US")
        self.assertNotIn("FINLAND", http.queries[0])
        self.assertFalse(result["identity_confirmed"])

    def test_api_error_no_identity(self):
        class Broken:
            def find_exact_candidates(self, _):
                raise OSError("sensitive contents")
        out = pilot.registry_probe({"name": "test"}, Broken())
        self.assertEqual(out, {"status": "registry_request_error", "error_type": "OSError"})

    def test_never_auto_promote_hold(self):
        row = next(r for r in synthetic_rows() if r["status"] == "hold")
        info = pilot.research_record(row, {"status": "single_exact_lei_candidate"})
        self.assertEqual(info["review_route"], "manual_ambiguity_review")
        self.assertFalse(info["company_creation_allowed"])
        self.assertFalse(info["publication_allowed"])

    def test_private_output_outside_repo(self):
        items = [pilot.research_record(row) for row in synthetic_rows()]
        with tempfile.TemporaryDirectory() as root:
            report = pilot.write_private(items, pathlib.Path(root)/"secrets")
            self.assertEqual(report["total"], 81)
            self.assertEqual(report["new_company_rows"], 0)
            self.assertEqual(len((pathlib.Path(root)/"secrets/research.jsonl").read_text().splitlines()), 81)
            with self.assertRaises(ValueError):
                pilot.write_private(items, pilot.REPO_ROOT/"do_not_write_here")

    def test_cli_offline_81(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/"fixture.json"
            path.write_text(json.dumps(synthetic_rows()), encoding="utf-8")
            cmd = [sys.executable, "-m", "collectors.companies.enrich_pilot",
                   "--input-json", str(path), "--private-output-dir", str(pathlib.Path(tmp)/"private")]
            run = subprocess.run(cmd, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["total"], 81)
            self.assertNotIn("Fictional Supplier", run.stdout)

    def test_read_only_contract(self):
        self.assertTrue(pilot.QUERY.strip().upper().startswith("SELECT"))
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE "):
            self.assertNotIn(forbidden, pilot.QUERY.upper())
        self.assertNotIn("resolve.resolve(", pathlib.Path(pilot.__file__).read_text())
        self.assertNotIn("cards.promote(", pathlib.Path(pilot.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
