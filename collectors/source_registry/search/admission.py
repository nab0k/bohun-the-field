"""A1SD-02 / A2SA: admission of source candidates by rules, into the shadow A3SL registry (EXP-034).

Pure decision over an *observation* of one candidate (what the network check saw). No network here: the live
observer is a separate step that needs its own permission. Spec: docs/experiments/EXP-033-product-module-map/
specs/A_SOURCES.md, A2SA; design/A.md, A2SA.

Checks in order; the first one not passed stops:
 1 reachable, redirect stays on the same site      fail -> reject (transient error -> retry, 3rd time reject)
 2 robots allow reading                            fail -> reject
 3 delivery found: feed / api / page / dataset     doubt -> disputed
 4 language determined                             doubt -> disputed
 5 country determined                              never a rejection: unknown -> international (main-chat note)
 6 not a duplicate of the list                     fail -> reject; same host other section -> disputed
 7 fresh: a dated item within 60 days              doubt -> disputed
 8 topic share >= 20% (media-like types only)      doubt -> disputed
All passed -> accept (SAMPLE). The spec defines no automatic rejection for 3, 4, 7, 8, so they go to disputed.
"""
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import registry  # noqa: E402

RULES_VERSION = "a2sa-shadow-0.9"  # 0.2: fixes after the 18.11 shadow run; 0.3: no-news sites kept (D-20261118-A1SD-NO-NEWS-KEEP)
FRESH_DAYS = 60
REJECT_COOLDOWN_DAYS = 90
TOPIC_MIN_SHARE = 0.20
TOPIC_MIN_SAMPLE = 5
MAX_TRANSIENT = 2
TYPES = ("media", "company_news", "agency", "procurement", "grants", "investment", "company_registry",
         "association", "exhibition", "research")
TOPIC_EXEMPT = {"procurement", "grants", "company_registry"}  # topic is filtered per record in A4SC
# 0.9 (A1SD-04, 21.11): the judge read the site and is sure it is a defence source. For these kinds of sources the
# keyword count of headlines does not overrule it (DARPA and the Defence Innovation Network were "general" by keywords),
# and an undated page is accepted: the page collector (A4SC-03) dates every article itself.
JUDGE_SURE = 0.8
JUDGE_OVERRULES_TOPIC = {"procurement", "grants", "investment", "agency", "research", "association", "exhibition",
                         "company_news"}


def judge_sure(obs):
    v = obs.get("judge") or {}
    try:
        return v.get("specialised") is True and float(v.get("confidence") or 0) >= JUDGE_SURE
    except (TypeError, ValueError):
        return False
OFFICIAL_TYPES = {"agency", "procurement", "grants", "company_registry"}
DELIVERIES = ("feed", "api", "page", "dataset")

_POLICY = HERE.parent / "discovery/policy.proposed.json"
DOMAIN_TERMS = [t.casefold() for t in json.loads(_POLICY.read_text(encoding="utf-8"))["rules"]["domain"]]
# categories-0.2 (20.11): defence words of the ten languages added to the search. The policy file above is pinned by
# the shared Inoreader coordinator and cannot change; these live here. Stems are kept narrow (Greek "στρατιωτ", not
# "στρατ", which also matches "strategy"; Hungarian "honvéd"/"katonai", not the civil-protection "védelmi").
EXTRA_DOMAIN_TERMS = ["militær", "våpen", "våben", "άμυν", "στρατιωτ", "ένοπλ", "πολεμικ", "honvéd", "katonai",
                      "hadsereg", "hadiipar", "fegyver", "defesa", "forças armadas", "exército", "marinha", "armamento",
                      "отбран", "военн", "въоръж", "армия", "obran", "odbran", "vojn", "vojsk", "oružj", "karin",
                      "ginkl", "militār", "bruņot", "armij", "sõjav", "relvaj", "relvast", "kaitseväe",
                      # categories-0.3 (20.11, waves 2-4). Korean "군" alone also means "county" and is left out.
                      "savunma", "askeri", "mühimmat", "silahlı kuvvet", "عسكري", "الدفاع", "دفاعي", "الجيش",
                      "ذخيرة", "ביטחוני", "צבאי", "צה\"ל", "תחמושת", "防衛", "軍事", "自衛隊", "兵器", "弾薬", "국방",
                      "방산", "방위산업", "군사", "무기", "国防", "军事", "军工", "武器", "军队", "防务", "pertahanan",
                      "militer", "alutsista", "quốc phòng", "quân sự", "quân đội", "vũ khí", "กลาโหม", "ทหาร",
                      "อาวุธ"]
