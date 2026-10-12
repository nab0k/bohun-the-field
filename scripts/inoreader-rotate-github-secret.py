#!/usr/bin/env python3
"""One-shot OAuth refresh and encrypted GitHub Actions secret rotation.

Requires an explicitly provisioned fine-grained PAT with Actions secrets write
permission (INOREADER_SECRET_ADMIN_TOKEN). Never print OAuth credentials.
This script is deliberately NOT called by the normal ingestion workflows.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from nacl.public import PublicKey, SealedBox

REPO = "nab0k/bohun-the-field"
API = "https://api.github.com"
OAUTH = "https://www.inoreader.com/oauth2/token"


def request(url, *, token=None, data=None, content_type=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Bohun-OAuth-Rotation/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=25) as response:
        payload = response.read()
    return json.loads(payload) if payload else {}


def rotate():
    admin = os.environ["INOREADER_SECRET_ADMIN_TOKEN"]
    client_id = os.environ["INOREADER_CLIENT_ID"]
    client_secret = os.environ["INOREADER_CLIENT_SECRET"]
    old_refresh = os.environ["INOREADER_REFRESH_TOKEN"]
    if not all((admin, client_id, client_secret, old_refresh)):
        raise RuntimeError("MISSING_OAUTH_OR_ADMIN_CREDENTIALS")

    # Check the exact target and permissions BEFORE rotating the provider token.
    secret_url = f"{API}/repos/{REPO}/actions/secrets/INOREADER_REFRESH_TOKEN"
    key_url = f"{API}/repos/{REPO}/actions/secrets/public-key"
    key = request(key_url, token=admin)
    if not key.get("key") or not key.get("key_id"):
        raise RuntimeError("GITHUB_SECRET_PUBLIC_KEY_UNAVAILABLE")

    # GitHub's encrypted secret update uses PUT, not POST.
    form = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": old_refresh,
        "grant_type": "refresh_token",
    }).encode()
    refreshed = request(OAUTH, data=form, content_type="application/x-www-form-urlencoded")
    if not refreshed.get("access_token"):
        raise RuntimeError("OAUTH_REFRESH_FAILED")
    new_refresh = refreshed.get("refresh_token")
    if not new_refresh or new_refresh == old_refresh:
        print("OAuth access verified; provider did not rotate refresh token.")
        return

    encrypted = SealedBox(PublicKey(base64.b64decode(key["key"]))).encrypt(new_refresh.encode())
    body = json.dumps({"encrypted_value": base64.b64encode(encrypted).decode(), "key_id": key["key_id"]}).encode()
    req = urllib.request.Request(secret_url, data=body, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + admin,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "Bohun-OAuth-Rotation/1.0",
    }, method="PUT")
    with urllib.request.urlopen(req, timeout=25) as response:
        if response.status not in (201, 204):
            raise RuntimeError("GITHUB_SECRET_UPDATE_FAILED")
    print("OAuth verified; rotated refresh token securely updated in GitHub Actions secret.")


if __name__ == "__main__":
    try:
        rotate()
    except (KeyError, RuntimeError, urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
        # Do not print exception bodies: some services echo sensitive request details.
        print("OAuth rotation failed; check admin permissions, existing token and GitHub secret configuration.", file=sys.stderr)
        sys.exit(1)
