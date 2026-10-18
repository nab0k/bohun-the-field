"""One-time import of the existing Notion DB | Companies cards as leads (EXP-012, AC5).

Input: local snapshot JSON pages (BOHUN-local/docs/sources/notion/collections/<batch>/companies/pages/*.json).
PRIVATE data: never run this in CI or commit the snapshot. Nothing is written back to Notion.
Each card keeps its Notion page id; its previous assessment is kept as a `legacy_assessment` signal.
"""
import glob
import json
import os
import re

from ..model import Mention, Record, SignalIn
from ..normalize import clean_name, country_iso2
from ..taxonomy import LEGACY_PRODUCT, LEGACY_SIGNAL

SOURCE = "legacy_notion"
LAYER = {"A_CONFIRMED": "A", "B_MARKET_DECLARED": "B", "C_DUAL_USE_POTENTIAL": "C", "D_LEAD_ONLY": "D"}
FLAG_MAP = {"CHINA": "OWNERSHIP_LINK_CN"}
SOURCE_TYPE_SIGNAL = {"association": "association", "company_site": "self_declared", "exhibition": "exhibition",
                      "eu_fund": "grant", "nato": "ncage", "wikidata": "association", "other": "association"}
_PAGE_ID = re.compile(r"([0-9a-f]{32})$")


def page_id(url):
    m = _PAGE_ID.search((url or "").replace("-", ""))
    return m.group(1) if m else None


def parse(row):
    pid = page_id(row.get("url"))
    if not pid or not row.get("Name"):
        return None
    try:
        flags = json.loads(row.get("Flags") or "[]")
    except ValueError:
        flags = []
    cats = []
    for value, table, field in ((row.get("Product"), LEGACY_PRODUCT, "product"), (row.get("Signal"), LEGACY_SIGNAL, "signal")):
        if value and value in table:
            caps, doms = table[value]
            basis = f"LEGACY:{field}:{value}"
            cats += [("capability", c, basis) for c in caps] + [("domain", d, basis) for d in doms]
    layer = LAYER.get(row.get("Defence evidence layer"))
    origin = SOURCE_TYPE_SIGNAL.get(row.get("Source type"), "association")
    # A legacy discovery frame never upgrades a lead: its own signal is always layer D;
    # the previous human/AI assessment is carried separately as legacy_assessment.
    signals = [SignalIn(type=origin, layer="D", counterpart=row.get("Source type") or None,
                        url=row.get("Source URL") or None)]
    if layer:
        signals.append(SignalIn(type="legacy_assessment", layer=layer, url=row.get("url")))
    mention = Mention(
        name=clean_name(row["Name"]), country=country_iso2(row.get("Country")),
        city=row.get("City") or None, website=row.get("Website") or None,
        notion_page_id=pid,
        flags=[FLAG_MAP.get(f, f"LEGACY:{f}") for f in flags]
        + ([f"LEGACY_REVIEW:{row['Review status']}"] if row.get("Review status") else []),
        legacy_categories=cats, signals=signals)
    return Record(SOURCE, pid, row, [mention])


def load_snapshot(pages_dir):
    rows = []
    for path in sorted(glob.glob(os.path.join(pages_dir, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            rows += json.load(fh).get("results") or []
    return rows


class Legacy:
    """Adapter over a list of snapshot rows; cursor = index of the next row."""
    source = SOURCE

    def __init__(self, rows):
        self.rows = rows

    def fetch(self, http, cursor, limit):
        start = int(cursor or 0)
        chunk = self.rows[start:start + limit]
        records = [r for r in (parse(row) for row in chunk) if r]
        nxt = start + len(chunk)
        return records, (str(nxt) if nxt < len(self.rows) else None)
