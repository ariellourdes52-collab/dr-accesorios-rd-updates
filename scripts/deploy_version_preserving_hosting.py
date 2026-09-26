"""Update version.json while preserving every other live Hosting file."""
import argparse
import copy
import gzip
import hashlib
import json
import os
import re
import time

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
PUBLIC = "https://dr-accesorios-rd.web.app/version.json"


def version_tuple(value):
    if not re.fullmatch(r"v?\d+(?:\.\d+){1,2}", value):
        raise ValueError("Unrecognized version: " + value)
    parts = tuple(int(x) for x in value.removeprefix("v").split("."))
    return parts + (0,) * (3 - len(parts))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def preserved_manifest(original, digest):
    require("/version.json" in original, "Live version.json is missing")
    result = dict(original)
    result["/version.json"] = digest
    return result


def check_version(current, target):
    require(type(current.get("versionCode")) is int, "Invalid live versionCode")
    require(current["versionCode"] <= target["versionCode"], "Refusing versionCode rollback")
    require(version_tuple(current["versionName"]) <= version_tuple(target["versionName"]),
            "Refusing versionName rollback")
    if current["versionCode"] == target["versionCode"]:
        require(current["versionName"] == target["versionName"], "Version identity mismatch")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("payload")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    with open(args.payload, encoding="utf-8") as stream:
        target = json.load(stream)
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]),
        scopes=["https://www.googleapis.com/auth/firebase.hosting"])
    session = AuthorizedSession(credentials)

    def api(method, path, **kwargs):
        response = session.request(method, BASE + path, timeout=60, **kwargs)
        response.raise_for_status()
        return response.json()

    def active():
        release = api("GET", SITE + "/channels/live")["release"]
        version = SITE + "/versions/" + release["version"]["name"].rsplit("/", 1)[1]
        return release["name"], version

    def files(version):
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

    def live_json():
        response = requests.get(PUBLIC, params={"release_check": time.time_ns()},
                                headers={"Cache-Control": "no-cache"}, timeout=30)
        response.raise_for_status()
        return response

    def github(path, **params):
        response = requests.get("https://api.github.com/repos/" + os.environ["GITHUB_REPOSITORY"] + path,
                                headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"]},
                                params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def check_releases():
        page = 1
        while True:
            releases = github("/releases", per_page=100, page=page)
            for release in releases:
                if not release["draft"] and not release["prerelease"]:
                    require(version_tuple(release["tag_name"]) <= version_tuple(target["versionName"]),
                            "A newer stable GitHub release exists; refusing rollback")
            if len(releases) < 100:
                break
            page += 1

    current = live_json().json()
    check_version(current, target)
    check_releases()
    old_release, old_version = active()
    old = api("GET", old_version)
    old_files = files(old_version)
    require(bool(old_files) and len(old_files) == int(old["fileCount"]), "Incomplete Hosting inventory")
    require("/version.json" in old_files, "Live version.json is missing")
    print("Source Hosting version:", old_version, flush=True)
    print("Preserving paths:", json.dumps(sorted(p for p in old_files if p != "/version.json")), flush=True)
    if args.preflight:
        print("Preflight OK: Hosting readable, file inventory complete, no newer release", flush=True)
        return

    require(github("/releases/latest")["tag_name"] == target["versionName"], "Latest does not match target")
    config = copy.deepcopy(old.get("config", {}))
    headers = config.setdefault("headers", [])
    exact = next((h for h in reversed(headers) if h.get("glob") == "/version.json"), None)
    if exact is None:
        exact = {"glob": "/version.json", "headers": {}}
        headers.append(exact)
    exact.setdefault("headers", {})["Cache-Control"] = "no-cache, no-store, must-revalidate"
    compressed = gzip.compress((json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode(), mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()
    expected = preserved_manifest(old_files, digest)
    created = api("POST", SITE + "/versions", json={"config": config})
    new_version = SITE + "/versions/" + created["name"].rsplit("/", 1)[1]
    items = list(expected.items())
    for offset in range(0, len(items), 1000):
        batch = api("POST", new_version + ":populateFiles", json={"files": dict(items[offset:offset + 1000])})
        required = batch.get("uploadRequiredHashes", [])
        require(set(required) <= {digest}, "An existing Hosting file is unavailable; aborting")
        if digest in required:
            upload = session.post(batch["uploadUrl"] + "/" + digest, data=compressed,
                                  headers={"Content-Type": "application/octet-stream"}, timeout=60)
            upload.raise_for_status()
    require(files(new_version) == expected, "File inventory mismatch before finalization")
    api("PATCH", new_version, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    require(api("GET", new_version).get("config", {}) == config, "Hosting config mismatch")
    check_releases()
    require(github("/releases/latest")["tag_name"] == target["versionName"], "Latest changed during deployment")
    require(live_json().json() == current, "Live update metadata changed during deployment")
    require(active() == (old_release, old_version), "Another Hosting deployment occurred; aborting")
    release = api("POST", SITE + "/releases", params={"versionName": new_version},
                  json={"message": "Update app " + target["versionName"] + "; preserve Radar and all Hosting files"})
    print("Hosting release:", release["name"], flush=True)
    require(active()[1] == new_version, "Unexpected active Hosting version")
    require(files(new_version) == expected, "Released inventory mismatch")
    for attempt in range(18):
        response = live_json()
        if response.json() == target and "no-store" in response.headers.get("Cache-Control", ""):
            print("VERIFIED: target version.json live; all other file hashes preserved", flush=True)
            return
        time.sleep(5)
    raise RuntimeError("Public version.json verification did not complete; FCM will not run")


if __name__ == "__main__":
    main()
