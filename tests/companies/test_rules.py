"""Offline tests: normalization, taxonomy and signal layers (EXP-012)."""
import unittest

from collectors.companies.normalize import clean_name, country_iso2, name_norm
from collectors.companies.taxonomy import best_layer, classify, is_defence_cpv, is_rnd


class TestNormalize(unittest.TestCase):
    def test_legal_forms_are_equivalent(self):
        self.assertEqual(name_norm("Saab AB"), name_norm("SAAB Aktiebolag"))
        self.assertEqual(name_norm("Weibel Scientific A/S"), name_norm("WEIBEL SCIENTIFIC"))

    def test_different_companies_stay_different(self):
        self.assertNotEqual(name_norm("Saab AB"), name_norm("SSAB AB"))
        self.assertNotEqual(name_norm("Boeing"), name_norm("Bell Boeing Joint Project Office"))

    def test_ted_language_prefix_removed(self):
        self.assertEqual(clean_name("EN_Weibel Scientific A/S"), "Weibel Scientific A/S")
        self.assertEqual(clean_name('ТОВ "НОВА ЛІНІЯ 1"'), "ТОВ НОВА ЛІНІЯ 1")

    def test_country_codes(self):
        self.assertEqual(country_iso2("DNK"), "DK")
        self.assertEqual(country_iso2("USA"), "US")
        self.assertEqual(country_iso2("Україна"), "UA")
        self.assertEqual(country_iso2(["DEU", "DEU"]), "DE")
        self.assertIsNone(country_iso2(["DEU", "FRA"]))
        self.assertEqual(country_iso2("CHN"), "CN")


class TestTaxonomy(unittest.TestCase):
    def test_cpv_longest_prefix_and_basis(self):
        self.assertEqual(classify("CPV", "35613000"), [("capability", "UNCR", "CPV:35613000"),
                                                       ("domain", "AIR", "CPV:35613000")])
        self.assertEqual(classify("CPV", "35610000")[0][1], "PLAT")
        self.assertEqual(classify("CPV", "35613000-4")[0][1], "UNCR")  # ДК021 check digit

    def test_family_level_cpv_is_defence_without_guess(self):
        for code in ("35300000", "35400000", "35600000", "35700000", "73420000"):
            self.assertTrue(is_defence_cpv(code), code)
        self.assertEqual(classify("CPV", "35700000"), [])
        self.assertEqual(classify("CPV", "35400000"), [("domain", "LAND", "CPV:35400000")])

    def test_civil_security_cpv_is_not_defence(self):
        for code in ("35110000", "35111400", "35200000", "32000000"):
            self.assertFalse(is_defence_cpv(code), code)
            self.assertEqual(classify("CPV", code), [])

    def test_naics_and_psc(self):
        self.assertEqual(classify("NAICS", "336992")[0][1], "PLAT")
        self.assertEqual(classify("PSC", "1420")[0][1], "MSL")
        self.assertEqual(classify("PSC", "AC13"), [])
        self.assertTrue(is_rnd("PSC", "AC13"))
        self.assertEqual(classify("NAICS", "999999"), [])

    def test_best_layer(self):
        self.assertEqual(best_layer(["D", "B", "C"]), "B")
        self.assertEqual(best_layer(["D", "A"]), "A")
        self.assertIsNone(best_layer([]))


if __name__ == "__main__":
    unittest.main()
