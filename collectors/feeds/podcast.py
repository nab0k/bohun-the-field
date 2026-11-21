"""A5TC-04 podcast episodes (D-20261120-PODCASTS-WHISPER): text for episodes of the accepted feeds.

1. The publisher's own transcript (<podcast:transcript>, Podcasting 2.0): VTT, SRT, HTML, JSON or plain text,
   fetched like any page (robots, pauses, size limit).
2. Otherwise speech recognition of the audio the publisher offers in the feed (<enclosure>), by Groq Whisper
   (whisper-large-v3-turbo, free tier ~8 h of audio a day). Groq fetches the audio from the publisher's URL itself
   (the `url` parameter): the audio is never downloaded or stored here. Only fresh episodes, within a per-run budget.
The text goes to raw_data.content_capture with the same contract as article text; raw_data.transcript says where it
came from. Internal processing only.
"""
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

PODCAST_NS = "https://podcastindex.org/namespace/1.0"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = os.environ.get("PODCAST_WHISPER_MODEL", "whisper-large-v3-turbo")
RUN_AUDIO_SECONDS = int(os.environ.get("PODCAST_AUDIO_SECONDS_PER_RUN", "2400"))  # 12 runs x 2400 s = 8 h a day
MAX_EPISODE_SECONDS = 5400
UA = "BOHUN-FeedCollector/1.0 (+https://github.com/nab0k/bohun-the-field)"


def duration_seconds(text):
    """itunes:duration as seconds: '3725', '1:02:05', '62:05'."""
    if not text:
        return None
    parts = [p for p in text.strip().split(":") if p.strip().isdigit()]
    if not parts or len(parts) != len(text.strip().split(":")):
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs


def episodes(body):
    """Feed XML -> {link or guid: {"audio", "duration", "transcripts": [(url, type)]}} for items with audio."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return {}
    out = {}
    for item in root.iter("item"):
        enc = item.find("enclosure")
        audio = enc.get("url") if enc is not None and (enc.get("type") or "").startswith("audio") else None
        if not audio:
            continue
        info = {"audio": audio, "duration": duration_seconds(item.findtext(f"{{{ITUNES_NS}}}duration")),
                "transcripts": [(t.get("url"), (t.get("type") or "").lower())
                                for t in item.findall(f"{{{PODCAST_NS}}}transcript") if t.get("url")]}
        for key in (item.findtext("link"), item.findtext("guid")):
            if key:
                out[key.strip()] = info
    return out


TIME_LINE = re.compile(r"^\s*(\d+\s*)?$|-->|^WEBVTT|^NOTE\b", re.I)


def transcript_to_text(raw, kind):
    """VTT/SRT: cue text without numbers and times; HTML: visible text; JSON (podcastindex): segments' body."""
    text = raw.decode("utf-8", "replace")
    if "json" in kind:
        try:
            data = json.loads(text)
        except ValueError:
            return ""
        segs = data.get("segments", []) if isinstance(data, dict) else []
        return " ".join((s.get("body") or "").strip() for s in segs if isinstance(s, dict)).strip()
    if "vtt" in kind or "subrip" in kind or "srt" in kind:
        lines = [l for l in text.splitlines() if not TIME_LINE.search(l)]
        return re.sub(r"<[^>]+>", "", " ".join(l.strip() for l in lines if l.strip()))
    if "html" in kind:
        from collectors.inoreader.content_contract import visible_text
        return visible_text(text)
    return text.strip()


def whisper(audio_url, key, opener=urllib.request.urlopen, timeout=300):
    """Groq transcribes the publisher's audio from its URL (no download here). Returns plain text."""
    boundary = uuid.uuid4().hex
    fields = {"model": WHISPER_MODEL, "url": audio_url, "response_format": "text"}
    body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                   for k, v in fields.items()) + f"--{boundary}--\r\n"
    req = urllib.request.Request(WHISPER_URL, data=body.encode(), method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": UA})
    with opener(req, timeout=timeout) as r:
        return r.read(5_000_000).decode("utf-8", "replace").strip()


MAX_TEXT_BYTES = 120 * 1024  # below the contract's 128 KiB text limit, so a long episode is cut, not dropped


def clip(text, limit=MAX_TEXT_BYTES):
    """Cut text to `limit` UTF-8 bytes at a word boundary."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    cut = raw[:limit].decode("utf-8", "ignore")
    return cut[:cut.rfind(" ")] if " " in cut else cut


def as_capture(text, now):
    """Plain text -> the article content contract (raw_data.content_capture)."""
    from collectors.inoreader.content_contract import extract_inoreader_content
    return extract_inoreader_content({"summary": {"content": "<p>" + html.escape(clip(text)) + "</p>"}},
                                     captured_at=now)


class Transcriber:
    """Per run: publisher transcript first, then Whisper within the audio budget. Counts only, for the public log."""

    def __init__(self, fetcher, key=None, budget=RUN_AUDIO_SECONDS, whisper_fn=whisper):
        self.fetcher, self.key, self.budget, self.whisper = fetcher, key, budget, whisper_fn
        self.counts = {"episodes": 0, "publisher": 0, "whisper": 0, "skipped_budget": 0, "failed": 0}

    def text(self, info):
        self.counts["episodes"] += 1
        for url, kind in info["transcripts"]:
            try:
                if not self.fetcher.allowed(url):
                    continue
                status, _, raw = self.fetcher._get(url)
                if status == 200 and raw:
                    text = transcript_to_text(raw, kind)
                    if len(text) >= 200:
                        self.counts["publisher"] += 1
                        return text, {"source": "publisher", "url": url, "type": kind}
            except Exception:  # noqa: BLE001 - a broken transcript link falls back to recognition
                pass
        seconds = info.get("duration") or 3600
        if not self.key or seconds > MAX_EPISODE_SECONDS or seconds > self.budget:
            self.counts["skipped_budget"] += 1
            return None, None
        try:
            text = self.whisper(info["audio"], self.key)
        except (urllib.error.URLError, OSError, ValueError):
            self.counts["failed"] += 1
            return None, None
        self.budget -= seconds
        if len(text) < 200:
            self.counts["failed"] += 1
            return None, None
        self.counts["whisper"] += 1
        return text, {"source": "whisper", "model": WHISPER_MODEL, "audio_url": info["audio"], "seconds": seconds}
