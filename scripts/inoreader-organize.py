#!/usr/bin/env python3
"""Preview BOHUN folder assignments for existing Inoreader subscriptions."""

import json
import urllib.parse
import urllib.request
from pathlib import Path

ENV_PATH = Path(".env.local")

env = {}
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

required = [
    "INOREADER_APP_ID",
    "INOREADER_APP_KEY",
    "INOREADER_REFRESH_TOKEN",
]

missing = [key for key in required if not env.get(key)]
if missing:
    raise SystemExit("Missing variables: " + ", ".join(missing))

data = urllib.parse.urlencode({
    "client_id": env["INOREADER_APP_ID"],
    "client_secret": env["INOREADER_APP_KEY"],
    "refresh_token": env["INOREADER_REFRESH_TOKEN"],
    "grant_type": "refresh_token",
}).encode()

request = urllib.request.Request(
    "https://www.inoreader.com/oauth2/token",
    data=data,
    method="POST",
)

with urllib.request.urlopen(request, timeout=30) as response:
    token_data = json.load(response)

access_token = token_data["access_token"]

request = urllib.request.Request(
    "https://www.inoreader.com/reader/api/0/subscription/list",
    headers={"Authorization": "Bearer " + access_token},
)

with urllib.request.urlopen(request, timeout=30) as response:
    subscriptions = json.load(response).get("subscriptions", [])

MEDIA = {
    "Air & Space Forces Magazine",
    "Der Monitor für Defence und Sicherheitspolitik",
    "Indian Defence Review",
    "UK Defence Journal",
    "Zone Militaire",
}

INDUSTRY = {
    "Anduril Blog",
    "Kratos Defense",
    "Quantum Systems",
    "TEKEVER",
}

GOVERNMENT = {
    "Ministry of Defence - Activity on GOV.UK",
    "Laman Utama",
}

PROCUREMENT = {
    "Defence and Security Accelerator - Activity on GOV.UK",
}

INVESTORS = {
    "Crunchbase News",
}

STARTUPS = {
    "AFWERX",
}

def target_folder(sub):
    title = sub.get("title", "")
    url = sub.get("url", "")

    if "defensenews.com/arc/outboundfeeds/rss/category/" in url:
        return "BOHUN_01_DEFENCE_MEDIA"

    if title in MEDIA:
        return "BOHUN_01_DEFENCE_MEDIA"

    if title in INDUSTRY:
        return "BOHUN_02_INDUSTRY"

    if title in GOVERNMENT:
        return "BOHUN_03_GOVERNMENT"

    if title in PROCUREMENT:
        return "BOHUN_04_PROCUREMENT"

    if title in INVESTORS:
        return "BOHUN_08_INVESTORS_VC"

    if title in STARTUPS:
        return "BOHUN_09_STARTUPS"

    return None


changes = []
unclassified = []

for sub in subscriptions:
    categories = [
        category.get("label", "")
        for category in sub.get("categories", [])
    ]

    if any(category.startswith("BOHUN_") for category in categories):
        continue

    folder = target_folder(sub)

    if folder:
        changes.append({
            "title": sub.get("title", ""),
            "feed_id": sub.get("id", ""),
            "target_folder": folder,
            "existing_folders": categories,
        })
    else:
        unclassified.append(sub.get("title", ""))

print(f"\nTOTAL SUBSCRIPTIONS: {len(subscriptions)}")
print(f"PROPOSED CHANGES: {len(changes)}")
print(f"UNCLASSIFIED: {len(unclassified)}\n")

for item in sorted(changes, key=lambda x: x["title"]):
    print(f"{item['title']}")
    print(f"  ADD: {item['target_folder']}")
    print(f"  KEEP: {', '.join(item['existing_folders']) or '(none)'}")

if unclassified:
    print("\nUNCLASSIFIED:")
    for title in unclassified:
        print(" ", title)

Path("inoreader-folder-plan.json").write_text(
    json.dumps(changes, ensure_ascii=False, indent=2)
)

print("\nDRY RUN COMPLETE: no changes made.")
