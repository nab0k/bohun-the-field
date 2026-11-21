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
        # categories-0.2: the dictionary also carries the languages of the Europe extension (plan.EXTRA_LANG)
        langs = {c["lang"] for c in CAT["countries"].values()} | {"en"} | {l for l, _ in P.EXTRA_LANG.values()}
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


class MoreEngines(unittest.TestCase):
    """A1SD-03: Jina in the rotation, Exa "similar to accepted", keys from the owner's files."""

    def test_rotation_uses_every_share_exactly(self):
        todo = [{"layer": "types"} for _ in range(10)] + [{"layer": "similar", "engine": "exa", "site_url": "x"}]
        C.assign_engines(todo, {"types": 4}, {"types": 3})
        engines = [j["engine"] for j in todo[:10]]
        self.assertEqual((engines.count("gemini"), engines.count("jina"), engines.count("tavily")), (4, 3, 3))
        self.assertEqual(engines[:3], ["gemini", "jina", "tavily"])
        self.assertEqual(todo[10]["engine"], "exa")

    def test_daily_limits_with_jina_and_exa(self):
        import run_daily as R
        limits, quotas, gemini, caps, jina = R.daily_limits(True, jina=True, exa=True)
        self.assertEqual((limits["jina"], limits["exa"], quotas["similar"]), (10, 15, 15))
        self.assertEqual(limits["tavily"], 20)  # Tavily's share does not grow with Jina
        self.assertEqual(sum(v for k, v in quotas.items() if k not in R.OPEN_LAYERS),
                         limits["tavily"] + limits["gemini"] + limits["jina"])
        limits0, quotas0, _, _, jina0 = R.daily_limits(True)
        self.assertEqual((limits0["jina"], limits0["exa"], quotas0["similar"], jina0), (0, 0, 0, {}))

    def test_similar_jobs_once_per_site_and_dispatch(self):
        jobs = P.similar_jobs([("bundeswehr-journal.de", "DE"), ("defensescoop.com", None)])
        self.assertEqual([j["site_url"] for j in jobs], ["https://bundeswehr-journal.de/", "https://defensescoop.com/"])
        self.assertEqual(len(P.similar_jobs([("bundeswehr-journal.de", "DE")], {P.job_key(jobs[0])})), 0)
        e = C.Engines(tavily=lambda j: {"results": []}, gemini=lambda j: {"results": []})
        calls = []
        e.fn["exa"] = lambda j: calls.append(j["site_url"]) or {"results": []}
        e(jobs[0])
        self.assertEqual(calls, ["https://bundeswehr-journal.de/"])

    def test_key_line_is_picked_from_a_noted_file(self):
        import os
        import tempfile
        import providers as PR
        with tempfile.TemporaryDirectory() as d:
            Path(d, "EXA.txt").write_text("Exa\n0123abcd-0123-4567-89ab-0123456789ab\n\nUse the key with care in the header\n")
            real_dir, PR.KEY_DIR = PR.KEY_DIR, Path(d)
            old = os.environ.pop("EXA_API_KEY", None)
            try:
                self.assertEqual(PR._key("EXA.txt"), "0123abcd-0123-4567-89ab-0123456789ab")
                os.environ["EXA_API_KEY"] = "Exa\n0123abcd-0123-4567-89ab-0123456789ab\n\nUse the key with care\n"
                self.assertEqual(PR._key("EXA.txt"), "0123abcd-0123-4567-89ab-0123456789ab")  # whole file as secret
                os.environ.pop("EXA_API_KEY")
            finally:
                PR.KEY_DIR = real_dir
                if old is not None:
                    os.environ["EXA_API_KEY"] = old


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
        local = sum(1 for cc in COUNTRIES if P.country_lang(CAT, cc)[0] in P.LOCAL_TYPE_QUERIES)
        self.assertEqual(len(jobs), 8 * 12 + 4 * local)  # A1SD-04: 8 kinds in English, 4 priority kinds also local
        first = {j["role"] for j in jobs if j["country"] == jobs[0]["country"]}
        self.assertEqual({r.split(".")[0] for r in first}, set(P.TYPE_QUERIES))
        later = P.type_jobs(CAT, COUNTRIES, {P.job_key(j) for j in jobs[:48]})
        self.assertEqual(len(later), len(jobs) - 48)
        self.assertEqual(sum(C.QUOTAS.values()), C.LIMITS["tavily"] + C.LIMITS["gemini"])

    def test_priority_kinds_local_language_and_weighting(self):
        jobs = P.type_jobs(CAT, COUNTRIES)
        ua = [j for j in jobs if j["country"] == "UA" and j["lang"] == "uk"]
        self.assertEqual({j["role"] for j in ua}, {"grants.v2", "procurement.v2", "investment", "startups"})
        self.assertTrue(all("Україна" in j["query"] for j in ua))
        self.assertEqual({j["type_hint"] for j in jobs if j["role"] == "startups"}, {"investment"})
        # old wording of grants and procurement was asked under the old role: the new wording is asked again
        old = {"types|UA||grants|en|", "types|UA||procurement|en|"}
        self.assertTrue(any(j["country"] == "UA" and j["role"] == "grants.v2" and j["lang"] == "en"
                            for j in P.type_jobs(CAT, COUNTRIES, old)))
        w = P.weight_types(jobs)
        head = w[:30]
        prio = sum(j["type_hint"] in P.PRIORITY_TYPES for j in head)
        self.assertEqual(prio, 20)  # two of every three
        self.assertEqual(sorted(map(id, w)), sorted(map(id, jobs)))
        tiny = P.type_jobs(CAT, ["AD"], names={"AD": "Andorra"})
        self.assertFalse(any(j["type_hint"] in P.PRIORITY_TYPES for j in tiny))  # micro-states: no priority jobs


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
        limits, quotas, gemini, caps, jina = R.daily_limits(False)
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


