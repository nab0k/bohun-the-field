"""Official historical Eurosatory exhibitor table: source-first discovery only."""
import hashlib
import pathlib
import re
from html.parser import HTMLParser

from ..model import Discovery, Record
from .http import SourceError

URL = "https://www.eurosatory.com/en/trends/exhibitors-2024"
SOURCE = "eurosatory_2024_exhibitors"
SHA = re.compile(r"^[a-f0-9]{64}$")


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.depth, self.rows, self.row, self.cell = [], 0, None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.depth == 0:
                self.rows = []
            self.depth += 1
        elif self.depth == 1:
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th") and self.row is not None and self.cell is None:
                self.cell = []

    def handle_data(self, data):
        if self.depth == 1 and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.tables.append(self.rows or [])
                self.rows = self.row = self.cell = None
        elif self.depth == 1:
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row.append(" ".join("".join(self.cell).split()))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                if self.row:
                    self.rows.append(self.row)
                self.row = None


def parse_html(raw):
    parser = Tables()
    parser.feed(raw.decode("utf-8-sig"))
    parser.close()
    tables = []
    for rows in parser.tables:
        for i, row in enumerate(rows):
            upper = [x.strip().upper() for x in row]
            if "EXHIBITOR" not in upper or "COUNTRY" not in upper:
                continue
            name_idx, country_idx = upper.index("EXHIBITOR"), upper.index("COUNTRY")
            pairs = []
            for cells in rows[i + 1:]:
                if len(cells) <= max(name_idx, country_idx) or not cells[name_idx] or not cells[country_idx]:
                    if any(cells):
                        raise SourceError("incomplete exhibitor/country row")
                    continue
                pairs.append((cells[name_idx].strip(), cells[country_idx].strip()))
            if pairs:
                tables.append(pairs)
    if len(tables) != 1:
        raise SourceError("expected precisely one populated EXHIBITOR / COUNTRY table")
    return list(dict.fromkeys(tables[0]))


def offset_from_cursor(cursor, digest):
    if not cursor:
        return 0
    if cursor == "done:" + digest:
        return None
    if cursor.startswith("done:"):
        if not SHA.fullmatch(cursor[5:]):
            raise SourceError("invalid done cursor")
        return 0
    bits = cursor.split(":")
    if len(bits) != 3 or bits[0] != "o" or not bits[1].isdigit() or not SHA.fullmatch(bits[2]):
        raise SourceError("invalid offset cursor")
    return int(bits[1]) if bits[2] == digest else 0


class Exhibitors2024:
    source = SOURCE
    def __init__(self, raw_dir=None):
        self.raw_dir = pathlib.Path(raw_dir) if raw_dir else None

    def fetch(self, http, cursor, limit):
        if limit < 1:
            raise ValueError("limit must be positive")
        raw = http.get_bytes(URL, accept="text/html")
        if not raw:
            raise SourceError("missing official HTML")
        digest = hashlib.sha256(raw).hexdigest()
        if self.raw_dir:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            path = self.raw_dir / f"eurosatory_2024_exhibitors-{digest}.html"
            try:
                with path.open("xb") as fh:
                    fh.write(raw)
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise SourceError("existing raw snapshot hash mismatch")
        offset = offset_from_cursor(cursor, digest)
        if offset is None:
            return [], "done:" + digest
        rows = parse_html(raw)
        if offset > len(rows):
            raise SourceError("cursor offset out of range")
        page = rows[offset:offset + limit]
        recs = []
        for name, country in page:
            key = hashlib.sha256((name + "\x1f" + country).encode("utf-8")).hexdigest()
            payload = dict(source_name=name, source_country=country, source_role="exhibitor",
                           source_url=URL, snapshot_sha256=digest, snapshot_bytes=len(raw))
            recs.append(Record(SOURCE, key, payload, mentions=[],
                               discoveries=[Discovery(name, reported_country=country, role="exhibitor")]))
        end = offset + len(page)
        return recs, ("done:" if end == len(rows) else f"o:{end}:") + digest
