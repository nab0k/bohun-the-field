"""A1SD-02 search by the approved design: planner, cycle collection and measures, no network, no credits."""
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import search_cycle as C  # noqa: E402
import plan as P  # noqa: E402

CAT = P.load()
COUNTRIES = list(CAT["countries"])


class Dictionary(unittest.TestCase):
    def test_every_category_has_every_language_and_valid_type(self):
        langs = {c["lang"] for c in CAT["countries"].values()} | {"en"}
        for cid, c in CAT["categories"].items():
            self.assertEqual(set(c["terms"]), langs, cid)
            self.assertTrue(all(t.strip() for t in c["terms"].values()), cid)
            self.assertIn(c["default_type"], C.A.TYPES, cid)

    def test_missing_translation_stops_at_load(self):
        import json, tempfile
        bad = json.loads(json.dumps(CAT))
        del bad["categories"]["ew"]["terms"]["fi"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
        with self.assertRaises(ValueError):
            P.load(f.name)

    def test_tech_categories_cover_all_14_base_topics(self):
        topics = {c["base_topic"] for c in CAT["categories"].values() if c["base_topic"]}
        self.assertEqual(len(topics), 14)


class Planner(unittest.TestCase):
    def test_skeleton_six_roles_per_country(self):
        jobs = P.skeleton_jobs(CAT, COUNTRIES)
        self.assertEqual(len(jobs), 6 * 12)
        self.assertEqual(len({j["country"] for j in jobs[:24]}), 12)  # role-major: a partial quota covers everyone

    def test_layer1_first_cycle_72_queries_local_and_english(self):
        jobs = P.layer1_jobs(CAT, COUNTRIES)
        self.assertEqual(len(jobs), 72)
        self.assertEqual(len({j["country"] for j in jobs[:24]}), 12)
        pl = [j for j in jobs if j["country"] == "PL"]
        self.assertEqual(Counter(j["lang"] for j in pl), {"pl": 3, "en": 3})
        self.assertTrue(all(j["lang"] == "en" for j in jobs if j["country"] == "GB"))
        self.assertNotIn("arctic", {j["category"] for j in jobs if j["country"] == "PL"})

    def test_gaps_first(self):
        order = P.priority(CAT)
        self.assertLess(order.index("ew"), order.index("naval"))
        self.assertLess(order.index("critical_materials"), order.index("uncrewed"))

    def test_next_cycle_moves_down_the_list(self):
        first = P.layer1_jobs(CAT, COUNTRIES)
        second = P.layer1_jobs(CAT, COUNTRIES, done={P.job_key(j) for j in first})
        asked = {(j["country"], j["category"]) for j in first}
        self.assertTrue(second)
        self.assertFalse(asked & {(j["country"], j["category"]) for j in second})

    def test_layer3_dedups_titles_and_excludes_known_publishers(self):
        ev = [{"id": 1, "title": "Saab launches X", "topic": "naval-shipbuilding", "known_hosts": ["a.com"]},
              {"id": 2, "title": "Saab launches X!", "topic": "naval-shipbuilding", "known_hosts": ["b.com"]},
              {"id": 3, "title": "EW jammer contract", "topic": "electronic-warfare", "known_hosts": ["c.com"]}]
        jobs = P.layer3_jobs(CAT, ev, 50)
        self.assertEqual([j["event_id"] for j in jobs], [3, 1])  # gap topic first, duplicate title once
        self.assertEqual(jobs[1]["exclude_hosts"], ["a.com"])

    def test_cycle_quota_and_done(self):
        jobs = {"skeleton": P.skeleton_jobs(CAT, COUNTRIES), "layer1": P.layer1_jobs(CAT, COUNTRIES)}
        todo = P.plan_cycle(jobs, {"skeleton": 24, "layer1": 48})
        self.assertEqual(len(todo), 72)
        again = P.plan_cycle(jobs, {"skeleton": 24, "layer1": 48}, {P.job_key(j) for j in todo})
        self.assertFalse({P.job_key(j) for j in todo} & {P.job_key(j) for j in again})
        self.assertEqual(sum(C.QUOTAS.values()), C.LIMITS["tavily"] + C.LIMITS["gemini"])


def fake_search(job):
    if "Romania" in job["query"]:
        raise RuntimeError("TAVILY_CLI_FAILED")
    return {"results": [{"url": "https://www.defence24.com/x"}, {"url": "https://new-mag.example.pl/a"},
                        {"url": "https://new-mag.example.pl/b"}, {"url": "https://www.youtube.com/watch?v=1"},
                        {"url": f"https://site-{len(job['query'])}.example.eu/n"}]}


class Collect(unittest.TestCase):
    def jobs(self):
        return [{"layer": "skeleton", "country": "PL", "role": "ministry", "lang": "en", "type_hint": "agency",
                 "query": "Poland ministry", "topic": "general", "max_results": 5},
                {"layer": "skeleton", "country": "RO", "role": "ministry", "lang": "en", "type_hint": "agency",
                 "query": "Romania ministry", "topic": "general", "max_results": 5},
                {"layer": "layer3", "event_id": 9, "lang": "en", "type_hint": "media", "query": "event title here",
                 "topic": "news", "max_results": 10, "exclude_hosts": ["new-mag.example.pl"]}]

    def test_known_and_aggregators_skipped_failed_query_goes_on(self):
        cands, found, done, trace = C.collect(self.jobs(), fake_search, {"defence24.com"}, 100, 150)
        sites = [c["url"] for c in cands]
        self.assertNotIn("https://www.defence24.com/", sites)
        self.assertEqual(sites.count("https://new-mag.example.pl/"), 1)
        stats = trace.pop("job_stats")
        self.assertEqual(trace, {"queries": 3, "failed": 1})
        self.assertEqual(len(stats), 2)  # asked and answered jobs only
        self.assertEqual(sum(s["new"] for s in stats.values()), len(cands))
        self.assertTrue(all(c["found_via"]["job"] in stats for c in cands))
        self.assertEqual(len(done), 2)
        self.assertTrue(all(c["found_via"]["channel"] == "tavily" for c in cands))
        self.assertEqual(sum(f["known"] for f in found), 2)  # defence24 seen twice (skeleton PL + layer3)

    def test_hints_stay_in_found_via(self):
        cands, _, _, _ = C.collect(self.jobs(), fake_search, set(), 100, 150)
        self.assertTrue(all("guessed_type" not in c and "guessed_country" not in c for c in cands))
        self.assertEqual(cands[0]["found_via"]["country"], "PL")
        self.assertEqual(cands[0]["found_via"]["type_hint"], "agency")

    def test_per_layer_caps_leave_room_for_layer3(self):
        cands, _, _, _ = C.collect(self.jobs(), fake_search, set(), 100, 150, {"skeleton": 1, "layer3": 5})
        self.assertEqual(Counter(c["found_via"]["layer"] for c in cands), {"skeleton": 1, "layer3": 1})
        self.assertEqual(sum(C.NEW_CAPS.values()), C.LIMITS["new"])

    def test_caps(self):
        cands, _, _, trace = C.collect(self.jobs(), fake_search, set(), 1, 150)
        self.assertEqual(trace["queries"], 1)
        cands, _, _, _ = C.collect(self.jobs(), fake_search, set(), 100, 2)
        self.assertEqual(len(cands), 2)


class Measures(unittest.TestCase):
    def test_cycle_acceptance_and_saturation(self):
        cands = [{"url": f"https://site{i}.eu/", "found_via": {"layer": "layer1", "country": c}}
                 for i, c in enumerate(COUNTRIES[:10])]
        res = [{"decision": "accept", "type": t, "country": "PL"} for t in ("media", "agency", "procurement")] + \
              [{"decision": "disputed", "type": t} for t in ("grants", "exhibition")] + \
              [{"decision": "retry"}] * 3 + [{"decision": "hold", "type": "media"}] * 5
        m = C.measure_cycle(cands, [{"layer": "layer1", "known": True}, {"layer": "layer1", "known": False}], res)
        self.assertEqual((m["n_countries"], m["n_types"], m["saturation"]), (10, 5, 0.5))
        self.assertEqual(m["accepted_share"], 0.3)
        self.assertTrue(m["a1sd_cycle_ok"])

    def test_known_reissued_is_counted_and_fails_the_cycle(self):
        cands = [{"url": f"https://site{i}.eu/", "found_via": {"layer": "layer1", "country": c}}
                 for i, c in enumerate(COUNTRIES[:10])]
        res = [{"decision": "accept", "type": t} for t in ("media", "agency", "procurement", "grants", "exhibition")]
        m = C.measure_cycle(cands, [], res, known={"site0.eu"})
        self.assertEqual(m["known_reissued"], 1)
        self.assertFalse(m["a1sd_cycle_ok"])

    def test_coverage_excludes_control_publishers(self):
        ev = [{"hosts": ["defensenews.com"]}, {"hosts": ["www.hartpunkt.de", "x.com"]}, {"hosts": ["y.org"]}]
        r = C.coverage(ev, ["hartpunkt.de", "defensenews.com"], exclude_hosts=["defensenews.com"])
        self.assertEqual((r["found"], r["share"]), (1, 0.333))




class Ranking(unittest.TestCase):
    def test_defence_named_and_multi_hit_sites_first(self):
        def search(job):
            if job["query"] == "q1":
                return {"results": [{"url": "https://generalnews.eu/a"}, {"url": "https://twice.eu/a"},
                                    {"url": "https://zona-militar.com/a"}]}
            return {"results": [{"url": "https://twice.eu/b"}]}
        jobs = [{"layer": "layer1", "country": "ES", "category": "ew", "lang": "es", "type_hint": "media",
                 "query": q, "topic": "news", "max_results": 10} for q in ("q1", "q2")]
        jobs[1]["category"] = "naval"
        cands, _, _, _ = C.collect(jobs, search, set(), 10, 2)
        self.assertEqual([c["url"] for c in cands], ["https://zona-militar.com/", "https://twice.eu/"])
        self.assertEqual(cands[1]["found_via"]["hits"], 2)


class Sections(unittest.TestCase):
    def test_job_keys_of_old_cycles_unchanged(self):
        job = {"layer": "layer1", "country": "PL", "category": "ew", "lang": "pl"}
        self.assertEqual(P.job_key(job), "layer1|PL|ew||pl|")
        self.assertTrue(P.job_key({"layer": "section", "lang": "fr", "site": "senat.fr"}).endswith("|senat.fr"))

    def test_official_first_and_language_term(self):
        jobs = P.section_jobs([{"site": "france24.com", "language": "fr"}, {"site": "senat.fr", "language": "fr"},
                               {"site": "x.example", "language": "xx"}])
        self.assertEqual([j["site"] for j in jobs], ["senat.fr", "france24.com", "x.example"])
        self.assertEqual((jobs[0]["query"], jobs[0]["include_domains"]), ("défense", ["senat.fr"]))
        self.assertEqual(jobs[2]["query"], "defence")

    def test_section_cut_from_result_address(self):
        seen = {}
        job = {"layer": "section", "site": "france24.com", "lang": "fr", "query": "défense", "type_hint": "media"}
        C.section_candidate(seen, job, {"url": "https://www.france24.com/fr/tag/defense/page-2"})
        C.section_candidate(seen, job, {"url": "https://www.france24.com/fr/europe/20261118-article"})
        C.section_candidate(seen, job, {"url": "https://www.ukrinform.ua/rubric-%D0%BE%D0%B1%D0%BE%D1%80%D0%BE%D0%BD%D0%B0/x"})
        C.section_candidate(seen, job, {"url": "https://www.bundestag.de/dokumente/2026/kw37-de-verteidigung-1194766"})
        C.section_candidate(seen, job, {"url": "https://www.regeringen.se/contentassets/x/forsvar.pdf"})
        urls = [v["by_layer"]["section"]["url"] for v in seen.values()]
        self.assertEqual(urls, ["https://www.france24.com/fr/tag/defense/",
                                "https://www.ukrinform.ua/rubric-%D0%BE%D0%B1%D0%BE%D1%80%D0%BE%D0%BD%D0%B0/"])
        self.assertEqual(seen["section:" + urls[0]]["by_layer"]["section"]["mode"], "channel")


class Reissued(unittest.TestCase):
    def test_section_on_known_host_is_new_unless_same_address(self):
        sec = {"url": "https://www.france24.com/fr/tag/defense/", "found_via": {"layer": "section"}}
        site = {"url": "https://www.france24.com/", "found_via": {"layer": "layer1"}}
        self.assertEqual(C.reissued([sec, site], {"france24.com"}), 1)
        self.assertEqual(C.reissued([sec], {"france24.com", C.A.registry.canonical_url(sec["url"])}), 1)


class Layer3Rotation(unittest.TestCase):
    def test_asked_events_are_skipped(self):
        ev = [{"id": i, "title": f"Event number {i}", "topic": "naval-shipbuilding", "known_hosts": []} for i in range(5)]
        first = P.layer3_jobs(CAT, ev, 2)
        second = P.layer3_jobs(CAT, ev, 2, {P.job_key(j) for j in first})
        self.assertEqual([j["event_id"] for j in first], [0, 1])
        self.assertEqual([j["event_id"] for j in second], [2, 3])


class TwoEngines(unittest.TestCase):
    def test_share_and_alternation(self):
        todo = [{"layer": "layer1"} for _ in range(6)] + [{"layer": "section"} for _ in range(2)]
        C.assign_engines(todo, {"layer1": 3, "section": 0})
        self.assertEqual([j["engine"] for j in todo[:6]], ["gemini", "tavily"] * 3)
        self.assertEqual({j["engine"] for j in todo[6:]}, {"tavily"})
        self.assertEqual(sum(C.GEMINI_SHARE.values()), C.LIMITS["gemini"])
        self.assertEqual(sum(C.QUOTAS.values()) - sum(C.GEMINI_SHARE.values()), C.LIMITS["tavily"])

    def test_engine_switched_off_after_two_failures(self):
        calls = []

        def bad(job):
            calls.append(job)
            raise OSError("402")
        e = C.Engines(tavily=lambda job: {"results": []}, gemini=bad)
        for _ in range(4):
            with self.assertRaises(RuntimeError):
                e({"engine": "gemini"})
        self.assertEqual(len(calls), 2)            # third and fourth never reach the provider
        self.assertEqual(e({"engine": "tavily"}), {"results": []})
        self.assertEqual(e.off, {"gemini"})
        self.assertEqual(e.errors, {"gemini:OSError:": 2})


class TypesLayer(unittest.TestCase):
    def test_every_type_early_and_rotation(self):
        jobs = P.type_jobs(CAT, COUNTRIES)
        self.assertEqual(len(jobs), 6 * 12)
        self.assertEqual({j["role"] for j in jobs[:6]}, set(P.TYPE_QUERIES))
        later = P.type_jobs(CAT, COUNTRIES, {P.job_key(j) for j in jobs[:48]})
        self.assertEqual(len(later), 24)
        self.assertEqual(sum(C.QUOTAS.values()), C.LIMITS["tavily"] + C.LIMITS["gemini"])


class OpenBases(unittest.TestCase):
    def test_gdelt_and_wikidata_jobs(self):
        g = P.gdelt_jobs(CAT, COUNTRIES)
        self.assertEqual({j["engine"] for j in g}, {"gdelt"})
        self.assertEqual((g[0]["category"], g[0]["gdelt_country"]), ("arctic", "sweden"))  # gap category first
        self.assertTrue(all(j["query"] and " " not in j["gdelt_country"] for j in g))
        later = P.gdelt_jobs(CAT, COUNTRIES, {P.job_key(j) for j in g[:12]})
        self.assertEqual(len(later), len(g) - 12)
        w = P.wikidata_jobs(COUNTRIES)
        self.assertEqual(len(w), 24)  # A1SD-03: officials and companies per country
        self.assertEqual([(j["country"], j["role"]) for j in w[:2]], [(COUNTRIES[0], ""), (COUNTRIES[0], "companies")])
        # officials asked before 0.6 (no role in the key) are not asked again
        old = "wikidata|%s|||en|" % COUNTRIES[0]
        self.assertEqual(P.job_key(w[0]), old)
        self.assertEqual(P.wikidata_jobs(COUNTRIES, {old})[0]["role"], "companies")
        eu = P.europe()
        self.assertEqual(len(eu), 50)
        self.assertTrue(set(COUNTRIES) <= set(eu))

    def test_open_bases_keep_their_engine_and_dispatch(self):
        todo = [{"layer": "gdelt", "engine": "gdelt", "query": "electronic warfare", "gdelt_country": "poland"},
                {"layer": "wikidata", "engine": "wikidata", "qid": "Q36"}, {"layer": "layer1"},
                {"layer": "wikidata", "engine": "wikidata", "iso": "PL", "role": "companies"}]
        C.assign_engines(todo, {"layer1": 1})
        self.assertEqual([j["engine"] for j in todo], ["gdelt", "wikidata", "gemini", "wikidata"])
        e = C.Engines(tavily=lambda j: {"results": []}, gemini=lambda j: {"results": []})
        calls = []
        e.fn["gdelt"] = lambda j: calls.append(("gdelt", j["gdelt_country"])) or {"results": []}
        e.fn["wikidata"] = lambda j: calls.append(("wikidata", j["qid"])) or {"results": []}
        for j in todo[:2]:
            e(j)
        self.assertEqual(calls, [("gdelt", "poland"), ("wikidata", "Q36")])
        import providers as PR
        real, PR._wikidata = PR._wikidata, lambda sparql: {"results": [], "sparql": sparql}
        try:
            e2 = C.Engines(tavily=lambda j: {"results": []}, gemini=lambda j: {"results": []})
            q = e2(todo[3])["sparql"]
        finally:
            PR._wikidata = real
        self.assertIn('wdt:P297 "PL"', q)
        self.assertIn("wd:Q392933", q)  # arms industry, checked on Wikidata 19.11

    def test_contact_in_user_agent(self):
        import providers as PR
        self.assertIn("github.com/nab0k/bohun-the-field", PR.UA)

    def test_daily_limits_include_open_bases(self):
        import run_daily as R
        limits, quotas, gemini, caps = R.daily_limits(False)
        self.assertEqual((limits["gdelt"], limits["wikidata"], limits["tavily"]), (0, 20, 0))
        self.assertEqual(caps["wikidata"], R.WIKIDATA_NEW)
        self.assertLessEqual(sum(v for k, v in caps.items() if k != "wikidata"), limits["new"] - R.WIKIDATA_NEW + 2)
        self.assertEqual(sum(quotas.values()), sum(limits[e] for e in ("tavily", "gemini", "gdelt", "wikidata")))


class SearchShare(unittest.TestCase):
    def test_company_layer_reported_apart(self):
        cands = [{"url": f"https://site{i}.eu/", "found_via": {"layer": "layer1", "country": c}}
                 for i, c in enumerate(COUNTRIES[:10])]
        res = [{"decision": "accept", "type": t, "found_via_layer": "layer1"}
               for t in ("media", "agency", "procurement", "grants", "exhibition")]
        res += [{"decision": "hold", "type": "company_news", "found_via_layer": "layer2"}] * 10
        m = C.measure_cycle(cands, [], res, set())
        self.assertEqual((m["accepted_share_search"], m["accepted_share_layer2"]), (1.0, 0.0))
        self.assertEqual(m["accepted_share"], 0.333)
        self.assertTrue(m["a1sd_cycle_ok"])  # acceptance judged on what search found


if __name__ == "__main__":
    unittest.main()
