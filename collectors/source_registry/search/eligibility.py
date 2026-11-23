"""Shared delivery gate for reviewed RSS feeds and pages."""
import re


def owner_accepted(reasons):
    return isinstance(reasons, (list, tuple)) and "owner_accept" in reasons


def can_poll(url, rules, verdict, reasons):
    if not url or not url.startswith(("https://", "http://")):
        return False
    version = re.fullmatch(r"a2sa-shadow-(\d+)\.(\d+)", rules or "")
    return owner_accepted(reasons) or (
        isinstance(verdict, dict) and bool(verdict) and version is not None and tuple(map(int, version.groups())) >= (0, 7)
    )