class Layer1Europe(unittest.TestCase):
    def test_layer1_covers_europe_in_known_languages(self):
        ext = P.with_europe(CAT)
        self.assertEqual(ext["countries"]["PL"], CAT["countries"]["PL"])          # originals unchanged
        self.assertEqual(ext["countries"]["AT"]["lang"], "de")
        self.assertEqual(ext["countries"]["LT"], {"name_en": "Lithuania", "lang": "lt", "name_local": "Lietuva"})
        self.assertEqual(ext["countries"]["SK"]["lang"], "en")  # no Slovak terms yet
        self.assertEqual(ext["countries"]["RS"]["lang"], "hr")
        self.assertNotIn("VA", ext["countries"])
        self.assertEqual(len(ext["countries"]), 45)
        jobs = P.layer1_jobs(ext, list(ext["countries"]))
        self.assertTrue({"LT", "AT", "GR"} <= {j["country"] for j in jobs})
        at = [j for j in jobs if j["country"] == "AT" and j["lang"] == "de"]
        self.assertTrue(at and at[0]["query"].endswith("Österreich"))
        self.assertTrue(all(j["query"] and j["lang"] in CAT["categories"][j["category"]]["terms"] for j in jobs))
        self.assertNotIn("countries", CAT.get("_mutated", {}))  # the original dictionary is not modified
        self.assertNotIn("LT", CAT["countries"])


class EngineCaps(unittest.TestCase):
    def test_learning_never_moves_the_engine_limits(self):
        import run_daily as R
        quotas = {"types": 14, "layer1": 17, "layer3": 31, "section": 8, "gdelt": 0, "wikidata": 20, "similar": 15}
        gemini, jina = R.engine_shares(quotas, 40, ["types", "layer1", "layer3"], {"types": 5, "layer1": 5})
        self.assertEqual(sum(gemini.values()), 40)
        self.assertEqual(jina, {"types": 5, "layer1": 5})
        self.assertTrue(all(gemini[l] + jina.get(l, 0) <= quotas[l] for l in gemini))
        self.assertEqual(R.tavily_left(quotas, gemini, jina), 70 - 40 - 10)
        self.assertNotIn("section", gemini)  # the section layer stays with Tavily (include_domains)


class World(unittest.TestCase):
    def test_world_covers_133_countries_in_their_languages(self):
        w = P.world()
        self.assertEqual(len(w), 133)
        self.assertTrue({"US", "BR", "JP", "IN", "AU", "SA"} <= set(w))
        ext = P.with_europe(CAT, w)
        self.assertEqual(ext["countries"]["JP"]["lang"], "ja")
        self.assertEqual(ext["countries"]["MX"], {"name_en": "Mexico", "lang": "es", "name_local": "México"})
        self.assertEqual(ext["countries"]["US"]["lang"], "en")
        self.assertNotIn("TV", ext["countries"])                  # micro-state: no layer 1
        self.assertEqual(ext["countries"]["PL"], CAT["countries"]["PL"])
        self.assertEqual(len(P.wikidata_jobs(list(w))), 266)       # officials and companies for every country
        self.assertEqual(C.A.CCTLD.get("co"), None)                 # .co is generic (dronexl.co), never Colombia
        self.assertEqual(C.A.topic_share(["日本の防衛装備品輸出", "국방부 무기 계약", "Football"])[0], 2 / 3)


if __name__ == "__main__":
    unittest.main()
