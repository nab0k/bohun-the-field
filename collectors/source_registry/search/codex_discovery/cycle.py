"""Bounded A1SD discovery cycle. Persists candidates and queue progress locally."""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import fcntl
from html.parser import HTMLParser
from itertools import islice, zip_longest
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote_plus, urljoin, urlsplit, urlunsplit

from .policy import Problem, identity_url
from .publication_evidence import PublicationPage, folded
from .radars import Budget, HTTP, feed_data
from .seed_provider import SCOPE, STOP, populate
from .web_search import search


ROUTES = {"feed", "api", "page", "dataset", "unknown"}
FEED_TYPES = {"application/rss+xml", "application/atom+xml"}
TRACKING = {"fbclid", "gclid", "msclkid"}

IGNORED_EXTENSIONS = {".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp"}


def channel_url(url):
    parts = urlsplit(identity_url(url))
    kept = []
    for part in parts.query.split("&"):
        key = unquote_plus(part.partition("=")[0]).lower()
        if not key.startswith("utm_") and key not in TRACKING:
            kept.append(part)
    return urlunsplit(parts._replace(query="&".join(kept)))


def cell(row):
    return "|".join(row[field] for field in ("country", "type", "language"))


class ListedLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.alternates = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href")
        if not href:
            return
        rel = (attrs.get("rel") or "").lower().split()
        if tag == "link" and "alternate" in rel and attrs.get("type") in FEED_TYPES:
            if len(self.alternates) < 12:
                self.alternates.append(href)
        if tag == "a" and len(self.links) < 500:
            self.active = (href, [])

    def handle_data(self, value):
        if self.active and len(self.active[1]) < 12:
            self.active[1].append(value[:100])

    def handle_endtag(self, tag):
        if tag == "a" and self.active:
            href, label = self.active
            if any(text.strip() for text in label):
                self.links.append((href, " ".join(" ".join(label).split())[:160]))
            self.active = None


def html_links(body):
    parser = ListedLinks()
    parser.feed(body.decode("utf-8", errors="replace"))
    parser.close()
    return parser


def safe_link(base, href):
    try:
        url = channel_url(urljoin(base, href))
    except (Problem, ValueError):
        return None
    parts = urlsplit(url)
    if Path(parts.path).suffix.lower() in IGNORED_EXTENSIONS:
        return None
    return url


def channel_evidence(response, url, route):
    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if route == "api":
        if mime != "application/json" and not mime.endswith("+json"):
            return None
        try:
            document = json.loads(response.body)
        except (ValueError, UnicodeError, RecursionError):
            return None
        if not isinstance(document, (list, dict)) or not document:
            return None
        return {"kind": "json_response", "alternates": []}
    if route == "feed":
        parsed = feed_data(response.body, url)
        if not parsed["entries"]:
            return None
        return {"kind": "feed_entries", "alternates": []}
    if route == "dataset":
        if mime not in {"text/csv", "application/csv", "application/json", "application/zip",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}:
            return None
        return {"kind": "dataset_response", "alternates": []} if response.body else None
    if mime != "text/html":
        return None
    page = html_links(response.body)
    if not page.links:
        return None
    alternates = [safe_link(url, link) for link in page.alternates]
    return {"kind": "publication_page", "alternates": list(dict.fromkeys(filter(None, alternates)))}


PUBLICATION = re.compile(r"\b(aktualnosci|aktualnosciach|wiadomosci|nowosci|news|press|komunikaty|publikacje|aktualnosci-branzowe)\b")


def publication_links(home, body):
    page = html_links(body)
    host = urlsplit(home).hostname.removeprefix("www.")
    links = []
    for href, hint, label in [
        *((href, "feed", "") for href in page.alternates),
        *((href, "page", name) for href, name in page.links),
    ]:
        url = safe_link(home, href)
        if not url or url == home or urlsplit(url).hostname.removeprefix("www.") != host:
            continue
        path = urlsplit(url).path
        if hint == "feed" or PUBLICATION.search(folded(path + " " + label)):
            links.append((url, hint))
    return list(dict.fromkeys(links))


ORGANIZATION = re.compile(r"\b(?:izb\w*|stowarzyszeni\w*|zwiazek|zwiazku|federacj\w*|klaster|klastr\w*)\b")
GENERIC_TOPIC = {"technologii", "technologie", "polska", "polsce", "aktualnosci", "temat"}


def publisher_host(url):
    return urlsplit(url).hostname.removeprefix("www.")


