#!/usr/bin/env python3
"""Deploy DR Audio assets to Firebase Hosting while preserving the live site."""
from __future__ import annotations

import argparse
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

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
PUBLIC_INDEX = "https://dr-accesorios-rd.web.app/dr-audio/index.json"
SCOPES = ["https://www.googleapis.com/auth/firebase.hosting"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_dir", nargs="?", default="dr_audio_build")
    args = parser.parse_args()

    build_dir = Path(args.build_dir)
    audio_dir = build_dir / "dr-audio"
    index_path = audio_dir / "index.json"
    require(index_path.exists(), "dr-audio/index.json is missing")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries = index.get("entries") or []
    require(entries, "Shared audio index has no entries")

    raw_sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(bool(raw_sa), "FIREBASE_SERVICE_ACCOUNT is missing")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(raw_sa), scopes=SCOPES
    )
    session = AuthorizedSession(credentials)

    def api(method: str, path: str, **kwargs):
        response = session.request(method, BASE + path, timeout=90, **kwargs)
        response.raise_for_status()
        return response.json()

    def active():
        release = api("GET", SITE + "/channels/live")["release"]
        version = SITE + "/versions/" + release["version"]["name"].rsplit("/", 1)[1]
        return release["name"], version

    def files(version: str):
        result, token = {}, None
        while True:
            params = {"pageSize": 1000, "status": "ACTIVE"}
            if token:
                params["pageToken"] = token
            page = api("GET", version + "/files", params=params)
            for item in page.get("files", []):
                require(item["path"] not in result, "Duplicate Hosting path")
                result[item["path"]] = item["hash"]
            token = page.get("nextPageToken")
            if not token:
                return result

    old_release, old_version = active()
    old = api("GET", old_version)
    old_files = files(old_version)
    require(bool(old_files), "Live Hosting file inventory is empty")

    local_files: dict[str, bytes] = {"/dr-audio/index.json": index_path.read_bytes()}

    for entry in entries:
        audio_path = str(entry.get("audioPath") or "")
        if not audio_path.startswith("/dr-audio/") or not audio_path.endswith(".m4a"):
            continue
        candidate = audio_dir / Path(audio_path).name
        if candidate.exists():
            local_files[audio_path] = candidate.read_bytes()
        else:
            require(audio_path in old_files, f"Reused Hosting audio missing: {audio_path}")

    referenced_audio_paths = {
        str(entry.get("audioPath") or "")
        for entry in entries
        if str(entry.get("audioPath") or "").startswith("/dr-audio/")
    }

    expected = {
        path: digest
        for path, digest in old_files.items()
        if not path.startswith("/dr-audio/")
    }
    for path in referenced_audio_paths:
        if path in old_files and path not in local_files:
            expected[path] = old_files[path]

    compressed_by_hash: dict[str, bytes] = {}
    for path, raw in local_files.items():
        compressed = gzip.compress(raw, mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        expected[path] = digest
        compressed_by_hash[digest] = compressed

    config = copy.deepcopy(old.get("config", {}))
    headers = config.setdefault("headers", [])

    def upsert_header(glob: str, name: str, value: str) -> None:
        item = next((h for h in reversed(headers) if h.get("glob") == glob), None)
        if item is None:
            item = {"glob": glob, "headers": {}}
            headers.append(item)
        item.setdefault("headers", {})[name] = value

    upsert_header("/dr-audio/index.json", "Cache-Control", "no-cache, no-store, must-revalidate")
    upsert_header("/dr-audio/**", "Cache-Control", "public, max-age=604800, immutable")

    created = api("POST", SITE + "/versions", json={"config": config})
    new_version = SITE + "/versions/" + created["name"].rsplit("/", 1)[1]

    required_hashes: set[str] = set()
    items = list(expected.items())
    upload_url = None
    for offset in range(0, len(items), 1000):
        batch = api(
            "POST",
            new_version + ":populateFiles",
            json={"files": dict(items[offset : offset + 1000])},
        )
        required_hashes.update(batch.get("uploadRequiredHashes", []))
        upload_url = batch.get("uploadUrl") or upload_url

    require(upload_url, "Hosting did not return an upload URL")
    unknown = required_hashes - set(compressed_by_hash)
    require(not unknown, "Hosting requested an unavailable preserved hash; aborting")

    for digest in sorted(required_hashes):
        upload = session.post(
            upload_url + "/" + digest,
            data=compressed_by_hash[digest],
            headers={"Content-Type": "application/octet-stream"},
            timeout=120,
        )
        upload.raise_for_status()

    api("PATCH", new_version, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    require(active() == (old_release, old_version), "Another Hosting deployment occurred; aborting")

    release = api(
        "POST",
        SITE + "/releases",
        params={"versionName": new_version},
        json={"message": "Update shared DR Audio; preserve app update metadata and Radar"},
    )
    print("Hosting release:", release["name"], flush=True)

    for attempt in range(18):
        response = requests.get(
            PUBLIC_INDEX,
            params={"verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        if response.ok:
            try:
                if response.json() == index:
                    print("VERIFIED: shared DR Audio catalog is live", flush=True)
                    return
            except ValueError:
                pass
        time.sleep(5)

    raise RuntimeError("Shared DR Audio catalog did not verify after deployment")


if __name__ == "__main__":
    main()
