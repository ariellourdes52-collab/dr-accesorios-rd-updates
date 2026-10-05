#!/usr/bin/env python3
"""Deploy only /radar-widget/index.html while preserving every live Hosting file."""
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

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"
WIDGET_PATH = "/radar-widget/index.html"
WIDGET_SOURCE = Path("landing/radar-widget/index.html")
WIDGET_MARKER = b"DR_RADAR_BLOGGER_WIDGET_V1"
CRITICAL_PATHS = (
    "/radar.json",
    "/descargar/index.html",
    "/dr-audio/index.json",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    secret = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(secret, "FIREBASE_SERVICE_ACCOUNT no está configurado.")
    require(WIDGET_SOURCE.exists(), f"No existe {WIDGET_SOURCE}.")

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(secret),
        scopes=["https://www.googleapis.com/auth/firebase.hosting"],
    )
    session = AuthorizedSession(credentials)

    def api(method: str, path: str, **kwargs):
        response = session.request(method, BASE + path, timeout=60, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def active():
        release = api("GET", SITE + "/channels/live")["release"]
        version_id = release["version"]["name"].rsplit("/", 1)[1]
        return release["name"], SITE + "/versions/" + version_id

    def files(version: str) -> dict[str, str]:
        result: dict[str, str] = {}
        token = None
        while True:
            params = {"pageSize": 1000, "status": "ACTIVE"}
            if token:
                params["pageToken"] = token
            page = api("GET", version + "/files", params=params)
            for item in page.get("files", []):
                path = item["path"]
                require(path not in result, f"Ruta duplicada en Hosting: {path}")
                result[path] = item["hash"]
            token = page.get("nextPageToken")
            if not token:
                return result

    old_release, old_version = active()
    old = api("GET", old_version)
    old_files = files(old_version)
    require(bool(old_files), "El inventario activo de Hosting está vacío.")

    missing = [path for path in CRITICAL_PATHS if path not in old_files]
    require(
        not missing,
        "ABORTADO: faltan rutas críticas antes del deploy: " + " | ".join(missing),
    )

    for route in ("/radar.json", "/descargar/", "/dr-audio/index.json"):
        response = requests.get(
            PUBLIC_BASE + route,
            params={"precheck": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(response.ok, f"ABORTADO: {route} no responde (HTTP {response.status_code}).")

    html = WIDGET_SOURCE.read_bytes()
    require(WIDGET_MARKER in html, "Falta el marcador del widget DR Radar.")
    require(b"/radar.json" in html, "El widget no referencia radar.json.")
    require(b"/radar-seismic-v231.json" in html, "El widget no referencia el feed sísmico.")

    body = gzip.compress(html, mtime=0)
    digest = hashlib.sha256(body).hexdigest()

    expected = dict(old_files)
    expected[WIDGET_PATH] = digest

    config = copy.deepcopy(old.get("config", {}))
    headers = config.setdefault("headers", [])
    widget_header = next(
        (item for item in reversed(headers) if item.get("glob") == "/radar-widget/**"),
        None,
    )
    if widget_header is None:
        widget_header = {"glob": "/radar-widget/**", "headers": {}}
        headers.append(widget_header)
    widget_header.setdefault("headers", {})["Cache-Control"] = "public, max-age=300"
    widget_header["headers"]["X-Content-Type-Options"] = "nosniff"

    created = api("POST", SITE + "/versions", json={"config": config})
    new_version = SITE + "/versions/" + created["name"].rsplit("/", 1)[1]
    items = list(expected.items())
    uploaded = False

    for offset in range(0, len(items), 1000):
        batch = api(
            "POST",
            new_version + ":populateFiles",
            json={"files": dict(items[offset : offset + 1000])},
        )
        required_hashes = set(batch.get("uploadRequiredHashes", []))
        require(
            required_hashes <= {digest},
            "Firebase pidió volver a subir un archivo preservado; se aborta.",
        )
        if digest in required_hashes and not uploaded:
            upload = session.post(
                batch["uploadUrl"] + "/" + digest,
                data=body,
                headers={"Content-Type": "application/octet-stream"},
                timeout=60,
            )
            upload.raise_for_status()
            uploaded = True

    require(files(new_version) == expected, "El inventario nuevo no coincide con el preservado.")
    api("PATCH", new_version, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    require(
        active() == (old_release, old_version),
        "Otro deploy cambió Hosting durante la preparación; se abortó antes de publicar.",
    )

    release = api(
        "POST",
        SITE + "/releases",
        params={"versionName": new_version},
        json={"message": "DR Radar Blogger widget; preserve all live Hosting files"},
    )
    print("Hosting release:", release.get("name", ""), flush=True)
    require(active()[1] == new_version, "La nueva versión de Hosting no quedó activa.")
    require(files(new_version) == expected, "El inventario publicado no coincide.")

    verified = False
    for _ in range(18):
        response = requests.get(
            PUBLIC_BASE + "/radar-widget/",
            params={"verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        if response.ok and "DR_RADAR_BLOGGER_WIDGET_V1" in response.text:
            verified = True
            break
        time.sleep(5)
    require(verified, "El widget no pudo verificarse después del deploy.")

    for route in ("/radar.json", "/descargar/", "/dr-audio/index.json"):
        response = requests.get(
            PUBLIC_BASE + route,
            params={"verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(response.ok, f"ALERTA: {route} no sobrevivió al deploy.")

    print("✅ /radar-widget/ publicado.")
    print("✅ radar.json preservado.")
    print("✅ /descargar/ preservado.")
    print("✅ DR Audio preservado.")
    print("✅ El resto de Firebase Hosting se mantuvo intacto.")


if __name__ == "__main__":
    main()