def official_identity(pages, url):
    polish_page = next((page for page in pages if
                        re.match(r"^pl(?:$|[-_])", page.lang or page.metadata.get("og:locale", ""), re.I)), None)
    if not polish_page:
        return None
    for page in pages:
        names = (page.metadata["og:site_name"],) if page.metadata.get("og:site_name") else (page.title,)
        for raw_name in names:
            name = " ".join(raw_name.split())[:200]
            if (ORGANIZATION.search(folded(name)) and
                    (publisher_host(url).endswith(".pl") or "polsk" in folded(name)
                     or polish_page.metadata.get("og:locale", "").lower() == "pl_pl")):
                return name
    return None


def checked_site(http, url, host, mime=None):
    result = http.request(url, robots=True)
    if result.status != 200:
        raise Problem(f"HTTP_{result.status}")
    if publisher_host(channel_url(result.url)) != host:
        raise Problem("PUBLISHER_REDIRECT")
    if mime and result.headers.get("content-type", "").partition(";")[0].lower() != mime:
        raise Problem("PUBLICATION_FORMAT")
    return result


def item_link(base, href):
    url = safe_link(base, href)
    return url if url and url != base and publisher_host(url) == publisher_host(base) else None


def dated_page_items(page, url, http):
    items, links = {}, set()
    for href, title, dates in page.article_links:
        link = item_link(url, href)
        if not link or link in links:
            continue
        links.add(link)
        if len(set(dates)) == 1:
            items[link] = {"url": link, "title": title, "date": dates[0].isoformat()}
        elif len(links) <= 3:
            try:
                article = checked_site(http, link, publisher_host(url), "text/html")
                detail = PublicationPage(article.body)
            except Problem as exc:
                if str(exc) in STOP:
                    raise
                continue
            published = detail.individual_date()
            if published:
                final = channel_url(article.url)
                links.add(final)
                items[final] = {"url": final, "title": (detail.heading.strip() or title)[:240],
                                "date": published.isoformat()}
    return list(items.values())


def dated_feed_items(result, url):
    items = {}
    for entry in feed_data(result.body, url, publication_only=True)["entries"][:20]:
        link = item_link(url, entry["url"])
        if link and entry["published_epoch"] is not None:
            published = datetime.fromtimestamp(entry["published_epoch"], timezone.utc).date().isoformat()
            items[link] = {"url": link, "title": entry["title"][:240], "date": published}
    return list(items.values())


def recent_evidence(items, topic, as_of):
    terms = {word[:6] for word in re.findall(r"[a-z]{5,}", folded(topic)) if word not in GENERIC_TOPIC}
    recent = sorted((item for item in items if 0 <= (as_of - date.fromisoformat(item["date"])).days <= 180),
                    key=lambda item: item["date"], reverse=True)
    if len(recent) < 2:
        raise Problem("PUBLICATION_DATES_INSUFFICIENT")
    topical = next((item for item in recent if terms
                    and (as_of - date.fromisoformat(item["date"])).days <= 90
                    and sum(term in folded(item["title"]) for term in terms) >= min(2, len(terms))), None)
    if topical is None:
        raise Problem("PUBLICATION_TOPIC_UNCONFIRMED")
    return [topical, *(item for item in recent[:3] if item != topical)][:3]


def thematic_section(http, response, page, url, topic, as_of):
    page_links = {link for href, _, _ in page.article_links if (link := item_link(url, href))}
    advertised = [target for target, route in publication_links(url, response.body) if route == "feed"]
    section_path = urlsplit(url).path.rstrip("/")
    advertised.sort(key=lambda target: not urlsplit(target).path.startswith(section_path + "/"))
    failures = []
    for target in advertised[:2]:
        try:
            feed = checked_site(http, target, publisher_host(url))
            feed_url = channel_url(feed.url)
            matching = [item for item in dated_feed_items(feed, feed_url) if item["url"] in page_links]
            if len(matching) < 2:
                raise Problem("FEED_SECTION_UNCONFIRMED")
            items = recent_evidence(matching, topic, as_of)
            return {"kind": "dated_publications", "alternates": [feed_url],
                    "preferred_delivery": {"route": "feed", "url": feed_url}, "publications": items}
        except Problem as exc:
            if str(exc) in STOP:
                raise
            failures.append(str(exc))
    items = dated_page_items(page, url, http)
    return {"kind": "dated_publications", "alternates": [],
            "preferred_delivery": {"route": "page", "url": url},
            "feed_checks": failures, "publications": recent_evidence(items, topic, as_of)}