DOMAIN_TERMS += [t for t in EXTRA_DOMAIN_TERMS if t not in DOMAIN_TERMS]
# Terms that are too broad alone for a source's theme (space/cyber/satellite news is often civil).
WEAK_TERMS = {"space", "satellite", "cyber", "aerospace", "radar", "mod "}

CCTLD = {"ua": "UA", "pl": "PL", "de": "DE", "fr": "FR", "uk": "GB", "it": "IT", "es": "ES", "nl": "NL", "be": "BE",
         "se": "SE", "no": "NO", "dk": "DK", "fi": "FI", "ee": "EE", "lv": "LV", "lt": "LT", "cz": "CZ", "sk": "SK",
         "ro": "RO", "bg": "BG", "hu": "HU", "at": "AT", "ch": "CH", "pt": "PT", "gr": "GR", "ie": "IE", "us": "US",
         "ca": "CA", "au": "AU", "nz": "NZ", "jp": "JP", "kr": "KR", "tr": "TR", "il": "IL", "in": "IN", "si": "SI",
         "hr": "HR", "md": "MD", "ge": "GE", "lu": "LU", "is": "IS", "rs": "RS", "br": "BR", "mx": "MX", "sg": "SG",
         "tw": "TW", "za": "ZA", "ae": "AE", "sa": "SA", "ru": "RU", "cn": "CN", "by": "BY",
         # waves 2-4 (20.11); generic-use domains (.co .tv .fm .ws .me .io) are left out on purpose
         "hk": "HK", "id": "ID", "my": "MY", "vn": "VN", "th": "TH", "ph": "PH", "pk": "PK", "bd": "BD", "qa": "QA", "kw": "KW", "jo": "JO", "om": "OM", "bh": "BH", "lb": "LB", "iq": "IQ", "ir": "IR", "af": "AF", "kz": "KZ", "kg": "KG", "tj": "TJ", "tm": "TM", "uz": "UZ", "mn": "MN", "lk": "LK", "np": "NP", "mm": "MM", "kh": "KH", "la": "LA", "bn": "BN", "mo": "MO", "ps": "PS", "sy": "SY", "ye": "YE", "ar": "AR", "cl": "CL", "pe": "PE", "ve": "VE", "ec": "EC", "bo": "BO", "py": "PY", "uy": "UY", "gt": "GT", "hn": "HN", "sv": "SV", "ni": "NI", "cr": "CR", "pa": "PA", "cu": "CU", "do": "DO", "gy": "GY", "sr": "SR", "pg": "PG", "fj": "FJ", "sb": "SB", "vu": "VU", "to": "TO", "ki": "KI", "mh": "MH", "pw": "PW", "nr": "NR"}
# 0.7 (review of the 95 accepted, 19.11): a national language says the country when host and hints say nothing
# (adriadefense.com, defensehere.com/tr were "international"). English, French, Spanish, German, Russian are spoken in
# many countries and say nothing.
LANG_COUNTRY = {"uk": "UA", "pl": "PL", "cs": "CZ", "sk": "SK", "ro": "RO", "it": "IT", "nl": "NL", "sv": "SE",
                "fi": "FI", "da": "DK", "no": "NO", "nb": "NO", "et": "EE", "lv": "LV", "lt": "LT", "hu": "HU",
                "bg": "BG", "el": "GR", "hr": "HR", "sl": "SI", "sr": "RS", "tr": "TR", "pt": "PT", "ka": "GE",
                "hy": "AM", "is": "IS", "mk": "MK", "sq": "AL", "be": "BY",
                "tr": "TR", "he": "IL", "ja": "JP", "ko": "KR", "vi": "VN", "th": "TH"}
