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


_ID_NOTE = re.compile(r"\([^)]*\)")
_ID_PREFIX = re.compile(r"^(?:[a-z]{1,3}:|ORG-)", re.I)


def national_id(value):
    """Registry number as published by procurement portals -> comparable form (EXP-012 T08 finding 5).

    't:687370703' -> '687370703'; 'HRB 79012 (Voxtronic Deutschland GmbH)' -> 'HRB79012';
    'ORG-27661440' -> '27661440'; 'DE 133556136' -> 'DE133556136'. Returns None if nothing is left.
    """
    text = _ID_NOTE.sub(" ", str(value or ""))
    text = _ID_PREFIX.sub("", text.strip())
    text = re.sub(r"[\s.]+", "", text).upper()
    return text or None


# Buyers that make an award a defence signal (layer A). Police, ports, foreign ministries and other
# public buyers of CPV 35 items are security / dual-use (layer C), see T08 finding 1.
DEFENCE_BUYER = re.compile(
    r"armasuisse|agencja uzbrojenia|ministry of defen|ministerstwo obrony|bundesamt für ausrüstung|baainbw|"
    r"bundeswehr|verteidigung|direction générale de l.armement|\bdga\b|ministère de la défense|"
    r"ministère des armées|forsvar|försvar|puolustus|defence equipment|\bmod\b|ministerie van defensie|"
    r"ministero della difesa|ministerio de defensa|ocrsg|\bnspa\b|\bocar\b|european defence agency|"
    r"unitatea militar|military unit|jednostka wojskowa|armed forces|\barmée\b|esercito|ejército|"
    r"ministerstvo obrany|aizsardzības|krašto apsaugos|kaitsevä|kaitseinvesteeringute|"
    r"міністерство оборони|"
    r"військова частина|"
    r"агенція оборонних закупівель|"
    r"національна гвардія|"
    r"збройних сил",
    re.I)


def is_defence_buyer(name):
    return bool(DEFENCE_BUYER.search(name or ""))
