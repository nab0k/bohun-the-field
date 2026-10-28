"""TAXONOMY v0 (EXP-012): code -> capability/domain mapping and signal -> layer rules.

Every assigned category keeps its basis (e.g. "CPV:35613000"). Unknown codes give no category:
the classifier never guesses. Mapping is accepted for MVP with POST-MVP REVIEW (Q03).
"""

CAPABILITIES = ("PLAT", "UNCR", "WPN", "MSL", "SENS", "C4I", "EWCY", "SOFT", "PROP", "COMP",
                "MATL", "MFG", "PROT", "TEST", "SERV")
DOMAINS = ("LAND", "AIR", "MAR", "SPACE", "CYBER_EM", "JOINT")

# CPV (EU procurement): longest prefix wins. Only defence sub-families of division 35.
# 351 (emergency/security) and 352 (police) are deliberately absent: civil security, not defence.
CPV_RULES = {
    "353": (["WPN"], []),            # weapons, ammunition and associated parts
    "354": ([], ["LAND"]),           # military vehicles and associated parts (family level)
    "3541": (["PLAT"], ["LAND"]),    # armoured military vehicles
    "3542": (["COMP"], ["LAND"]),    # parts for military vehicles
    "355": (["PLAT"], ["MAR"]),      # warships and associated parts
    "356": ([], []),                 # military aircraft, missiles and spacecraft (family level)
    "3561": (["PLAT"], ["AIR"]),     # military aircraft
    "35613": (["UNCR"], ["AIR"]),    # unmanned aerial vehicles
    "3562": (["MSL"], []),           # missiles
    "3563": (["PLAT"], ["SPACE"]),   # military spacecraft and satellites
    "3564": (["COMP"], ["AIR"]),     # parts for military aerospace equipment
    "357": ([], []),                 # military electronic systems (family level: defence, no capability guess)
    "3571": (["C4I"], []),           # command, control, communication systems
    "3572": (["SENS"], []),          # ISTAR, radar
    "3573": (["EWCY"], ["CYBER_EM"]),# electronic warfare and countermeasures
    "3574": (["TEST"], []),          # combat simulators
    "7342": (["TEST"], []),          # development of military equipment (R&D) -> also role RND
    "7343": (["TEST"], []),          # test and evaluation of military equipment
}
DEFENCE_CPV_PREFIXES = tuple(CPV_RULES)

NAICS_RULES = {
    "336411": (["PLAT"], ["AIR"]), "336412": (["PROP"], ["AIR"]), "336413": (["COMP"], ["AIR"]),
    "336414": (["MSL"], ["SPACE"]), "336415": (["PROP"], []), "336419": (["COMP"], []),
    "332992": (["WPN"], []), "332993": (["WPN"], []), "332994": (["WPN"], []),
    "336992": (["PLAT"], ["LAND"]), "334511": (["SENS"], []), "336611": (["PLAT"], ["MAR"]),
    "334220": (["C4I"], []), "334290": (["C4I"], []), "334413": (["COMP"], []),
    "325920": (["WPN"], []),
}

# PSC (US federal product/service codes): prefix rules.
PSC_RULES = {
    "10": (["WPN"], []), "13": (["WPN"], []), "14": (["MSL"], []), "15": (["PLAT"], ["AIR"]),
    "16": (["COMP"], ["AIR"]), "19": (["PLAT"], ["MAR"]), "23": (["PLAT"], ["LAND"]),
    "28": (["PROP"], []), "58": (["C4I"], []), "59": (["COMP"], []),
    "J0": (["SERV"], []), "K0": (["SERV"], []),
}
RND_HINTS = {"NAICS": ("541715",), "PSC": ("AC",), "PSC_GROUP": ("AC",), "CPV": ("7342", "7343")}

# Legacy Notion `Product` / `Signal` values -> new axes (TAXONOMY v0, migration table).
LEGACY_PRODUCT = {
    "land": ([], ["LAND"]), "air": ([], ["AIR"]), "naval": ([], ["MAR"]),
    "munitions": (["WPN"], []), "smallarms": (["WPN"], []), "missiles": (["MSL"], []),
    "drones": (["UNCR"], []), "software": (["SOFT"], []), "propulsion": (["PROP"], []),
    "components": (["COMP"], []), "testing": (["TEST"], []),
    # "electronics" is ambiguous (SENS/C4I/COMP): intentionally unmapped, needs review.
}
LEGACY_SIGNAL = {
    "UAS / UGV": (["UNCR"], []), "SOFTWARE": (["SOFT"], []), "PROPULSION": (["PROP"], []),
    "COMPONENTS": (["COMP"], []), "TESTING & VALIDATION": (["TEST"], []),
    "COMMUNICATIONS": (["C4I"], []), "INDUSTRIAL CAPACITY": (["MFG"], []),
}

# Signal type -> evidence layer (A strongest). See TAXONOMY v0 "Сигналы".
SIGNAL_LAYER = {
    "contract": "A", "subcontract": "A", "grant": "A", "tender_award": "A", "export_licence": "A",
    "ncage": "B", "registry_code": "B", "self_declared": "B", "exhibition": "C",
    "fund_portfolio": "C", "association": "D", "news": "D",
}
LAYER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _prefix_lookup(code, rules):
    code = (code or "").strip().upper().split("-")[0]
    for length in range(len(code), 0, -1):
        hit = rules.get(code[:length])
        if hit:
            return code[:length], hit
    return None, None


def classify(scheme, code):
    """Return list of (axis, value, basis) for one code. Empty list if unknown."""
    scheme = scheme.upper()
    if scheme == "NAICS":
        hit = NAICS_RULES.get(str(code).strip())
        prefix = str(code).strip()
    elif scheme == "CPV":
        prefix, hit = _prefix_lookup(code, CPV_RULES)
    elif scheme == "PSC":
        prefix, hit = _prefix_lookup(code, PSC_RULES)
    else:
        return []
    if not hit:
        return []
    basis = f"{scheme}:{str(code).strip().split('-')[0]}"
    caps, doms = hit
    return [("capability", c, basis) for c in caps] + [("domain", d, basis) for d in doms]


def is_defence_cpv(code):
    return _prefix_lookup(code, CPV_RULES)[1] is not None


def is_rnd(scheme, code):
    code = str(code or "").strip().upper()
    return any(code.startswith(p) for p in RND_HINTS.get(scheme.upper(), ()))


def best_layer(layers):
    layers = [l for l in layers if l in LAYER_ORDER]
    return min(layers, key=LAYER_ORDER.get) if layers else None
