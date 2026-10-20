"""Pure offline tests: no HTTP, Supabase, GitHub writes or paid API calls."""
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from official_sources import ProbeError, uk_probe, canada_probe, eu_probe


class OfficialSourceTests(unittest.TestCase):
    def test_uk_one_page(self):
        def mock(url):
            self.assertEqual(parse_qs(urlsplit(url).query)["limit"], ["100"])
            return json.dumps({"releases": [{"ocid": "ocds-test", "id": "1"}]}).encode()
        out = uk_probe("2026-10-15T09:00:00", "2026-10-15T10:00:00",
                       transport=mock)
        self.assertEqual((out["complete"], out["records"]), (True, 1))

    def test_uk_cursor_preserves_window(self):
        seen = []
        def mock(url):
            seen.append(parse_qs(urlsplit(url).query))
            result = ({"releases": [], "links": {"next": "?cursor=abc123"}}
                      if len(seen) == 1 else {"releases": []})
            return json.dumps(result).encode()
        out = uk_probe("2026-10-15T09:00:00", "2026-10-15T10:00:00",
                       transport=mock)
        self.assertEqual(out["pages"], 2)
        self.assertEqual(seen[0]["updatedFrom"], seen[1]["updatedFrom"])
        self.assertEqual(seen[1]["cursor"], ["abc123"])

    def test_uk_rejects_other_host(self):
        with self.assertRaisesRegex(ProbeError, "other_endpoint"):
            uk_probe("2026-10-15T09:00:00", "2026-10-15T10:00:00",
                     transport=lambda url: json.dumps({
                         "releases": [], "links": {"next": "https://evil.test/?cursor=x"}
                     }).encode())

    def test_uk_rejects_changed_window(self):
        with self.assertRaisesRegex(ProbeError, "changed_scope"):
            uk_probe("2026-10-15T09:00:00", "2026-10-15T10:00:00",
                     transport=lambda url: json.dumps({
                         "releases": [],
                         "links": {"next": "?updatedFrom=2000-01-01T00%3A00%3A00&cursor=abc"}
                     }).encode())

    def test_uk_rejects_unknown_paging_parameters(self):
        with self.assertRaisesRegex(ProbeError, "unknown_params"):
            uk_probe("2026-10-15T09:00:00", "2026-10-15T10:00:00",
                     transport=lambda url: json.dumps({
                         "releases": [], "links": {"next": "?cursor=abc&redirection=1"}
                     }).encode())

    def test_uk_fails_incomplete(self):
        call = []
        def mock(url):
            call.append(url)
            nxt = "?cursor=abc" if len(call) == 1 else "?cursor=def"
            return json.dumps({"releases": [], "links": {"next": nxt}}).encode()
        with self.assertRaisesRegex(ProbeError, "incomplete"):
            uk_probe("2026-10-15T09:00:00", "2026-10-15T10:00:00",
                     transport=mock)

    def test_uk_window_cap(self):
        with self.assertRaisesRegex(ProbeError, "page_limit"):
            uk_probe("2026-10-15T09:00:00", "2026-10-15T12:00:00")

    def test_canada_csv(self):
        body = b"referenceNumber-numeroReference,title-titre-eng\nREF-1,Example\n"
        out = canada_probe("ca_new", transport=lambda url: body)
        self.assertEqual(out["records"], 1)
        self.assertEqual(out["source"], "ca_new")

    def test_canada_duplicate_headers(self):
        with self.assertRaisesRegex(ProbeError, "bad_csv_header"):
            canada_probe("ca_open", transport=lambda url: b"id,id\n1,2\n")

    def test_canada_ragged_rows(self):
        with self.assertRaisesRegex(ProbeError, "ragged"):
            canada_probe("ca_new", transport=lambda url: b"id,title\n1,2,3\n")

    def test_canada_unknown_scope(self):
        with self.assertRaisesRegex(ProbeError, "unknown_scope"):
            canada_probe("ca_awards")

    def test_eu_schema(self):
        def mock(url, *, body, content_type):
            self.assertIn("apiKey=SEDIA", url)
            self.assertIn("multipart/form-data", content_type)
            self.assertIn(b"query", body)
            return json.dumps({
                "results": [{"reference": "EX-1", "metadata": {}}],
                "totalResults": 100
            }).encode()
        out = eu_probe(transport=mock)
        self.assertEqual(out["records"], 1)
        self.assertFalse(out["complete"])

    def test_eu_unknown_shape(self):
        with self.assertRaisesRegex(ProbeError, "wire_format"):
            eu_probe(transport=lambda *a, **kw: b'{"unexpected": []}')

    def test_eu_bad_term(self):
        with self.assertRaises(ProbeError):
            eu_probe("X\nY")


if __name__ == "__main__":
    unittest.main()