def news_sections(url, response, page):
    sections = [url] if len(page.article_links) >= 2 else []
    sections.extend(target for target, route in publication_links(url, response.body) if route == "page"
                    and PUBLICATION.fullmatch(folded(urlsplit(target).path.rstrip("/").split("/")[-1])))
    return sections


def thematic_observations(seed, http, response, url, now):
    if response.headers.get("content-type", "").partition(";")[0].lower() != "text/html":
        raise Problem("PUBLICATION_FORMAT")
    initial = PublicationPage(response.body)
    sections = news_sections(url, response, initial)
    host = publisher_host(url)
    label = seed["label"] if seed["provider"] == "kig" and re.match(r"^pl(?:$|[-_])", initial.lang, re.I) else None
    label = label or official_identity([initial], url)
    home = urlunsplit(urlsplit(url)._replace(path="/", query=""))
    fetched = {url: (response, initial)}
    if home != url and (not sections or not label):
        try:
            home_response = checked_site(http, home, host, "text/html")
            home_url = channel_url(home_response.url)
            home_page = PublicationPage(home_response.body)
            fetched[home_url] = home_response, home_page
            sections.extend(news_sections(home_url, home_response, home_page))
            label = label or official_identity([home_page], home_url)
        except Problem as exc:
            if str(exc) in STOP:
                raise
    if not sections:
        raise Problem("PUBLICATION_SECTION_UNCONFIRMED")
    successful, reason = False, "PUBLISHER_UNVERIFIED"
    for target in list(dict.fromkeys(sections))[:2]:
        try:
            if target in fetched:
                result, page = fetched[target]
            else:
                result = checked_site(http, target, host, "text/html")
                page = PublicationPage(result.body)
            section = channel_url(result.url)
            name = label or official_identity([page, initial], section)
            if not name:
                raise Problem("PUBLISHER_UNVERIFIED")
            evidence = thematic_section(http, result, page, section, seed["topic"], date.fromisoformat(now[:10]))
        except Problem as exc:
            reason = str(exc)
            if reason in STOP:
                raise
            continue
        successful = True
        yield section, name, "page", {
            **evidence, "provider": seed["provider"], "provider_url": seed["provider_url"],
            "query": seed.get("query"), "publisher": name,
        }, url
    if not successful:
        raise Problem(reason)


def publisher_observations(seed, http, response, final):
    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if mime != "text/html":
        return
    for target, route in publication_links(final, response.body)[:2]:
        try:
            checked = http.request(target, robots=True)
            if checked.status != 200:
                continue
            url = channel_url(checked.url)
            if urlsplit(final).hostname.removeprefix("www.") != urlsplit(url).hostname.removeprefix("www."):
                continue
            evidence = channel_evidence(checked, url, route)
        except Problem as exc:
            if str(exc) in STOP:
                raise
            continue
        if evidence:
            yield url, seed["label"], route, {
                **evidence, "provider": seed["provider"], "provider_url": seed["provider_url"],
            }, final


def observations(seed, http, now=None):
    response = http.request(seed["url"], robots=True)
    if response.status != 200:
        raise Problem(f"HTTP_{response.status}")
    final = channel_url(response.url)
    if seed["mode"] == "publisher":
        if seed.get("topic"):
            if publisher_host(final) != publisher_host(seed["url"]):
                raise Problem("PUBLISHER_REDIRECT")
            yield from thematic_observations(seed, http, response, final, now)
        else:
            yield from publisher_observations(seed, http, response, final)
        return
    if seed["mode"] == "channel":
        evidence = channel_evidence(response, final, seed["route"])
        if evidence:
            yield final, seed["label"], seed["route"], evidence, final
        return

    if response.headers.get("content-type", "").split(";", 1)[0].lower() != "text/html":
        return
    links = html_links(response.body)
    for href, name in links.links:
        url = safe_link(final, href)
        if url and url != final and any(url.startswith(prefix) for prefix in seed["prefixes"]):
            yield url, name, "unknown", {"kind": "directory_link", "alternates": []}, final


