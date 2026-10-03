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
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"
PUBLIC_INDEX = PUBLIC_BASE + "/dr-audio/index.json"
SCOPES = ["https://www.googleapis.com/auth/firebase.hosting"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def valid_audio_path(value: object) -> str:
    path = str(value or "").strip()
    if path.startswith("/dr-audio/") and path.endswith(".m4a"):
        return path
    return ""


def remove_male_fields(entry: dict) -> None:
    for key in (
        "audioMaleUrl",
        "audioMalePath",
        "maleContentHash",
        "maleVoice",
        "maleBytes",
    ):
        entry.pop(key, None)


def catalog_for_change_detection(value: dict) -> dict:
    """Return catalog content without volatile run metadata."""
    stable = copy.deepcopy(value)
    stable.pop("generatedAt", None)
    return stable


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
        max_attempts = 5
        retry_codes = {429, 500, 502, 503, 504}
        delays = [10, 20, 40, 60, 90]

        for attempt in range(max_attempts):
            response = session.request(
                method,
                BASE + path,
                timeout=90,
                **kwargs,
            )

            if response.status_code in retry_codes:
                if attempt < max_attempts - 1:
                    wait = delays[attempt]
                    print(
                        f"Firebase API {response.status_code}. "
                        f"Retry {attempt + 1}/{max_attempts} in {wait}s...",
                        flush=True,
                    )
                    time.sleep(wait)
                    continue

            response.raise_for_status()
            return response.json()

        raise RuntimeError(
            f"Firebase API failed after {max_attempts} attempts: {method} {path}"
        )

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

    # Collect every voice asset referenced by the catalog.
    # Legacy audioPath is the female alias used by v2.3, while schema v3 adds
    # audioFemalePath and audioMalePath. Male files must be uploaded/preserved too.
    local_audio_files: dict[str, bytes] = {}
    stripped_missing_male = 0

    for entry in entries:
        female_paths: list[str] = []
        for key in ("audioFemalePath", "audioPath"):
            path = valid_audio_path(entry.get(key))
            if path and path not in female_paths:
                female_paths.append(path)

        for path in female_paths:
            candidate = audio_dir / Path(path).name
            if candidate.exists():
                local_audio_files[path] = candidate.read_bytes()
            else:
                require(
                    path in old_files,
                    f"Reused female Hosting audio missing: {path}",
                )

        male_path = valid_audio_path(entry.get("audioMalePath"))
        if male_path:
            candidate = audio_dir / Path(male_path).name
            if candidate.exists():
                local_audio_files[male_path] = candidate.read_bytes()
            elif male_path not in old_files:
                # Never publish a catalog entry that points to a 404.
                # Once removed, the next generator run sees that male voice as
                # missing and regenerates it according to the normal priority/quota.
                print(
                    f"Removing dangling male audio reference before deploy: {male_path}",
                    flush=True,
                )
                remove_male_fields(entry)
                stripped_missing_male += 1

    latest_count = min(int(index.get("latestCount") or 0), len(entries))
    index["latestMaleReady"] = sum(
        1
        for entry in entries[:latest_count]
        if valid_audio_path(entry.get("audioMalePath"))
    )

    if stripped_missing_male:
        print(
            f"Removed {stripped_missing_male} dangling male audio reference(s) from catalog.",
            flush=True,
        )

    # The index may have been sanitized above, so persist the exact catalog that
    # will be deployed before calculating its Hosting hash.
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    live_catalog_response = requests.get(
        PUBLIC_INDEX,
        params={"change_check": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )

    if live_catalog_response.status_code == 404:
        # Recovery mode: a previous full Hosting deploy may have removed the
        # DR Audio catalog. Rebuild only /dr-audio/ from files that are
        # actually available in this run while preserving every other live
        # Hosting path exactly as-is.
        print(
            "RECOVERY: live DR Audio catalog is missing (HTTP 404). "
            "Rebuilding DR Audio from verified local/cached assets.",
            flush=True,
        )
        live_catalog = {"entries": []}
        catalog_changed = True
    else:
        require(
            live_catalog_response.ok,
            f"Could not read live DR Audio catalog: "
            f"HTTP {live_catalog_response.status_code}",
        )
        try:
            live_catalog = live_catalog_response.json()
        except ValueError as error:
            raise RuntimeError(
                "Live DR Audio catalog is not valid JSON"
            ) from error

        catalog_changed = (
            catalog_for_change_detection(index)
            != catalog_for_change_detection(live_catalog)
        )

    local_files: dict[str, bytes] = {
        "/dr-audio/index.json": index_path.read_bytes(),
        **local_audio_files,
    }

    referenced_audio_paths: set[str] = set()
    for entry in entries:
        for key in ("audioPath", "audioFemalePath", "audioMalePath"):
            path = valid_audio_path(entry.get(key))
            if path:
                referenced_audio_paths.add(path)

    missing_referenced = [
        path
        for path in sorted(referenced_audio_paths)
        if path not in local_files and path not in old_files
    ]
    require(
        not missing_referenced,
        "Catalog still references missing Hosting audio: "
        + " | ".join(missing_referenced[:10]),
    )

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

    upsert_header(
        "/dr-audio/index.json",
        "Cache-Control",
        "no-cache, no-store, must-revalidate",
    )
    upsert_header(
        "/dr-audio/index.json",
        "Access-Control-Allow-Origin",
        "*",
    )
    upsert_header(
        "/dr-audio/**",
        "Cache-Control",
        "public, max-age=604800, immutable",
    )
    upsert_header(
        "/dr-audio/**",
        "Access-Control-Allow-Origin",
        "*",
    )

    # Avoid creating Firebase Hosting versions when DR Audio has no real changes.
    # generatedAt is intentionally ignored so the 10-minute scheduler cannot create
    # a new Hosting version merely because another check ran.
    config_changed = config != old.get("config", {})
    audio_or_site_changed = any(
        path != "/dr-audio/index.json" and old_files.get(path) != digest
        for path, digest in expected.items()
    )
    deploy_changed = catalog_changed or config_changed or audio_or_site_changed

    if not deploy_changed:
        print(
            "No real DR Audio Hosting changes detected. Skipping Firebase deploy.",
            flush=True,
        )
        return

    try:
        created = api("POST", SITE + "/versions", json={"config": config})
    except requests.HTTPError as error:
        response = getattr(error, "response", None)
        if response is not None and response.status_code == 429:
            raise RuntimeError(
                "Firebase Hosting create-version rate limit is still active. "
                "The workflow will fail intentionally so unpublished .m4a files "
                "are preserved and reused on the next run instead of regenerating them."
            ) from error
        raise

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
    require(
        not unknown,
        "Hosting requested an unavailable preserved hash; aborting",
    )

    for digest in sorted(required_hashes):
        upload = session.post(
            upload_url + "/" + digest,
            data=compressed_by_hash[digest],
            headers={"Content-Type": "application/octet-stream"},
            timeout=120,
        )
        upload.raise_for_status()

    api(
        "PATCH",
        new_version,
        params={"updateMask": "status"},
        json={"status": "FINALIZED"},
    )
    require(
        active() == (old_release, old_version),
        "Another Hosting deployment occurred; aborting",
    )

    release = api(
        "POST",
        SITE + "/releases",
        params={"versionName": new_version},
        json={"message": "Update shared DR Audio; preserve app update metadata and Radar"},
    )
    print("Hosting release:", release["name"], flush=True)

    catalog_verified = False
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
                    catalog_verified = True
                    break
            except ValueError:
                pass
        time.sleep(5)

    require(catalog_verified, "Shared DR Audio catalog did not verify after deployment")

    # Verify every audio file generated in this run, including male files.
    # This catches the exact failure mode where index.json points to an asset
    # that was never uploaded.
    new_audio_paths = sorted(
        path
        for path in local_audio_files
        if path.endswith(".m4a")
    )
    for path in new_audio_paths:
        url = PUBLIC_BASE + path
        verified = False
        for attempt in range(12):
            try:
                response = requests.head(
                    url,
                    headers={"Cache-Control": "no-cache"},
                    timeout=30,
                    allow_redirects=True,
                )
                if response.ok:
                    verified = True
                    break
            except requests.RequestException:
                pass
            time.sleep(5)
        require(verified, f"Published DR Audio asset is not reachable: {path}")

    if new_audio_paths:
        print(
            f"VERIFIED: {len(new_audio_paths)} generated audio asset(s) are reachable",
            flush=True,
        )


if __name__ == "__main__":
    main()
