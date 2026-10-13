#!/usr/bin/env python3
"""Safely deploy only three iOS DR Agenda PWA files, preserving Firebase Hosting."""
import argparse
import copy
import gzip
import hashlib
import json
import os
import time
from pathlib import Path

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

from seismic_feed_guard import require_seismic_preserved

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
HOST = "https://dr-accesorios-rd.web.app"
FILES = {
    "/radar-alertas/index.html": "landing/radar-alertas/index.html",
    "/radar-alertas/sw.js": "landing/radar-alertas/sw.js",
    "/radar-alertas/agenda.html": "landing/radar-alertas/agenda.html",
}
# Git blobs of last GitHub originals reviewed BEFORE iOS changes, not PWA-generated files.
ORIGINAL_GIT_BLOBS = {
    "/radar-alertas/index.html": "a35125708e5ada8ac67ce52fa7dae61655900d0d",
    "/radar-alertas/sw.js": "2f4bd1bf1d86efbe9684ac7b67928ffd10a01e0b",
}
CRITICAL = (
    "/version.json", "/radar.json", "/dr-audio/index.json",
    "/radar-alertas/index.html", "/radar-alertas/sw.js",
    "/radar-alertas/manifest.webmanifest",
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auditar", "publicar"), default="auditar")
    parser.add_argument("--confirm", default="NO")
    args = parser.parse_args()
    if args.mode == "publicar":
        require(args.confirm == "PUBLICAR-DR-AGENDA-IOS",
                "Se requiere confirmación exacta de publicación.")
    info = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    require(info.get("project_id") == "dr-accesorios-rd", "Proyecto Firebase incorrecto")
    credential = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/firebase.hosting"])
    session = AuthorizedSession(credential)

    def api(method, path, **kwargs):
        r = session.request(method, BASE + path, timeout=90, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else {}

    def active():
        channel = api("GET", SITE + "/channels/live")["release"]
        return channel["name"], SITE + "/versions/" + channel["version"]["name"].rsplit("/", 1)[1]

    def listing(version):
        found, token = {}, None
        while True:
            params = {"pageSize": 1000, "status": "ACTIVE"}
            if token:
                params["pageToken"] = token
            batch = api("GET", version + "/files", params=params)
            for item in batch.get("files", []):
                require(item["path"] not in found, "Ruta Hosting duplicada")
                found[item["path"]] = item["hash"]
            token = batch.get("nextPageToken")
            if not token:
                return found

    old_release, old_version = active()
    old = api("GET", old_version)
    live = listing(old_version)
    require(all(x in live for x in CRITICAL), "Hosting incompleto: abortado.")
    if "fileCount" in old:
        require(int(old["fileCount"]) == len(live), "Inventario Hosting incompleto")
    # Prevent silently overwriting a PWA modified outside this exact source tree.
    for route, sha in ORIGINAL_GIT_BLOBS.items():
        r = requests.get(HOST + route, params={"audit":time.time_ns()},
                         timeout=30, headers={"Cache-Control":"no-cache"})
        r.raise_for_status()
        require(git_blob(r.content) == sha,
                "La versión LIVE de " + route + " difiere de la base revisada; "
                "revisar cambios antes de reemplazar. Sin publicar nada.")

    contents = {route: Path(source).read_bytes() for route, source in FILES.items()}
    require(b"dr-agenda-push-v1" in contents["/radar-alertas/agenda.html"], "Historial iOS ausente")
    require(b"DR_RADAR_WEBPUSH_SW_V3" in contents["/radar-alertas/sw.js"], "Service Worker incorrecto")
    print("OK: LIVE sin cambios externos; rutas críticas conservadas", flush=True)
    if args.mode == "auditar":
        print("AUDITORÍA SOLAMENTE: ningún despliegue, ninguna notificación.", flush=True)
        return

    encoded = {route:gzip.compress(raw, mtime=0) for route, raw in contents.items()}
    changed = {route:hashlib.sha256(data).hexdigest() for route, data in encoded.items()}
    expected = dict(live)
    expected.update(changed)
    config = copy.deepcopy(old.get("config", {}))
    headers = config.setdefault("headers", [])
    for route in changed:
        header = next((h for h in reversed(headers) if h.get("glob") == route), None)
        if not header:
            header = {"glob":route,"headers":{}}
            headers.append(header)
        header.setdefault("headers", {})["Cache-Control"] = "no-cache"
        header["headers"]["X-Content-Type-Options"] = "nosniff"

    require_seismic_preserved(live, expected)

    created = api("POST", SITE + "/versions", json={"config":config})
    version = SITE + "/versions/" + created["name"].rsplit("/", 1)[1]
    bodies = {digest:encoded[route] for route, digest in changed.items()}
    for offset in range(0, len(expected), 1000):
        chunk = dict(list(expected.items())[offset:offset+1000])
        response = api("POST", version + ":populateFiles", json={"files":chunk})
        missing = set(response.get("uploadRequiredHashes", []))
        require(missing <= set(bodies), "Hosting solicitó archivos externos: abortado")
        for digest in missing:
            upload = session.post(response["uploadUrl"] + "/" + digest,
                                  data=bodies[digest],
                                  headers={"Content-Type":"application/octet-stream"},timeout=90)
            upload.raise_for_status()

    require(listing(version) == expected, "Inventario de versión distinto: abortado")
    api("PATCH", version, params={"updateMask":"status"}, json={"status":"FINALIZED"})
    require(active() == (old_release, old_version), "Otro deploy modificó Hosting: abortado")
    api("POST", SITE + "/releases", params={"versionName":version},
        json={"message":"DR Agenda iOS PWA: solo 3 archivos, sin tocar Radar, Audio ni Blog"})
    require(active()[1] == version, "Versión publicada inesperada")
    require(listing(version) == expected, "Inventario LIVE cambió inesperadamente")
    for route, marker in (
        ("/radar-alertas/sw.js", "DR_RADAR_WEBPUSH_SW_V3"),
        ("/radar-alertas/agenda.html", "dr-agenda-push-v1"),
        ("/radar-alertas/index.html", "DR Agenda · Eventos y avisos iOS"),
    ):
        r = requests.get(HOST + route, params={"verify":time.time_ns()},
                         timeout=30, headers={"Cache-Control":"no-cache"})
        require(r.ok and marker in r.text, "Fallo de comprobación después del deploy: " + route)
    print("PUBLICADO: DR Agenda iOS, 3 archivos; demás contenido preservado.", flush=True)


if __name__ == "__main__":
    main()
