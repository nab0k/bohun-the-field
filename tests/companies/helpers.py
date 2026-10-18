"""Shared offline helpers: fixtures and a fake HTTP client (no network in tests)."""
import json
import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name):
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


class FakeHttp:
    """Routes by URL prefix. `post` / `get` map prefix -> response (dict) or callable(url, body) -> dict."""

    def __init__(self, post=None, get=None):
        self.post, self.get, self.calls = post or {}, get or {}, []

    @staticmethod
    def _route(table, url, body=None):
        for prefix in sorted(table, key=len, reverse=True):
            if url.startswith(prefix):
                value = table[prefix]
                return value(url, body) if callable(value) else value
        return None

    def post_json(self, url, body):
        self.calls.append(("POST", url, body))
        return self._route(self.post, url, body)

    def get_json(self, url, accept=None):
        self.calls.append(("GET", url, None))
        return self._route(self.get, url)


class FakeGleif:
    """Stand-in for sources.gleif.Gleif built from fixtures/gleif.json."""

    def __init__(self, extra_parents=None, extra_exact=None):
        data = fixture("gleif.json")
        self.exact = {}
        for key, rows in data["by_legal_name"].items():
            for r in rows:
                self.exact.setdefault((r["legalName"], r["country"]), {"lei": r["id"], "legal_name": r["legalName"],
                                                                         "country": r["country"]})
        self.parents = dict(data["ultimate_parent"])
        self.exceptions = dict(data["ultimate_parent_exception"])
        self.parents.update(extra_parents or {})
        for (name, country), lei in (extra_exact or {}).items():
            self.exact[(name, country)] = {"lei": lei, "legal_name": name, "country": country}

    def find_exact(self, name, country):
        from collectors.companies.normalize import name_norm
        hits = [v for (n, c), v in self.exact.items() if name_norm(n) == name_norm(name) and c == country]
        return hits[0] if len(hits) == 1 else None

    def ultimate_parent(self, lei):
        p = self.parents.get(lei)
        if p:
            return "parent", {"lei": p["id"], "legal_name": p["legalName"], "country": p["country"]}
        if lei in self.exceptions:
            return "exception", self.exceptions[lei]["reason"]
        return "none", None