# 0.7: official defence hosts that the gov/mil pattern misses (defensie.nl, defmin.fi were typed "media")
OFFICIAL_SITES = {"defensie.nl", "defmin.fi", "mapn.ro", "bmvg.de", "bundeswehr.de", "defense.gouv.fr", "difesa.it",
                  "defensa.gob.es", "mod.gov.ua", "mil.by", "army.cz", "mo.gov.cz", "forsvarsmakten.se",
                  "puolustusvoimat.fi", "forsvaret.no", "fmn.dk", "mil.gov.ua", "rnbo.gov.ua", "mon.gov.pl",
                  "mod.gov.uk", "defence.gouv.fr", "mindef.nl", "defensie.be", "mil.be", "bundesheer.at", "vtg.admin.ch",
                  "kam.lt", "mod.gov.lv", "mod.gov.ee", "mod.gov.ro", "mod.bg", "morh.hr", "mors.si", "mosr.sk",
                  "honvedelem.hu", "mod.mil.gr", "defesa.gov.pt", "msb.gov.tr", "ssb.gov.tr"}
STOPWORDS = {
    "en": {"the", "and", "of", "to", "for", "in", "on", "with", "new", "is"},
    "pl": {"i", "w", "na", "z", "do", "się", "dla", "nie", "oraz", "jest"},
    "de": {"der", "die", "und", "das", "für", "mit", "von", "den", "im", "ist"},
    "fr": {"le", "la", "les", "et", "des", "du", "pour", "en", "une", "sur"},
    "es": {"el", "la", "los", "las", "y", "de", "del", "para", "en", "con"},
    "it": {"il", "la", "di", "e", "per", "con", "del", "della", "che", "una"},
    "pt": {"o", "a", "os", "as", "e", "de", "do", "da", "para", "com"},
    "nl": {"de", "het", "en", "van", "een", "voor", "met", "op", "is", "naar"},
    "sv": {"och", "i", "för", "med", "av", "på", "som", "till", "det", "en"},
    "cs": {"a", "v", "na", "se", "pro", "je", "s", "z", "do", "o"},
}


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def parse_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def site_of(host):
    """Registered-site key without a public-suffix list: last two labels, three for co.uk-like suffixes."""
    labels = (host or "").lower().split(".")
    if len(labels) >= 3 and labels[-2] in ("co", "com", "gov", "org", "ac", "net", "mil") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


try:  # D-20261118-A1SD-READY-MODULES: Lingua when available (venv ~/.config/bohun/a1sd02), our rules otherwise
    import ready_modules as _ready
    _ready.detector()
    if _ready.Language is None:
        _ready = None
except Exception:  # noqa: BLE001 - any import/model problem means: use our own rules
    _ready = None


def detect_language(titles, html_lang):
    """Language of the titles; the page tag only confirms. Returns (lang or None, basis)."""
    if _ready is not None and len(titles) >= 3:
        lang, conf = _ready.detect_language(titles)
        if lang:
            tag = (html_lang or "").split("-")[0].lower() or None
            return lang, f"lingua:{conf}" + (f";tag:{tag}" if tag and tag != lang else "")
    return _detect_language_rules(titles, html_lang)


def _detect_language_rules(titles, html_lang):
    text = " ".join(titles).casefold()
    tag = (html_lang or "").split("-")[0].lower() or None
    cyr = len(re.findall(r"[а-яёіїєґ]", text))
    lat = len(re.findall(r"[a-z]", text))
    guess = None
    if cyr > lat:
        guess = "uk" if re.search(r"[іїєґ]", text) else ("ru" if re.search(r"[ыэъё]", text) else None)
    elif lat:
        words = re.findall(r"[a-zà-ÿąćęłńóśźżčďěňřšťůž]+", text)
        scores = {lang: sum(w in sw for w in words) for lang, sw in STOPWORDS.items()}
        best = max(scores, key=scores.get) if scores else None
        if best and scores[best] >= 2 and list(scores.values()).count(scores[best]) == 1:
            guess = best
    if guess and tag and guess != tag:
        return None, f"titles:{guess}!=tag:{tag}"
    if guess:
        return guess, "titles" + ("+tag" if tag else "")
    if tag and len(titles) < 3:
        return tag, "tag_only_few_titles"
    return (tag, "tag_only") if tag else (None, "unknown")


def detect_country(host, guessed, page_country, lang=None):
    tld = (host or "").rsplit(".", 1)[-1]
    hints = {h for h in (CCTLD.get(tld), (guessed or "").upper() or None, (page_country or "").upper() or None) if h}
    hints = {h for h in hints if h not in registry.INTERNATIONAL}
    if len(hints) == 1:
        return hints.pop(), "single_basis"
    if len(hints) > 1:
        return "international", "several_countries"
    if lang in LANG_COUNTRY:
        return LANG_COUNTRY[lang], "language"
    return "international", "no_single_country"