def thematic_seeds(scope, http, visited, providers, topic, limit):
    catalog, audit = [], []
    if "kig" in providers:
        catalog, audit = populate(scope, http, channel_url, visited, limit=limit, providers=("kig",))
    web = []
    if "tavily" in providers and not any(row["reason"] in STOP for row in audit):
        hits, report = search(http, channel_url, topic, os.environ.get("TAVILY_API_KEY", "").strip())
        audit.append(report)
        hits.sort(key=lambda row: visited.get(row["url"], 0))
        web = [{**row, "country": scope[0], "type": scope[1], "language": scope[2], "mode": "publisher"}
               for row in hits]
    unique = {}
    for pair in zip_longest(web, catalog):
        for seed in pair:
            if seed is not None:
                unique.setdefault(seed["url"], {**seed, "topic": topic})
    return [{**seed, "queue_rank": rank}
            for rank, seed in enumerate(islice(unique.values(), limit))], audit


def run_cycle(seeds, gaps, known, state, http, *, max_seeds, max_new, per_seed, now):
    number = state["cycle"] + 1
    seeds_left = list(seeds)
    seen = set(state["candidates"]) | known
    rows, attempts = [], []
    provider_attempts = Counter()
    configured_cells = {cell(seed) for seed in seeds}
    stop = None
    for _ in range(min(len(seeds_left), max_seeds)):
        seed = min(seeds_left, key=lambda s: (
            state["cells"].get(cell(s), 0), -gaps.get(cell(s), 0),
            provider_attempts[s.get("provider")] if s.get("topic") else 0,
            state["seeds"].get(s["url"], 0), s.get("queue_rank", 0), s["url"],
        ))
        seeds_left.remove(seed)
        if not seed.get("topic"):
            state["seeds"][seed["url"]] = number
        state["cells"][cell(seed)] = number
        if seed.get("topic"):
            provider_attempts[seed.get("provider")] += 1
        if seed["mode"] == "channel" and seed["url"] in seen:
            attempts.append({"seed": seed["url"], "cell": cell(seed), "reason": "known_only", "known": 1})
            continue
        counts = Counter()
        reason = "ok"
        try:
            for url, label, route, evidence, origin in observations(seed, http, now):
                counts["observed"] += 1
                preferred = evidence.get("preferred_delivery")
                if url in seen or (preferred and preferred["url"] in seen):
                    counts["known"] += 1
                    continue
                row = {
                    "url": url, "host": urlsplit(url).hostname, "label": label,
                    "guessed_type": seed["type"], "guessed_country": seed["country"],
                    "guessed_language": seed["language"], "delivery_hint": route, "status": "new",
                    "found_at": now, "found_via": {"seed_url": seed["url"], "page_url": origin,
                                                  **evidence},
                }
                state["candidates"][url] = row
                seen.add(url)
                rows.append(row)
                counts["new"] += 1
                if counts["new"] >= per_seed or len(rows) >= max_new:
                    break
        except Problem as exc:
            reason = str(exc)
            if reason in STOP:
                stop = reason
        if reason == "ok":
            reason = "new_candidates" if counts["new"] else "known_only" if counts["known"] else "no_candidate"
        attempt = {"seed": seed["url"], "cell": cell(seed), "reason": reason, **counts}
        if seed.get("topic"):
            attempt["provider"] = seed["provider"]
            if not stop:
                state["seeds"][seed["url"]] = number
        attempts.append(attempt)
        if stop or len(rows) >= max_new:
            break
    state["cycle"] = number
    state["last_report"] = {
        "cycle": number, "status": "new_candidates" if rows else "empty",
        "new_candidates": rows, "attempts": attempts, "stop": stop,
        "seeds_remaining": len(seeds_left),
        "unseeded_gaps": sorted(key for key, weight in gaps.items() if weight and key not in configured_cells),
    }
    return state["last_report"]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink()
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_catalog(path):
    catalog = read_json(path)
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("catalog must be a nonempty list of seeds")
    urls = set()
    for seed in catalog:
        seed["url"] = channel_url(seed["url"])
        if seed["url"] in urls:
            raise ValueError("duplicate seed URL")
        urls.add(seed["url"])
        if seed["mode"] == "directory":
            seed["prefixes"] = [channel_url(prefix) for prefix in seed["prefixes"]]
            if not seed["prefixes"] or any(not prefix.endswith("/") for prefix in seed["prefixes"]):
                raise ValueError("directory prefixes must end with a slash")
        elif seed["mode"] != "channel" or seed["route"] not in ROUTES - {"unknown"}:
            raise ValueError("invalid seed mode or channel route")
        if any(not isinstance(seed[key], str) or not seed[key] or "|" in seed[key]
               for key in ("country", "type", "language", "label")):
            raise ValueError("invalid seed metadata")
    return catalog


def bounded(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"expected {low}..{high}")
        return number
    return parse


