"""Bounded HTTP client: robots, per-domain delay, validators and safe logs."""

import threading
import time
import urllib.robotparser
from collections import defaultdict
from urllib.parse import urlsplit

import requests


class PoliteSession:
    def __init__(self, *, delay=2.0, timeout=15, user_agent="BOHUN-Source-Harvest/1.0 (+https://bohundefence.com/contact)"):
        self.delay = delay
        self.timeout = timeout
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8"})
        self._robots = {}
        self._last = defaultdict(float)
        self._locks = defaultdict(threading.Lock)
        self.request_count = 0

    def _robot(self, url):
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser(origin + "/robots.txt")
            try:
                self._throttled_get(origin + "/robots.txt", raw=True)
                response = self.session.get(origin + "/robots.txt", timeout=self.timeout)
                self.request_count += 1
                rp.parse(response.text.splitlines() if response.ok else [])
                self._robots[origin] = rp if response.ok else None
            except requests.RequestException:
                self._robots[origin] = None
        return self._robots[origin]

    def allowed(self, url):
        robot = self._robot(url)
        return robot is None or robot.can_fetch(self.user_agent, url)

    def _throttled_get(self, url, raw=False):
        host = (urlsplit(url).hostname or "").lower()
        with self._locks[host]:
            wait = self.delay - (time.monotonic() - self._last[host])
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def get(self, url, *, etag="", last_modified="", check_robots=True):
        if check_robots and not self.allowed(url):
            raise PermissionError(f"robots_disallowed:{urlsplit(url).hostname}")
        self._throttled_get(url)
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        response = self.session.get(url, headers=headers, timeout=self.timeout, allow_redirects=True)
        self.request_count += 1
        return response