def topic_share(titles):
    def hit(t):
        terms = [d for d in DOMAIN_TERMS if d in t.casefold()]
        return any(d not in WEAK_TERMS for d in terms)
    n = len(titles)
    return (sum(hit(t) for t in titles) / n if n else 0.0), n


# Company websites of our base (companies.company): read from the database by the daily runner (set_base); the
# public repository never carries the list. Empty sets keep the rules working without it.
BASE = {"company": set(), "rnd": set()}


def set_base(company_hosts, rnd_hosts):
    BASE["company"], BASE["rnd"] = set(company_hosts), set(rnd_hosts)


def guess_type(candidate, host, delivery_url, site_name=None):
    t = candidate.get("guessed_type")
    if t in TYPES:
        return t, "candidate"
    h, path = host or "", urlsplit(delivery_url or "").path.lower()
    if re.search(r"(^|\.)(gov|mil|gouv|gob|bund|mod)\.|\.gov(\.[a-z]{2})?$|\.mil$", h) or site_of(h) in OFFICIAL_SITES:
        return "agency", "host"
    site = site_of(h)
    if site in BASE["company"] or h in BASE["company"]:  # 0.7: our base before name words (saab.com was "media")
        return "company_news", "base_company"
    if re.search(r"tender|procure|zamowien|vergabe|marches", h + path):
        return "procurement", "host_or_path"
    if re.search(r"grant|funding", h + path):
        return "grants", "host_or_path"
    if re.search(r"(^|\.)(uni|univ|university)[.-]|\.ac\.[a-z]{2}$|\.edu$|(^|\.)cas\.cz$|institut|research|"
                 r"hochschule|universit", h):
        return "research", "host"
    name = (site_name or "").lower()
    if re.search(r"institut|istituto|universi|univerz|uniwersytet|hochschule|research cent|forskning|tutkimus|"
                 r"akadem|academy|polytechn|politechn|fraunhofer|laborator|technolog(ical|y) cent|centro de investig|"
                 r"centre de recherche|forschungs|onderzoek|tutkimuskeskus|научн|інститут|університет|"
                 r"think tank|marshall fund|council on|centre for|center for|war on the rocks|stiftung|fondation|"
                 r"\bresearch\b|foundation for research|forschungsgesellschaft", name):
        return "research", "site_name"
    # 0.7: a journal/magazine/news site named after the army is media, not the agency (bundeswehr-journal.de)
    if not re.search(r"journal|magazin|news|zeitung|times|post\b|media|blog|revue|rivista|gazet|tidning|lehti", name) \
            and re.search(r"ministry|ministerstvo|ministerstwo|ministère|ministerio|ministero|ministerium|ministeerium|"
                 r"ministerie|departement|department of defen|armed forces|bundeswehr|agency|agentur|agence|agencia|"
                 r"keskus|centre for defence|defence materiel|procurement|zamówień|beschaffung|direction générale", name):
        return "agency", "site_name"
    if re.search(r"association|federation|federacja|asociaci|associazione|verband|chamber|izba|cluster|"
                 r"\bunion\b|syndicat|gifas|asd-europe|bdsv", h + " " + name):
        return "association", "host_or_site_name"
    if site in BASE["rnd"] or h in BASE["rnd"]:
        return "research", "base_rnd"
    # cycle 5: "expo" matched "export" (forsvarsexport.se); "worlddefenseshow" was missed
    if re.search(r"expo(?!rt)|exhibition|messe|salon|targi|veletrh|eurosatory|dsei|idex\b|defexpo|show\b|fair\b", h + " " + name):
        return "exhibition", "host_or_site_name"
    if re.search(r"ventures|capital\b|\.fund$|investment|investor", h + " " + name):
        return "investment", "host_or_site_name"
    if site in BASE["company"] or h in BASE["company"]:
        return "company_news", "base_company"
    if (candidate.get("found_via") or {}).get("channel") == "company_website":
        return "company_news", "found_via_company_website"
    return "media", "default"


def judged_type(obs, stype, type_basis):
    """The free-model judge's type replaces only a guess by default (no evidence in host, name or our base)."""
    verdict = obs.get("judge") or {}
    if type_basis in ("default", "found_via_company_website") and verdict.get("type") in TYPES:
        return verdict["type"], "judge:" + str(verdict.get("by"))
    return stype, type_basis


