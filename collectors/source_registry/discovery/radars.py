"""Bounded pinned HTTPS and adapters for explicit radar inputs. No import I/O."""
from dataclasses import dataclass
import csv
import datetime as dt
from email.utils import parsedate_to_datetime
import http.client
import io
import hashlib
import ipaddress
import json
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import uuid
import xml.etree.ElementTree as ET

from .policy import Problem, digest, epoch, identity_url, parse_json, read_file, require

UA = "BOHUN-Source-Discovery/1.0"
API = "https://www.inoreader.com/reader/api/0/"


@dataclass
class Response:
    status: int
    body: bytes
    url: str
    headers: dict


class Budget:
    def __init__(self, limits, *, monotonic=time.monotonic):
        self.limits, self.clock = limits, monotonic
        self.deadline = self.clock() + limits["max_seconds"]
        self.requests = self.bytes = 0
        self.trace = []

    def remaining(self):
        left = self.deadline - self.clock()
        require(left > 0, "CYCLE_TIME_CAP")
        return left

    def request(self):
        self.remaining()
        require(self.requests < self.limits["max_requests"], "HTTP_REQUEST_CAP")
        self.requests += 1

    def add_bytes(self, n):
        self.bytes += n
        require(self.bytes <= self.limits["max_bytes"], "HTTP_BYTE_CAP")
        self.remaining()


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, family, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address, self.family = address, family

    def connect(self):
        # Connect directly to an already checked address; TLS verifies the original hostname.
        raw = socket.socket(self.family, socket.SOCK_STREAM)
        self.sock = raw
        deadline = time.monotonic() + self.timeout
        try:
            raw.settimeout(self.timeout)
            raw.connect((self.address, 443))
            raw.settimeout(max(0.001, deadline - time.monotonic()))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            self.sock.do_handshake()
        except BaseException:
            raw.close()
            raise


def resolve_public(host, timeout):
    box = []
    def work():
        try:
            box.append(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            box.append(None)
    # A stuck OS resolver must not hold up the cycle. On timeout the client stops,
    # so it cannot accumulate additional resolver threads in this worker.
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout)
    require(not thread.is_alive(), "DNS_TIMEOUT")
    require(box and box[0], "DNS_UNAVAILABLE")
    records = box[0]
    require(all(ipaddress.ip_address(r[4][0]).is_global for r in records), "DNS_PRIVATE")
    first = records[0]
    return first[4][0], first[0]


