#!/usr/bin/env python3
"""Deploy English DR Audio ONLY to a separate Firebase Hosting site.

No request in this module targets the Spanish Hosting site. Fail closed on
missing site, catalog conflicts, wrong audio paths or concurrent releases.
"""
from __future__ import annotations
import copy
import gzip
import hashlib
import json
import os
import time
from pathlib import Path

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from generate_dr_audio_english import (
    ENGLISH_SITE_ID, ENGLISH_INDEX, ENGLISH_PUBLIC_BASE,
)
from generate_shared_dr_audio import canonical_url

SPANISH_SITE_ID = "dr-accesorios-rd"
SITE_ID = ENGLISH_SITE_ID
SITE = "sites/" + SITE_ID
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
SCOPES = ["https://www.googleapis.com/auth/firebase.hosting"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_site_identity() -> None:
    require(bool(SITE_ID) and SITE_ID != SPANISH_SITE_ID,
            "English Hosting site must NEVER be the existing Spanish site")
    require(SITE_ID.startswith("dr-accesorios-rd-audio-en"),
            "Refusing to deploy English outside the dedicated Hosting site")
    require(ENGLISH_PUBLIC_BASE == "https://" + SITE_ID + ".web.app",
            "English URL/site mismatch")


def validate_english_catalog(current: dict, updated: dict) -> None:
    """Only one new entry, while all previous English records stay verbatim."""
    require(updated.get("language") == "en", "English language metadata required")
    old = current.get("entries", [])
    new = updated.get("entries", [])
    require(isinstance(old, list) and isinstance(new, list), "Invalid English entries")
    require(len(new) == len(old) + 1, "Expected exactly one new English article")
    require(new[1:] == old, "Historical English entries were changed or reordered")
    require(current.get("schemaVersion", 1) == updated.get("schemaVersion", 1),
            "Unexpected English schema change")
    existing_urls = {
        canonical_url(str(entry.get("url") or ""))
        for entry in old
    }
    article = new[0]
    require(canonical_url(str(article.get("url") or "")) not in existing_urls,
            "Duplicate English article")
    require(str(article.get("audioEnglishPath") or "").startswith("/dr-audio/")
            and str(article.get("audioEnglishPath") or "").endswith(".m4a"),
            "New English audio path is invalid")
    require(article.get("audioEnglishUrl") ==
            ENGLISH_PUBLIC_BASE + article["audioEnglishPath"],
            "New English URL must point to the separate English site")
    require(article.get("englishLanguage") == "en",
            "Only English audio allowed")
    require(
        all(not any(k.startswith(prefix) for k in article)
            for prefix in ("audioFemale", "audioMale")),
        "Do not put Spanish voice data into English catalog",
    )
    # Never silently mutate other top-level keys either.
    common = set(current) | set(updated)
    for key in common - {"entries"}:
        require(current.get(key) == updated.get(key),
                "Existing English catalog metadata changed: " + key)


def plan_assets(current_files: dict[str, str], audio_dir: Path, index: dict) -> dict[str, bytes]:
    """Verify sidecar payload; all old site assets retained by their hashes."""
    new_record = index["entries"][0]
    audio_path = new_record["audioEnglishPath"]
    expected_name = Path(audio_path).name
    candidate = audio_dir / expected_name
    require(candidate.is_file(), "English audio file not found locally")
    raw = candidate.read_bytes()
    require(len(raw) > 1024, "English audio file is suspiciously small")
    require(audio_path not in current_files, "Refusing to overwrite existing English audio")
    return {
        "/dr-audio/index.json": json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
        audio_path: raw,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("build_dir", nargs="?", default="dr_audio_english_build")
    args = parser.parse_args()
    validate_site_identity()
    audio_dir = Path(args.build_dir) / "dr-audio"
    manifest = audio_dir / "index.json"
    require(manifest.is_file(), "English index.json not prepared")
    index = json.loads(manifest.read_text(encoding="utf-8"))

    credentials_json = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(bool(credentials_json), "Firebase service credentials missing")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(credentials_json), scopes=SCOPES
    )
    session = AuthorizedSession(credentials)

    def api(method: str, path: str, **kwargs):
        for attempt in range(4):
            res = session.request(method, BASE + path, timeout=90, **kwargs)
            if res.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            res.raise_for_status()
            return res.json() if res.content else {}
        raise RuntimeError("Firebase API exhausted retries")

    # Confirm that this is a real dedicated Firebase Hosting site.
    site = api("GET", SITE)
    # Firebase may return either sites/<id> or projects/<project>/sites/<id>.
    actual_site_name = str(site.get("name") or "")
    require(
        actual_site_name == SITE or actual_site_name.endswith("/" + SITE),
        "Dedicated English Hosting site is not provisioned; no deploy",
    )

    def active():
        channel = api("GET", SITE + "/channels/live")
        release = channel.get("release")
        if not release:
            return "", ""
        version = release.get("version", {}).get("name", "")
        return str(release.get("name") or ""), str(version)

    def files(version: str) -> dict[str, str]:
        if not version:
            return {}
        items = {}
        page_token = ""
        while True:
            params = {"pageSize": 1000, "status": "ACTIVE"}
            if page_token:
                params["pageToken"] = page_token
            page = api("GET", version + "/files", params=params)
            for file in page.get("files", []):
                require(file["path"] not in items, "Duplicate Hosting path")
                items[file["path"]] = file["hash"]
            page_token = str(page.get("nextPageToken") or "")
            if not page_token:
                break
        return items

    prior_release, prior_version = active()
    old_files = files(prior_version)
    old_config = api("GET", prior_version).get("config", {}) if prior_version else {}
    response = requests.get(
        ENGLISH_INDEX, params={"verify": time.time_ns()},
        headers={"Cache-Control": "no-cache"}, timeout=30
    )
    if response.status_code == 404:
        current = {"schemaVersion": 1, "language": "en", "entries": []}
    else:
        response.raise_for_status()
        current = response.json()

    validate_english_catalog(current, index)
    new_files = plan_assets(old_files, audio_dir, index)
    # Preserve EVERY existing file in this site (not just indexed M4As).
    expected = dict(old_files)
    compressed = {}
    for path, raw in new_files.items():
        body = gzip.compress(raw, mtime=0)
        sha = hashlib.sha256(body).hexdigest()
        expected[path] = sha
        compressed[sha] = body

    config = copy.deepcopy(old_config)
    # Define headers only for this English site; never alter Spanish config.
    headers = config.setdefault("headers", [])
    headers.extend([
        {"glob": "/dr-audio/index.json", "headers": {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*"
        }},
        {"glob": "/dr-audio/**", "headers": {
            "Cache-Control": "public, max-age=604800, immutable",
            "Access-Control-Allow-Origin": "*"
        }}
    ])

    require(active() == (prior_release, prior_version),
            "English site changed concurrently; abort")
    version = api("POST", SITE + "/versions", json={"config": config})["name"]

    hashes_to_upload = set()
    upload_url = ""
    for offset in range(0, len(expected), 1000):
        batch = api("POST", version + ":populateFiles",
                    json={"files": dict(list(expected.items())[offset:offset+1000])})
        hashes_to_upload.update(batch.get("uploadRequiredHashes", []))
        upload_url = batch.get("uploadUrl") or upload_url
    require(bool(upload_url), "Missing Firebase upload URL")
    require(not (hashes_to_upload - set(compressed)),
            "Missing historical English assets in Firebase; refusing deploy")
    for sha in sorted(hashes_to_upload):
        response = session.post(
            upload_url + "/" + sha,
            data=compressed[sha],
            headers={"Content-Type": "application/octet-stream"},
            timeout=120,
        )
        response.raise_for_status()
    api("PATCH", version, params={"updateMask": "status"},
        json={"status": "FINALIZED"})
    require(active() == (prior_release, prior_version),
            "English Hosting changed before release; abort")
    api("POST", SITE + "/releases", params={"versionName": version},
        json={"message": "Publish English female narration to English-only site"})

    response = requests.get(
        ENGLISH_INDEX, params={"check": time.time_ns()},
        headers={"Cache-Control": "no-cache"}, timeout=30
    )
    require(response.ok and response.json() == index,
            "Published English catalog failed verification")
    audio_url = index["entries"][0]["audioEnglishUrl"]
    response = requests.head(audio_url, timeout=30, allow_redirects=True)
    require(response.ok, "Published English M4A is not reachable")
    print("English-only Hosting release verified; Spanish Hosting untouched.")

if __name__ == "__main__":
    main()