def main(argv=None, *, transport=HTTP):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", action="append", type=Path, default=[],
                        help="JSON seed list; repeat for independent input snapshots")
    parser.add_argument("--discover", nargs=3, metavar=("COUNTRY", "TYPE", "LANGUAGE"))
    parser.add_argument("--topic", help="topic for the Polish association discovery pilot")
    parser.add_argument("--discover-limit", type=bounded(1, 60), default=12)
    parser.add_argument("--seed-provider", choices=("kig", "wikidata", "tavily"), action="append",
                        help="use only the specified public seed providers")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--known", type=Path, required=True, help="JSON array of exact known channel URLs")
    parser.add_argument("--gaps", type=Path, help="JSON object with numeric weights by country|type|language")
    parser.add_argument("--max-seeds", type=bounded(1, 30), default=8)
    parser.add_argument("--max-new", type=bounded(1, 100), default=12)
    parser.add_argument("--per-seed", type=bounded(1, 30), default=3)
    parser.add_argument("--max-requests", type=bounded(1, 100))
    parser.add_argument("--max-seconds", type=bounded(1, 300))
    parser.add_argument("--fetch", action="store_true", required=True,
                        help="read public seed URLs, with no registry or subscription writes")
    args = parser.parse_args(argv)

    if not args.catalog and not args.discover:
        parser.error("provide --catalog or --discover")
    if args.discover and tuple(args.discover) != SCOPE:
        parser.error("supported scope is PL association pl")
    if args.seed_provider and not args.discover:
        parser.error("--seed-provider requires --discover")
    if args.topic is not None:
        args.topic = " ".join(args.topic.split())
        if not args.discover or not 1 <= len(args.topic) <= 160:
            parser.error("--topic requires --discover and 1–160 characters")
        if "wikidata" in (args.seed_provider or ()):
            parser.error("the thematic pilot supports KIG and Tavily")
        if (args.max_requests or 48) > 48 or (args.max_seconds or 120) > 120:
            parser.error("the thematic pilot allows at most 48 requests and 120 seconds")
    elif "tavily" in (args.seed_provider or ()):
        parser.error("Tavily requires --topic")
    inputs = (*args.catalog, args.known, args.gaps)
    if any(path and path.resolve() == args.state.resolve() for path in inputs):
        parser.error("--state must be separate from input files")
    seeds = [seed for path in args.catalog for seed in load_catalog(path)]
    if len({seed["url"] for seed in seeds}) != len(seeds):
        parser.error("catalogs contain the same seed URL")
    raw_known = read_json(args.known)
    if not isinstance(raw_known, list):
        parser.error("--known must be a JSON array of URLs")
    known = {channel_url(url) for url in raw_known}
    gaps = read_json(args.gaps) if args.gaps else {}
    if not isinstance(gaps, dict) or not all(type(w) is int and w >= 0 for w in gaps.values()):
        parser.error("--gaps must map cell keys to nonnegative integers")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock_path = args.state.with_name(args.state.name + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(2, "another cycle uses this state\n")
        state = (read_json(args.state) if args.state.exists() else
                 {"schema": 1, "cycle": 0, "cells": {}, "seeds": {}, "candidates": {}})
        if state.get("schema") != 1:
            parser.error("unsupported discovery state schema")
        budget = Budget({"max_requests": args.max_requests or (48 if args.topic else 24),
                         "max_seconds": args.max_seconds or (120 if args.topic else 75),
                         "max_bytes": 6_000_000})
        http = transport(budget, enabled=args.fetch)
        providers = []
        if args.discover:
            if args.topic:
                generated, providers = thematic_seeds(
                    args.discover, http, state["seeds"], tuple(args.seed_provider or ("kig",)),
                    args.topic, args.discover_limit,
                )
            else:
                generated, providers = populate(args.discover, http, channel_url, state["seeds"],
                                                limit=args.discover_limit,
                                                providers=tuple(args.seed_provider or ("kig", "wikidata")))
            present = {seed["url"] for seed in seeds}
            seeds.extend(seed for seed in generated if seed["url"] not in present)
        result = run_cycle(seeds, gaps, known, state, http,
                           max_seeds=args.max_seeds, max_new=args.max_new, per_seed=args.per_seed,
                           now=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if not result["stop"]:
            result["stop"] = next((row["reason"] for row in providers if row["reason"] in STOP), None)
        result.update(http_requests=budget.requests, http_bytes=budget.bytes,
                      seed_providers=providers, seed_count=len(seeds))
        save_json(args.state, state)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