class HTTP:
    def __init__(self, budget, *, enabled=False, resolver=resolve_public,
                 connection_factory=PinnedConnection, sleep=time.sleep,
                 reserve_api=None):
        self.budget, self.enabled = budget, enabled
        self.resolver, self.connection_factory = resolver, connection_factory
        self.sleep, self.reserve_api = sleep, reserve_api
        self.last_host = {}
        self.robots = {}
        self.dns_failed = False

    def _one(self, url, method, headers, body, limit, api):
        require(self.enabled, "NETWORK_DISABLED")
        require(not self.dns_failed, "DNS_WORKER_HOLD")
        self.budget.request()
        if api:
            require(self.reserve_api is not None, "API_LEDGER_REQUIRED")
            self.reserve_api(uuid.uuid4().hex)
        host = urlsplit(url).hostname
        wait = 2 - (self.budget.clock() - self.last_host.get(host, -1e9))
        if wait > 0:
            require(wait < self.budget.remaining(), "CYCLE_TIME_CAP")
            self.sleep(wait)
        started = self.budget.clock()
        try:
            address, family = self.resolver(host, min(12, self.budget.remaining()))
        except Problem as exc:
            if str(exc) == "DNS_TIMEOUT":
                self.dns_failed = True
            raise
        require(ipaddress.ip_address(address).is_global, "DNS_PRIVATE")
        timeout = min(12 - (self.budget.clock() - started), self.budget.remaining())
        require(timeout > 0, "REQUEST_TIMEOUT")
        conn = self.connection_factory(host, address, family, timeout)
        def abort():
            try:
                if conn.sock is not None and hasattr(conn.sock, "shutdown"):
                    conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                conn.close()
        cutoff = threading.Timer(timeout, abort)
        cutoff.daemon = True
        cutoff.start()
        data = bytearray()
        try:
            request_target = urlsplit(url).path or "/"
            if urlsplit(url).query:
                request_target += "?" + urlsplit(url).query
            conn.request(method, request_target, body=body,
                         headers={"User-Agent": UA, "Accept-Encoding": "identity", **headers})
            if conn.sock is not None:
                conn.sock.settimeout(min(12 - (self.budget.clock() - started), self.budget.remaining()))
            response = conn.getresponse()
            metadata = {k.lower(): v for k, v in response.getheaders()}
            require(metadata.get("content-encoding", "identity").lower() in ("", "identity"), "ENCODING_UNSUPPORTED")
            length = metadata.get("content-length")
            require(length is None or (length.isdigit() and int(length) <= limit), "RESPONSE_SIZE")
            while True:
                timeout = min(12 - (self.budget.clock() - started), self.budget.remaining())
                require(timeout > 0, "REQUEST_TIMEOUT")
                if conn.sock is not None:
                    conn.sock.settimeout(timeout)
                block = response.read1(min(16384, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                self.budget.add_bytes(len(block))
                require(len(data) <= limit, "RESPONSE_SIZE")
            self.last_host[host] = self.budget.clock()
            self.budget.trace.append({"url_hash": digest(url), "method": method,
                                      "status": response.status, "bytes": len(data)})
            return Response(response.status, bytes(data), url, metadata)
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
            if isinstance(exc, Problem):
                raise
            raise Problem("TRANSPORT_FAILURE") from None
        finally:
            cutoff.cancel()
            self.last_host[host] = self.budget.clock()
            conn.close()

    def allowed(self, url):
        parts = urlsplit(url)
        root = "https://" + parts.netloc
        if root not in self.robots:
            response = self.request(root + "/robots.txt", limit=128 * 1024, robots=False)
            if response.status in (404, 410):
                self.robots[root] = True
            elif response.status == 200:
                require("html" not in response.headers.get("content-type", "").lower() and
                        b"<html" not in response.body[:1024].lower(), "ROBOTS_UNCERTAIN")
                parser = RobotFileParser()
                parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
                self.robots[root] = parser
            else:
                raise Problem("ROBOTS_UNAVAILABLE")
        rules = self.robots[root]
        require(rules is True or rules.can_fetch(UA, url), "ROBOTS_DISALLOWED")

    def request(self, url, *, method="GET", headers=None, body=None, limit=1_000_000,
                robots=False, api=False, opaque=False):
        require(method in ("GET", "POST") and (body is None or isinstance(body, bytes)), "HTTP_METHOD")
        current = identity_url(url, opaque=opaque)
        for hop in range(4):
            if robots:
                self.allowed(current)
            result = self._one(current, method, headers or {}, body, limit, api)
            if result.status not in (301, 302, 303, 307, 308):
                return result
            require(method == "GET" and hop < 3, "REDIRECT_HOLD")
            target = identity_url(urljoin(current, result.headers.get("location", "")), opaque=opaque)
            # Never forward OAuth or any custom headers to another origin.
            require(not headers or urlsplit(target).netloc == urlsplit(current).netloc, "REDIRECT_AUTH_HOLD")
            current = target
        raise Problem("REDIRECT_HOLD")


def xml_root(raw):
    require(isinstance(raw, bytes) and len(raw) <= 1_000_000, "XML_SIZE")
    require(b"\x00" not in raw and b"<!doctype" not in raw.lower() and b"<!entity" not in raw.lower(), "XML_DTD")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        raise Problem("XML_INVALID") from None
    stack, count = [(root, 0)], 0
    while stack:
        element, depth = stack.pop()
        count += 1
        require(depth <= 64 and count <= 10000, "XML_COMPLEXITY")
        stack.extend((child, depth + 1) for child in element)
    return root


def localtag(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node, *names):
    return next(((c.text or "").strip() for c in node if localtag(c.tag) in names), "")


def parsed_date(raw):
    try:
        return epoch(raw)
    except Problem:
        try:
            d = parsedate_to_datetime(raw)
            return d.timestamp() if d.tzinfo else None
        except (ValueError, TypeError, OverflowError):
            return None


def feed_data(raw, url):
    root = xml_root(raw)
    kind = localtag(root.tag)
    require(kind in ("rss", "rdf", "feed"), "NOT_FEED")
    channel = next((c for c in root if localtag(c.tag) == "channel"), root)
    items = [n for n in root.iter() if localtag(n.tag) in ("item", "entry")][:1000]
    entries = []
    for node in items:
        title = child_text(node, "title")[:2000]
        link = child_text(node, "link")
        if not link:
            link = next((c.attrib.get("href", "") for c in node if localtag(c.tag) == "link"
                         and c.attrib.get("rel", "alternate") == "alternate"), "")
        published = child_text(node, "pubdate", "published", "updated", "date")
        source = next((c.attrib.get("url", "") for c in node if localtag(c.tag) == "source"), "")
        if title and link:
            entries.append({"title": title, "url": urljoin(url, link),
                            "published_epoch": parsed_date(published), "source_url": source,
                            "item_id": digest(child_text(node, "id", "guid") or link)})
    lang = child_text(channel, "language") or root.attrib.get("{http://www.w3.org/XML/1998/namespace}lang", "")
    return {"title": child_text(channel, "title")[:1000], "language": lang,
            "format": "atom" if kind == "feed" else "rss", "entries": entries}


def aggregator(host):
    return bool(host and any(host == base or host.endswith("." + base)
                            for base in ("google.com", "talkwalker.com", "inoreader.com")))


def publisher_observation(item, provider):
    result = {"item_id": digest(str(item.get("item_id", item.get("id", "")))),
              "title": str(item.get("title", ""))[:2000], "method": "article-origin"}
    try:
        raw = item.get("url", "")
        alternate = item.get("alternate", [])
        if not raw and isinstance(alternate, list) and alternate:
            raw = alternate[0].get("href", "")
        parts = urlsplit(raw)
        if parts.hostname in ("google.com", "www.google.com") and parts.path == "/url":
            params = parse_qs(parts.query)
            targets = params.get("url") or params.get("q") or []
            require(len(targets) == 1, "WRAPPER_UNKNOWN")
            raw = targets[0]
            result["method"] = "google-url-wrapper"
        article = identity_url(raw)
        host = urlsplit(article).hostname
        require(not aggregator(host), "AGGREGATOR_NOT_PUBLISHER")
        result["article_url"] = article
        result["publisher_url"] = "https://" + urlsplit(article).netloc + "/"
        origin = item.get("origin") or {}
        stream = origin.get("streamId", "") if isinstance(origin, dict) else ""
        feed = item.get("feed_url") or (stream[5:] if stream.startswith("feed/") else None)
        if feed:
            feed = identity_url(feed)
            if not aggregator(urlsplit(feed).hostname):
                result["feed_url"] = feed
                result["method"] = "inoreader-origin" if provider == "inoreader" else "tool-feed-hint"
    except (Problem, ValueError, AttributeError, TypeError):
        return {"item_id": result["item_id"], "title": result["title"],
                "method": "unresolved", "error": "PUBLISHER_REVIEW"}
    return result


def api_json(http, path, token, *, params=None, form=None):
    require(isinstance(token, str) and token and len(token) < 8192, "TOKEN_REQUIRED")
    url = API + path
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if params:
        url += "?" + urlencode(params)
    raw = urlencode(form).encode() if form is not None else None
    if raw is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    response = http.request(url, method="POST" if raw is not None else "GET", headers=headers,
                            body=raw, api=True)
    require(response.status == 200, "INOREADER_HTTP_" + str(response.status))
    return response


def radar_pages(config, cursor, http, token_provider, limits):
    kind = config["type"]
    if kind == "inoreader":
        continuation = cursor.get("continuation")
        seen_ids = set(cursor.get("seen_ids", []))
        total = 0
        for _ in range(limits["max_pages"]):
            params = {"n": min(50, limits["max_hits"] - total)}
            if continuation:
                params["c"] = continuation
            try:
                response = api_json(http, "stream/contents/" + quote(config["stream"], safe="/"),
                                    token_provider(), params=params)
            except Problem as exc:
                if str(exc) == "INOREADER_HTTP_400" and continuation:
                    yield [], {"continuation": None, "seen_ids": sorted(seen_ids)[-1000:],
                               "coverage": "RESYNC_REQUIRED"}
                    break
                raise
            data = parse_json(response.body)
            require(isinstance(data, dict) and isinstance(data.get("items"), list), "STREAM_CONTRACT")
            require(len(data["items"]) <= params["n"], "STREAM_PAGE_SIZE")
            observations = []
            for item in data["items"]:
                require(isinstance(item, dict) and item.get("id"), "STREAM_ITEM")
                obs = publisher_observation(item, kind)
                if obs["item_id"] not in seen_ids:
                    observations.append(obs)
                seen_ids.add(obs["item_id"])
            new = data.get("continuation")
            require(new is None or (isinstance(new, str) and 0 < len(new) <= 4096 and new != continuation),
                    "STREAM_CURSOR_STUCK")
            total += len(data["items"])
            state = {"continuation": new, "seen_ids": sorted(seen_ids)[-1000:],
                     "coverage": "BACKLOG" if new else "WINDOW_ONLY"}
            yield observations, state
            continuation = new
            if not new or total >= limits["max_hits"]:
                break
    elif kind in ("google_alerts", "talkwalker"):
        response = http.request(config["url"], opaque=True)
        require(response.status == 200, "RADAR_HTTP_" + str(response.status))
        parsed = feed_data(response.body, response.url)
        entries = parsed["entries"][:limits["max_hits"]]
        seen = set(cursor.get("seen_ids", []))
        observations = [publisher_observation(e, kind) for e in entries if e["item_id"] not in seen]
        yield observations, {"seen_ids": [e["item_id"] for e in entries], "coverage": "ROLLING_WINDOW_UNKNOWN"}
    else:
        raise Problem("RADAR_TYPE")


def artifact_page(config, cursor, limit):
    raw = read_file(config["path"])
    file_hash = hashlib.sha256(raw).hexdigest()
    if config.get("format") == "json":
        rows = parse_json(raw)
        if isinstance(rows, dict):
            rows = rows.get("feeds", rows.get("sources"))
    else:
        try:
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        except UnicodeError:
            raise Problem("ARTIFACT_UTF8") from None
    require(isinstance(rows, list) and len(rows) <= 10000, "ARTIFACT_ROWS")
    offset = cursor.get("offset", 0) if cursor.get("file_hash") == file_hash else 0
    require(type(offset) is int and 0 <= offset <= len(rows), "ARTIFACT_CURSOR")
    observations = []
    for index, row in enumerate(rows[offset:offset + limit], offset):
        require(isinstance(row, dict), "ARTIFACT_ROW")
        site = row.get("site") or row.get("website") or row.get("url") or row.get("feed_url") or ""
        feed = row.get("feed_url")
        obs = publisher_observation({"item_id": digest([file_hash, index]), "url": site,
                                     "feed_url": feed, "title": row.get("name_hint", row.get("title", ""))}, "tool")
        source_page = row.get("source_page") or row.get("directory_page") or row.get("directory_url")
        if source_page:
            try:
                obs["source_page"] = identity_url(source_page)
            except Problem:
                obs["error"] = "SOURCE_PAGE_REVIEW"
        observations.append(obs)
    return observations, {"file_hash": file_hash, "offset": min(len(rows), offset + limit),
                           "coverage": "BACKLOG" if offset + limit < len(rows) else "IMPORTED"}
