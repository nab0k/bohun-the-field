"""Offline tests for the EDF (European Defence Fund) participants adapter."""
import json
import unittest

from collectors.companies import pipeline
from collectors.companies.sources import edf
from collectors.companies.store import SqliteStore
from tests.companies.helpers import fixture

FX = fixture("edf_projects.json")


class FakeSearch:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def post_form(self, url, fields):
        self.calls.append((url, fields))
        page = int(url.rsplit("pageNumber=", 1)[1])
        return self.pages.get(page, {"totalResults": FX["totalResults"], "results": []})


def one_page():
    return FakeSearch({1: FX})


def two_pages():
    return FakeSearch({1: {"totalResults": 3, "results": FX["results"][:2]},
                       2: {"totalResults": 3, "results": FX["results"][2:]}})


class TestEdfParse(unittest.TestCase):
    def test_real_project(self):
        rec = edf.Grants.parse(FX["results"][0]["metadata"])
        self.assertEqual((rec.source_key, rec.payload["acronym"], rec.payload["call"]),
                         ("101168111", "MPortISTAR", "EDF-2023-LS-DA-SME"))
        coord = rec.mentions[0]
        self.assertEqual((coord.country, coord.identifiers), ("LV", [("EU_PIC", "881995878")]))
        self.assertIn("EDF_COORDINATOR", coord.flags)
        self.assertEqual(coord.website, "https://www.originuav.com")
        self.assertEqual((coord.signals[0].type, coord.signals[0].layer, coord.signals[0].date),
                         ("grant", "A", "2024-11-23"))
        self.assertEqual([m.country for m in rec.mentions], ["LV", "LT", "DE"])
        text = json.dumps(rec.payload, ensure_ascii=False)
        for forbidden in ("street", "latitude", "postalCode", "MARUPE"):
            self.assertNotIn(forbidden, text)

    def test_translation_is_skipped(self):
        self.assertIsNone(edf.Grants.parse(FX["results"][1]["metadata"]))

    def test_ukraine_city_dropped_and_research_role(self):
        rec = edf.Grants.parse(FX["results"][2]["metadata"])
        ua, uni = rec.mentions[0], rec.mentions[1]
        self.assertEqual((ua.country, ua.city), ("UA", None))
        self.assertIn("SME", ua.flags)
        self.assertEqual((uni.supply_role, uni.city, uni.website), ("RND", "DELFT", None))
        self.assertIn("ORG_TYPE:HES", uni.flags)

    def test_non_edf_programme_ignored(self):
        md = dict(FX["results"][0]["metadata"], programAbbreviation=["HORIZON"])
        self.assertIsNone(edf.Grants.parse(md))


class TestEdfFetch(unittest.TestCase):
    def test_request_shape(self):
        http = one_page()
        edf.Grants().fetch(http, None, 10)
        url, fields = http.calls[0]
        self.assertIn("apiKey=SEDIA_NONH2020_PROD", url)
        self.assertEqual(fields, {"query": edf.QUERY, "languages": ["en"]})

    def test_pagination_cursor(self):
        recs, cur = edf.Grants(page_size=2).fetch(two_pages(), None, 1)
        self.assertEqual(([r.source_key for r in recs], cur), (["101168111"], "o:2"))
        recs2, cur2 = edf.Grants(page_size=2).fetch(two_pages(), cur, 10)
        self.assertEqual(([r.source_key for r in recs2], cur2), (["999000001"], None))

    def test_pipeline_matches_same_pic_across_projects(self):
        store = SqliteStore()
        res = pipeline.run_source(store, edf.Grants(), one_page(), 10)
        self.assertEqual(res["status"], "ok")
        origin = store.query("SELECT id, layer, country FROM company WHERE name LIKE '%ORIGIN ROBOTICS%'")
        self.assertEqual(len(origin), 1)  # same PIC in two projects -> one company
        self.assertEqual((origin[0]["layer"], origin[0]["country"]), ("A", "LV"))
        grants = store.query("SELECT COUNT(*) n FROM signal WHERE company_id=? AND type='grant'", (origin[0]["id"],))
        self.assertEqual(grants[0]["n"], 2)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM company")[0]["n"], 5)
        self.assertIsNone(store.query("SELECT city FROM company WHERE country='UA'")[0]["city"])
        again = pipeline.run_source(store, edf.Grants(), one_page(), 10)
        self.assertEqual(again["new_observations"], 0)


class TestPostForm(unittest.TestCase):
    def test_multipart_body_without_network(self):
        import io
        from collectors.companies.sources.http import JsonHttp
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            seen["req"] = req
            return Resp(b'{"totalResults": 0, "results": []}')
        out = JsonHttp(opener=opener).post_form("https://example.invalid/search?x=1", {"query": {"a": 1},
                                                                                       "languages": ["en"]})
        req = seen["req"]
        body = req.data.decode()
        self.assertEqual(out, {"totalResults": 0, "results": []})
        self.assertTrue(req.get_header("Content-type").startswith("multipart/form-data; boundary="))
        self.assertIn('name="query"', body)
        self.assertIn('{"a":1}', body)
        self.assertIn('["en"]', body)


if __name__ == "__main__":
    unittest.main()
