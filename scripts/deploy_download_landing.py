"""Deploy the branded download landing to Firebase Hosting while preserving every live file."""
import copy
import gzip
import hashlib
import json
import os
import time

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
PAGE_URL = "https://dr-accesorios-rd.web.app/descargar/"

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def main():
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

    old_release, old_version = active()
    old = api("GET", old_version)
    old_files = files(old_version)
    require(bool(old_files), "Hosting inventory is empty")
    if "fileCount" in old:
        require(len(old_files) == int(old["fileCount"]), "Incomplete Hosting inventory")
    require("/version.json" in old_files, "Live version.json is missing")

    with open("landing/descargar/index.html", "rb") as stream:
        html = stream.read()
    require(b"DR_DOWNLOAD_V24" in html, "Landing marker missing")

    body = gzip.compress(html, mtime=0)
    digest = hashlib.sha256(body).hexdigest()
    expected = dict(old_files)
    expected["/descargar/index.html"] = digest

    config = copy.deepcopy(old.get("config", {}))
    headers = config.setdefault("headers", [])
    if not any(h.get("glob") == "/descargar/**" for h in headers):
        headers.append({
            "glob": "/descargar/**",
            "headers": {
                "Cache-Control": "public, max-age=300",
                "X-Content-Type-Options": "nosniff"
            }
        })

    created = api("POST", SITE + "/versions", json={"config": config})
    new_version = SITE + "/versions/" + created["name"].rsplit("/", 1)[1]
    items = list(expected.items())

    for offset in range(0, len(items), 1000):
        batch = api("POST", new_version + ":populateFiles", json={"files": dict(items[offset:offset + 1000])})
        required = set(batch.get("uploadRequiredHashes", []))
        require(required <= {digest}, "An existing Hosting file is unavailable; aborting")
        if digest in required:
            upload = session.post(
                batch["uploadUrl"] + "/" + digest,
                data=body,
                headers={"Content-Type": "application/octet-stream"},
                timeout=60)
            upload.raise_for_status()

    require(files(new_version) == expected, "File inventory mismatch before finalization")
    api("PATCH", new_version, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    require(active() == (old_release, old_version), "Another Hosting deployment occurred; aborting")
    release = api(
        "POST",
        SITE + "/releases",
        params={"versionName": new_version},
        json={"message": "Add branded DR Accesorios RD v2.4 download page; preserve all live files"})
    print("Hosting release:", release["name"], flush=True)
    require(active()[1] == new_version, "Unexpected active Hosting version")
    require(files(new_version) == expected, "Released inventory mismatch")

    for attempt in range(18):
        response = requests.get(PAGE_URL, params={"verify": time.time_ns()}, timeout=30)
        if response.ok and "DR_DOWNLOAD_V24" in response.text:
            print("VERIFIED:", PAGE_URL, flush=True)
            return
        time.sleep(5)
    raise RuntimeError("Landing verification did not complete")

if __name__ == "__main__":
    main()
