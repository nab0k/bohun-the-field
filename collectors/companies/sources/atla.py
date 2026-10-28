"""ATLA (Japan, Acquisition, Technology & Logistics Agency): central procurement contracts (no key).

Source: monthly xlsx files on "契約に係る情報の公表（中央調達分）" (competitive "kyoso" and sole-source "zuikei").
License: 公共データ利用規約 PDL1.0 (free reuse incl. commercial, with attribution), checked 2026-10-18.

Checked on two files (07_zuikei-03, 08_kyoso-04): row 2 is the header, data starts at row 4.
- column F: contractor name AND address in one cell -> only the name is kept;
- column G: 法人番号 (13-digit corporate number) -> identifier JP_CORP;
- column D names the contracting officer -> never stored (personal data);
- sole-source files hold the primes (MHI, MELCO, NEC, KHI, Fujitsu ...); competitive files are mostly fuel.
Skipped: civil supplies (fuel, food, cleaning, transport services) and governments as counterparts
(US Navy / Air Force under FMS). Trading houses are kept with role SERVICES and flag TRADING_INTERMEDIARY.
Item names are Japanese free text without a classification code: no capability is guessed.
"""
import datetime as dt
import re
import unicodedata
import urllib.parse

from ..model import Mention, Record, SignalIn
from ..normalize import clean_name
from . import xlsx

BASE = "https://www.mod.go.jp/atla/souhon/supply/jisseki/rakusatu/"
INDEX = BASE + "index.html"
SOURCE = "atla_contracts"
BUYER = "Acquisition, Technology and Logistics Agency (ATLA), Japan"
DATA_START = 4  # 1-based sheet row
_FILE = re.compile(r'href="(kohyo_r(\d{2})/\d{2}_(kyoso|zuikei)-(\d{2})\.xlsx)"')
_PREFECTURE = re.compile(r"(北海道|東京都|大阪府|京都府|[^\s　]{2,3}県)")
CIVIL_ITEM = re.compile(r"燃料|軽油|ガソリン|重油|灯油|食料|糧食|給食|米穀|清掃|輸送役務|旅客|宿泊")
GOVERNMENT = re.compile(r"(省|庁|局|政府|軍)$")  # e.g. 米海軍省 (US Navy, FMS), 米国防脅威削減局 (DTRA)
TRADING_HOUSES = ("双日", "住商エアロシステム", "伊藤忠アビエーション", "三井物産エアロスペース", "丸紅エアロスペース",
                  "兼松", "山田洋行", "コーンズテクノロジー")


def list_files(html, kinds=("zuikei", "kyoso")):
    """Monthly files in fiscal-chronological order (FY = Reiwa year; April is month 1 of the FY)."""
    found = {}
    for path, year, kind, month in _FILE.findall(html):
        if kind in kinds:
            found[path] = (int(year), (int(month) - 4) % 12, kind)
    return sorted(found, key=found.get)


def excel_date(value):
    try:
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(value)))).isoformat()
    except (TypeError, ValueError):
        return None


def split_contractor(cell):
    """'三菱重工業株式会社 東京都千代田区…' -> ('三菱重工業株式会社', True). Second item: address is in Japan."""
    text = unicodedata.normalize("NFKC", cell or "").strip()
    first, newline, rest = text.partition("\n")
    if newline:  # ATLA writes "name\naddress"; a Japanese prefecture on line 2 marks a domestic seat
        return clean_name(first), bool(_PREFECTURE.match(rest.strip()))
    m = _PREFECTURE.search(text)
    if m and m.start() > 0:
        return clean_name(text[:m.start()]), True
    parts = re.split(r"\s{2,}|　", text, maxsplit=1)
    return clean_name(parts[0]), False


class Awards:
    source = SOURCE

    def __init__(self, kinds=("zuikei", "kyoso"), from_year=7):
        self.kinds, self.from_year = kinds, from_year

    def fetch(self, http, cursor, limit):
        """Cursor '<file>|<next sheet row>' or '<file>|END'. Files after the cursor file follow in order."""
        html = (http.get_bytes(INDEX) or b"").decode("utf-8", "replace")
        files = [f for f in list_files(html, self.kinds) if int(f[7:9]) >= self.from_year]
        start_file, start_row = None, DATA_START
        if cursor:
            start_file, _, pos = cursor.partition("|")
            start_row = None if pos == "END" else int(pos)
        if start_file in files:
            files = files[files.index(start_file):]
            if start_row is None:
                files = files[1:]
                start_row = DATA_START
        records, last = [], cursor
        for name in files:
            data = http.get_bytes(urllib.parse.urljoin(BASE, name))
            if data is None:
                continue
            sheet = xlsx.rows(data)
            first = start_row if name == start_file else DATA_START
            for idx in range(first - 1, len(sheet)):
                if len(records) >= limit:
                    return records, f"{name}|{idx + 1}"
                rec = self.parse(sheet[idx], name, idx + 1)
                if rec:
                    records.append(rec)
            last = f"{name}|END"
        return records, last

    @staticmethod
    def parse(row, file_name, row_no):
        item = unicodedata.normalize("NFKC", row.get("A") or "").strip()
        contractor = row.get("F")
        if not item or not contractor or CIVIL_ITEM.search(item):
            return None
        name, in_japan = split_contractor(contractor)
        if not name or GOVERNMENT.search(name):
            return None
        corp = (row.get("G") or "").strip()
        identifiers = [("JP_CORP", corp)] if re.fullmatch(r"\d{13}", corp) else []
        trading = any(t in name for t in TRADING_HOUSES)
        url = urllib.parse.urljoin(BASE, file_name)
        date = excel_date(row.get("E"))
        kind = "sole_source" if "_zuikei-" in file_name else "competitive"
        mention = Mention(
            # legal seat only from a Japanese address, never from the corporate number alone
            name=name, country="JP" if in_japan else None, identifiers=identifiers,
            supply_role="SERVICES" if trading else None, flags=["TRADING_INTERMEDIARY"] if trading else [],
            signals=[SignalIn(type="contract", counterpart=BUYER, date=date, url=url, layer="A")])
        # Slim payload: no contracting-officer name, no address.
        payload = {"file": file_name, "row": row_no, "item": item, "quantity": row.get("B"), "unit": row.get("C"),
                   "date": date, "contractor": name, "corp_no": corp or None, "amount_jpy": row.get("I"),
                   "kind": kind}
        return Record(SOURCE, f"{file_name}#{row_no}", payload, [mention])