def decide(obs, known, now=None):
    """obs: one observation; known: callable(canonical_url, host) -> ('same'|'same_host'|None, channel_id)."""
    now = now or now_utc()
    cand = obs["candidate"]
    checks = []

    def out(decision, reason, **extra):
        rec = {"decision": decision, "reason_codes": [reason] if reason else [], "checks": checks,
               "rules_version": RULES_VERSION, "candidate_url": cand["url"]}
        rec.update(extra)
        if obs.get("judge"):  # 0.6: the judge's verdict is kept so every accept can be explained
            rec["judge"] = obs["judge"]
        return rec

    fetch = obs.get("fetch") or {}
    status = fetch.get("status")
    requested_host = registry.host_of(cand["url"])
    final_host = registry.host_of(fetch.get("final_url") or cand["url"])
    # 1 reachable
    if status in ("timeout", "connect_error") or (isinstance(status, int) and (status == 429 or status >= 500)):
        attempts = obs.get("prior_transient", 0) + 1
        checks.append({"check": 1, "result": "transient", "status": status, "attempt": attempts})
        if attempts > MAX_TRANSIENT:
            return out("reject", "unreachable")
        return out("retry", "transient_error")
    # 0.8 (owner 20.11 "Не нужно, решай сам"): nothing goes to the owner any more. What used to be disputed is held
    # for the next check, or decided by the judge's verdict where one exists.
    if status == "http_only":  # alive, but only over plain http
        checks.append({"check": 1, "result": "doubt", "basis": "http_only"})
        return out("hold", "http_only")
    if status == "RESPONSE_SIZE":  # 18.11: page larger than our transport reads; the site is alive
        checks.append({"check": 1, "result": "doubt", "basis": "page_too_large"})
        return out("hold", "page_too_large")
    if status != 200:
        checks.append({"check": 1, "result": "fail", "status": status})
        return out("reject", "unreachable")
    if site_of(requested_host) != site_of(final_host):
        # 18.11: both cases on the run were the same organisation under a new domain (.gr -> .com, rebrand).
        # A redirect cannot tell a rebrand from a sold domain, so it goes to a person instead of a rejection.
        checks.append({"check": 1, "result": "doubt", "basis": "redirect_to_other_domain", "final_host": final_host})
        return out("hold", "redirect_other_domain")
    checks.append({"check": 1, "result": "pass"})
    # 2 robots
    robots = obs.get("robots") or {}
    if robots.get("state") == "unreadable":
        checks.append({"check": 2, "result": "transient", "basis": robots.get("code")})
        attempts = obs.get("prior_transient", 0) + 1
        return out("reject", "robots_unreadable") if attempts > MAX_TRANSIENT else out("retry", "robots_unreadable")
    if not robots.get("allowed"):
        checks.append({"check": 2, "result": "fail"})
        return out("reject", "robots_denied")
    checks.append({"check": 2, "result": "pass", "basis": robots.get("state")})
    if obs.get("general_outlet"):  # 0.5: the home page already shows a general outlet (observer first stage)
        checks.append({"check": 8, "result": "general_home", **obs["general_outlet"]})
        site = registry.canonical_url(fetch.get("final_url") or cand["url"])
        country, _ = detect_country(registry.host_of(site), cand.get("guessed_country"), obs.get("page_country"))
        stype, type_basis = guess_type(cand, registry.host_of(site), site, obs.get("site_name"))
        stype, type_basis = judged_type(obs, stype, type_basis)
        return out("general", "general_outlet_home", delivery_url=site, delivery=None, type=stype,
                   type_basis=type_basis, country=country,
                   language=(obs.get("html_lang") or "").split("-")[0].lower() or None)
    # 3 delivery
    delivery, delivery_url = obs.get("delivery"), obs.get("delivery_url")
    items = [i for i in (obs.get("items") or []) if isinstance(i, dict) and i.get("title")]
    if delivery not in DELIVERIES or not delivery_url:
        # Owner 18.11: a live, readable site without news is kept for a separate pass by other criteria;
        # it is neither rejected nor sent to the owner's disputed batch.
        checks.append({"check": 3, "result": "hold", "basis": "no_delivery_found"})
        site = registry.canonical_url(fetch.get("final_url") or cand["url"])
        stype, type_basis = guess_type(cand, registry.host_of(site), site, obs.get("site_name"))
        stype, type_basis = judged_type(obs, stype, type_basis)
        country, _ = detect_country(registry.host_of(site), cand.get("guessed_country"), obs.get("page_country"))
        return out("hold", "no_news_site", delivery_url=site, delivery=None, type=stype, type_basis=type_basis,
                   country=country, language=(obs.get("html_lang") or "").split("-")[0].lower() or None)
    if delivery in ("feed", "page") and len(items) < 2:
        checks.append({"check": 3, "result": "doubt", "basis": f"{delivery}_with_{len(items)}_items"})
        return out("hold", "delivery_without_items", delivery=delivery)
    canon = registry.canonical_url(delivery_url)
    checks.append({"check": 3, "result": "pass", "delivery": delivery})
    titles = [i["title"] for i in items]
    sample = [{"title": i["title"][:200], "date": i.get("date")} for i in items[:5]]
    host = registry.host_of(delivery_url)
    stype, type_basis = guess_type(cand, host, delivery_url, obs.get("site_name"))
    stype, type_basis = judged_type(obs, stype, type_basis)
    base = {"delivery": delivery, "delivery_url": canon, "type": stype, "type_basis": type_basis, "sample_items": sample}
    # 4 language
    lang, lang_basis = detect_language(titles, obs.get("html_lang"))
    specialised = (obs.get("judge") or {}).get("specialised") is True
    if not lang and specialised:  # 0.8: the judge read the site and found it specialised; the language stays open
        lang, lang_basis = (obs.get("html_lang") or "").split("-")[0].lower() or None, "judge_specialised"
        checks.append({"check": 4, "result": "pass", "language": lang, "basis": lang_basis})
    elif not lang:
        checks.append({"check": 4, "result": "doubt", "basis": lang_basis})
        return out("hold", "language_uncertain", **base)
    else:
        checks.append({"check": 4, "result": "pass", "language": lang, "basis": lang_basis})
    # 5 country: never a rejection
    country, country_basis = detect_country(host, cand.get("guessed_country"), obs.get("page_country"), lang)
    checks.append({"check": 5, "result": "pass", "country": country, "basis": country_basis})
    base.update(language=lang, country=country)
    # 6 duplicate
    relation, channel = known(canon, host)
    if relation == "same":
        checks.append({"check": 6, "result": "fail", "channel_id": channel})
        return out("reject", "duplicate", **base)
    if relation == "same_host":
        # 0.8: another section of a known site is its own source when it passes the topic check below
        checks.append({"check": 6, "result": "pass", "basis": "same_host_other_section", "channel_id": channel})
    else:
        checks.append({"check": 6, "result": "pass"})
    # 7 freshness
    dates = [d for d in (parse_date(i.get("date")) for i in items) if d and d <= now + dt.timedelta(days=1)]
    if not dates and delivery == "page" and judge_sure(obs):
        checks.append({"check": 7, "result": "pass", "basis": "undated_page_judge_sure"})
    elif not dates:
        checks.append({"check": 7, "result": "doubt", "basis": "no_dates"})
        # 0.5 (D-20261118-A1SD-RETRO-PLAN-G2): only official sources without dates go to the owner; the rest
        # wait on hold for the next check (cycle 1: 23 of 66 disputed were undated, mostly not official)
        return out("hold", "no_dates", **base)  # 0.8: undated official pages wait for the page collector
    if dates:
        age = (now - max(dates)).days
        if age > FRESH_DAYS:
            checks.append({"check": 7, "result": "doubt", "newest_age_days": age})
            return out("hold", "stale", **base)  # 0.5: a dormant source is re-checked later, not the owner's
        checks.append({"check": 7, "result": "pass", "newest_age_days": age})
    # 8 topic
    if stype in TOPIC_EXEMPT:
        checks.append({"check": 8, "result": "skipped", "basis": "filtered_per_record_in_a4sc"})
    else:
        share, n = topic_share(titles)
        if n < TOPIC_MIN_SAMPLE:
            checks.append({"check": 8, "result": "doubt", "sample": n})
            return out("hold", "topic_sample_too_small", **base)  # 0.5: too few titles to judge -> next check
        if share < TOPIC_MIN_SHARE and stype in JUDGE_OVERRULES_TOPIC and judge_sure(obs):
            checks.append({"check": 8, "result": "pass", "share": round(share, 3), "sample": n,
                           "basis": "judge_sure_overrules_keywords"})
        elif share < TOPIC_MIN_SHARE:
            checks.append({"check": 8, "result": "doubt", "share": round(share, 3), "sample": n})
            # 0.5: a site that mostly writes about other things is a general outlet: kept in its own list for the
            # pass over its defence sections (decision 2 of the 18.11 review), never accepted, not the owner's
            return out("general", "topic_below_threshold", **base)
        else:
            checks.append({"check": 8, "result": "pass", "share": round(share, 3), "sample": n})
    if obs.get("judge_missing"):
        # 0.6: found by search, not judged (too few headlines or no model answered): keywords alone do not admit it;
        # it waits for the next check (recheck after RECHECK_AFTER_DAYS, with the judge)
        checks.append({"check": 8, "result": "doubt", "basis": "no_judge"})
        return out("hold", "no_judge", **base)
    return out("accept", None, **base)


