"""Normalization of company names and country codes (EXP-012).

Conservative by design: the normalized name is used for *exact* comparison only.
Two companies are never merged just because normalized names look similar.
"""
import re
import unicodedata

# ISO 3166-1 alpha-3 -> alpha-2 for countries that appear in the sources of wave 1
# plus the excluded ones (RU, CN) so that exclusion works on alpha-3 inputs too.
ISO3_TO_ISO2 = {
    "AUT": "AT", "BEL": "BE", "BGR": "BG", "HRV": "HR", "CYP": "CY", "CZE": "CZ", "DNK": "DK",
    "EST": "EE", "FIN": "FI", "FRA": "FR", "DEU": "DE", "GRC": "GR", "HUN": "HU", "IRL": "IE",
    "ITA": "IT", "LVA": "LV", "LTU": "LT", "LUX": "LU", "MLT": "MT", "NLD": "NL", "POL": "PL",
    "PRT": "PT", "ROU": "RO", "SVK": "SK", "SVN": "SI", "ESP": "ES", "SWE": "SE", "NOR": "NO",
    "ISL": "IS", "CHE": "CH", "GBR": "GB", "USA": "US", "CAN": "CA", "UKR": "UA", "AUS": "AU",
    "NZL": "NZ", "JPN": "JP", "KOR": "KR", "ISR": "IL", "TUR": "TR", "IND": "IN", "PAK": "PK",
    "MYS": "MY", "SGP": "SG", "ARE": "AE", "SAU": "SA", "BRA": "BR", "MEX": "MX", "ZAF": "ZA",
    "RUS": "RU", "CHN": "CN", "BLR": "BY", "HKG": "HK", "MAC": "MO", "TWN": "TW",
}
COUNTRY_NAMES = {"україна": "UA", "ukraine": "UA", "united states": "US", "united kingdom": "GB"}

# Legal-form tokens that are equivalent for matching; "AB" and "Aktiebolag" must compare equal
# (GLEIF stores "SAAB Aktiebolag" while sources say "Saab AB").
LEGAL_FORMS = [
    r"aktiebolag", r"ab", r"aktieselskab", r"a/s", r"as", r"asa", r"oy", r"oyj", r"gmbh", r"ag", r"kg",
    r"se", r"sa", r"s\.a\.", r"sas", r"sarl", r"s\.r\.l\.", r"srl", r"spa", r"s\.p\.a\.", r"bv", r"b\.v\.",
    r"nv", r"n\.v\.", r"plc", r"ltd", r"limited", r"llc", r"inc", r"incorporated", r"corp", r"corporation",
    r"co", r"company", r"sp\. z o\.o\.", r"sp z oo", r"s\.r\.o\.", r"spol\. s r\.o\.", r"a\.s\.",
    r"тов", r"товариство з обмеженою відповідальністю", r"пат", r"прат", r"ат", r"дп",
    r"the",
]
_FORM_RE = re.compile(r"(?<![\w])(" + "|".join(sorted(LEGAL_FORMS, key=len, reverse=True)) + r")(?![\w])")
_PREFIX_RE = re.compile(r"^(?:[A-Z]{2})_(?=\S)")  # TED artefact: "EN_Weibel Scientific A/S"


def clean_name(name):
    """Human-readable cleanup: strip TED language prefixes, quotes and extra spaces."""
    if not name:
        return ""
    name = _PREFIX_RE.sub("", str(name).strip())
    name = name.replace(" ", " ").replace("\n", " ")
    name = re.sub(r"[\"«»“”„]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def name_norm(name):
    """Key for exact comparison: lowercase, no accents, no legal forms, no punctuation."""
    text = clean_name(name).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ")
    text = _FORM_RE.sub(" ", text)
    text = re.sub(r"[^\w]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def country_iso2(value):
    """Return ISO2 code or None. Accepts ISO2, ISO3 and a few country names."""
    if not value:
        return None
    if isinstance(value, (list, tuple)):
        codes = {country_iso2(v) for v in value if v}
        codes.discard(None)
        return codes.pop() if len(codes) == 1 else None
    text = str(value).strip()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    if len(text) == 3 and text.isalpha():
        return ISO3_TO_ISO2.get(text.upper())
    return COUNTRY_NAMES.get(text.lower())


EXCLUDED_COUNTRIES = {"RU", "CN"}