SCHEMA = """
CREATE TABLE IF NOT EXISTS source_state(channel_id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL, type TEXT, delivery TEXT, country TEXT, language TEXT, since TEXT NOT NULL,
  rules_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_seen(candidate_url TEXT PRIMARY KEY, last_decision TEXT NOT NULL,
  decided_at TEXT NOT NULL, transient INTEGER NOT NULL DEFAULT 0, channel_id TEXT);
CREATE TABLE IF NOT EXISTS admission_decision(decision_id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_url TEXT NOT NULL,
  channel_id TEXT, decision TEXT NOT NULL, reasons_json TEXT NOT NULL, record_json TEXT NOT NULL,
  rules_version TEXT NOT NULL, decided_at TEXT NOT NULL, found_via TEXT);
"""
STATUS = {"accept": "accepted", "reject": "rejected", "disputed": "candidate", "hold": "no_news",
          "general": "general_outlet"}


def status_of(rec):
    """Registry status; a hold other than "no news" (stale, few titles, undated) waits for the next check."""
    if rec["decision"] == "hold" and rec["reason_codes"] != ["no_news_site"]:
        return "on_hold"
    return STATUS[rec["decision"]]


def setup(conn):
    conn.executescript(SCHEMA)


class transaction:
    """The registry connection is in autocommit mode; one decision = one explicit transaction."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def known_in(conn):
    def known(canon, host):
        row = conn.execute("SELECT channel_id FROM channel WHERE canonical_url=? UNION "
                           "SELECT channel_id FROM source_state WHERE canonical_url=? AND status!='candidate' "
                           "UNION SELECT channel_id FROM channel_alias WHERE alias_value=?",
                           (canon, canon, canon)).fetchone()
        if row:
            return "same", row[0]
        row = conn.execute("SELECT channel_id FROM channel WHERE host=? UNION "
                           # a general outlet / no-news / on-hold host is not a source yet: its defence section
                           # is a new source, not "another section of a known one" (decision 2, 18.11)
                           "SELECT channel_id FROM source_state WHERE canonical_url LIKE ? "
                           "AND status IN ('accepted', 'rejected')",
                           (host, f"https://{host}/%")).fetchone()
        return ("same_host", row[0]) if row else (None, None)
    return known


def admit(conn, observations, now=None, redecide=(), recheck=(), audit=()):
    """One cycle. One transaction per decision; a candidate rejected less than 90 days ago is not re-decided.
    audit (0.7, owner 19.11 "Да, чисти"): accepted sources checked again under the current rules and the judge. Their
    own channel does not count as "already known"; when the new decision is not to accept that same channel, the old
    channel leaves the accepted list (general outlet, on hold ...; a wrong delivery replaced by a better one on the same
    site is "rejected" as a delivery - no new status, so the shadow schema stays as it is).
    A transient failure keeps the source as it was."""
    now = now or now_utc()
    counts = {"accept": 0, "reject": 0, "disputed": 0, "hold": 0, "general": 0, "retry": 0, "skipped_recent_reject": 0,
              "skipped_decided": 0}
    known = known_in(conn)
    results = []
    for obs in observations:
        curl = registry.canonical_url(obs["candidate"]["url"])
        seen = conn.execute("SELECT last_decision, decided_at, transient FROM candidate_seen WHERE candidate_url=?",
                            (curl,)).fetchone()
        if seen and seen[0] == "reject" and now - parse_date(seen[1]) < dt.timedelta(days=REJECT_COOLDOWN_DAYS):
            counts["skipped_recent_reject"] += 1
            continue
        old_rules = seen and conn.execute(
            "SELECT rules_version FROM admission_decision WHERE candidate_url=? ORDER BY decision_id DESC LIMIT 1",
            (curl,)).fetchone()
        if seen and seen[0] in redecide and old_rules and old_rules[0] != RULES_VERSION:
            pass  # decided under older rules (e.g. 0.4 disputed) -> decide again under the current ones
        elif seen and seen[0] in ("hold", "disputed") and curl in recheck:
            pass  # on hold (stale, few titles, undated) and due, or disputed under rules < 0.8: decided again
        elif curl in audit and (not seen or seen[0] == "accept"):
            pass  # accepted under older rules or without the judge (or seeded with no record of how): checked again
        elif seen and seen[0] in ("accept", "disputed", "hold", "general"):
            counts["skipped_decided"] += 1
            continue
        obs = {**obs, "prior_transient": seen[2] if seen else 0}
        old = None
        if curl in audit:
            # 20.11: audit may name the accepted channel itself (sources seeded from the Mac have no candidate_seen row)
            old = audit.get(curl) if isinstance(audit, dict) else None
            if old is None and seen:
                old = conn.execute("SELECT channel_id FROM candidate_seen WHERE candidate_url=?", (curl,)).fetchone()[0]
        rec = decide(obs, (lambda c, h, _o=old: (None, None) if _o and known(c, h)[1] == _o else known(c, h))
                     if old else known, now)
        if old and rec["decision"] == "retry":
            counts["retry"] += 1
            results.append(rec)
            continue  # 0.7: a site that did not answer today keeps its accepted channel
        counts[rec["decision"]] += 1
        stamp = now.isoformat()
        with transaction(conn):
            channel = None
            if rec["decision"] in STATUS and rec.get("delivery_url"):
                channel = registry.channel_id_for(rec["delivery_url"])
                conn.execute("INSERT INTO source_state VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET "
                             "status=excluded.status, since=excluded.since, rules_version=excluded.rules_version, "
                             "type=excluded.type, country=excluded.country, language=excluded.language "
                             "WHERE source_state.status IN ('candidate', 'on_hold') OR source_state.channel_id=?",
                             (channel, rec["delivery_url"], status_of(rec), rec.get("type"), rec.get("delivery"),
                              rec.get("country"), rec.get("language"), stamp, RULES_VERSION, old))
            if old and old != channel:  # the audited source's old channel leaves the accepted list
                conn.execute("UPDATE source_state SET status=?, since=?, rules_version=? WHERE channel_id=? AND "
                             "status='accepted'", ("rejected" if rec["decision"] == "accept" else
                                                   status_of(rec) if rec["decision"] in STATUS else "candidate",
                                                   stamp, RULES_VERSION, old))
            transient = (seen[2] if seen else 0) + (1 if rec["decision"] == "retry" else 0)
            conn.execute("INSERT INTO candidate_seen VALUES (?,?,?,?,?) ON CONFLICT(candidate_url) DO UPDATE SET "
                         "last_decision=excluded.last_decision, decided_at=excluded.decided_at, "
                         "transient=excluded.transient, channel_id=excluded.channel_id",
                         (curl, rec["decision"], stamp, transient, channel))
            conn.execute("INSERT INTO admission_decision(candidate_url, channel_id, decision, reasons_json, record_json, "
                         "rules_version, decided_at, found_via) VALUES (?,?,?,?,?,?,?,?)",
                         (curl, channel, rec["decision"], json.dumps(rec["reason_codes"]),
                          json.dumps(rec, ensure_ascii=False, sort_keys=True), RULES_VERSION, stamp,
                          json.dumps(obs["candidate"].get("found_via"), ensure_ascii=False)))
        results.append(rec)
    return counts, results


def disputed_batch(conn):
    """What the owner sees: disputed sources with the reason and up to five dated titles."""
    rows = conn.execute("SELECT candidate_url, record_json FROM admission_decision d WHERE decision='disputed' AND "
                        "decision_id=(SELECT max(decision_id) FROM admission_decision x WHERE x.candidate_url=d.candidate_url) "
                        "ORDER BY candidate_url").fetchall()
    return [{"url": u, "reason": json.loads(r)["reason_codes"], "type": json.loads(r).get("type"),
             "sample_items": json.loads(r).get("sample_items", [])} for u, r in rows]


def open_db(path):
    conn = registry.connect(path)
    setup(conn)
    return conn
