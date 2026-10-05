#!/usr/bin/env python3
"""One-time surgical repair for /radar-widget/index.html only.

Clones the currently active Firebase Hosting inventory and replaces/adds exactly
one path. No other file, config entry, or route is intentionally changed.
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

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"
TARGET = "/radar-widget/index.html"
SOURCE = Path("landing/radar-widget/index.html")
MARKER = b"DR_RADAR_BLOGGER_WIDGET_V1"

CRITICAL = (
    "/radar.json",
    "/dr-audio/index.json",
    "/descargar/index.html",
    "/radar-alertas/index.html",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    secret = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(secret, "FIREBASE_SERVICE_ACCOUNT no configurado.")
    require(SOURCE.exists(), f"No existe {SOURCE}.")

    raw = SOURCE.read_bytes()
    require(MARKER in raw, "El widget fuente no contiene el marcador esperado.")
    require(b"REFRESH_MS = 30_000" in raw, "El widget fuente no está en 30 segundos.")
    require(b"/radar-alertas/index.html" in raw, "El widget fuente no enlaza la PWA 24/7.")

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
        out: dict[str, str] = {}
        token = None
        while True:
            params = {"pageSize": 1000, "status": "ACTIVE"}
            if token:
                params["pageToken"] = token
            page = api("GET", version + "/files", params=params)
            for item in page.get("files", []):
                path = item["path"]
                require(path not in out, f"Ruta duplicada: {path}")
                out[path] = item["hash"]
            token = page.get("nextPageToken")
            if not token:
                return out

    old_release, old_version = active()
    old_version_obj = api("GET", old_version)
    old_files = files(old_version)

    require(bool(old_files), "Inventario activo vacío.")
    missing = [p for p in CRITICAL if p not in old_files]
    require(not missing, "ABORTADO: faltan rutas críticas: " + " | ".join(missing))

    print("Release activo:", old_release)
    print("Versión activa:", old_version)
    print("Archivos activos:", len(old_files))
    print("Widget presente antes:", TARGET in old_files)

    # Public prechecks: do not proceed if the recovered core is not healthy.
    for route in ("/radar.json", "/dr-audio/index.json", "/descargar/", "/radar-alertas/index.html"):
        r = requests.get(
            PUBLIC_BASE + route,
            params={"repair_precheck": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(r.ok, f"ABORTADO: {route} responde HTTP {r.status_code}.")

    body = gzip.compress(raw, mtime=0)
    digest = hashlib.sha256(body).hexdigest()

    expected = dict(old_files)
    expected[TARGET] = digest

    # Preserve current live Hosting config byte-for-byte at the object level.
    config = copy.deepcopy(old_version_obj.get("config", {}))
    created = api("POST", SITE + "/versions", json={"config": config})
    new_version = SITE + "/versions/" + created["name"].rsplit("/", 1)[1]

    items = list(expected.items())
    uploaded = False

    for offset in range(0, len(items), 1000):
        batch = api(
            "POST",
            new_version + ":populateFiles",
            json={"files": dict(items[offset:offset + 1000])},
        )
        required_hashes = set(batch.get("uploadRequiredHashes", []))
        require(
            required_hashes <= {digest},
            "ABORTADO: Firebase pidió re-subir un archivo distinto del widget.",
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

    new_files = files(new_version)
    require(new_files == expected, "ABORTADO: el inventario nuevo no coincide.")

    # Explicitly prove every pre-existing path/hash is unchanged.
    for path, old_hash in old_files.items():
        if path == TARGET:
            continue
        require(
            new_files.get(path) == old_hash,
            f"ABORTADO: cambió inesperadamente {path}.",
        )

    api(
        "PATCH",
        new_version,
        params={"updateMask": "status"},
        json={"status": "FINALIZED"},
    )

    require(
        active() == (old_release, old_version),
        "ABORTADO: otro deploy cambió Hosting durante la reparación.",
    )

    release = api(
        "POST",
        SITE + "/releases",
        params={"versionName": new_version},
        json={"message": "SURGICAL REPAIR: restore /radar-widget/index.html only"},
    )
    print("Nuevo release:", release.get("name", ""))

    require(active()[1] == new_version, "La reparación no quedó activa.")
    require(files(new_version) == expected, "Inventario publicado inesperado.")

    # Verify the exact repaired route plus the already recovered core.
    verified = False
    last_status = None
    for _ in range(18):
        r = requests.get(
            PUBLIC_BASE + TARGET,
            params={"repair_verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        last_status = r.status_code
        if r.ok and MARKER.decode() in r.text:
            verified = True
            break
        time.sleep(5)
    require(verified, f"Widget no verificó después del repair: HTTP {last_status}")

    for route in ("/radar.json", "/dr-audio/index.json", "/descargar/", "/radar-alertas/index.html"):
        r = requests.get(
            PUBLIC_BASE + route,
            params={"repair_verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(r.ok, f"ALERTA: {route} dejó de responder: HTTP {r.status_code}")

    print("✅ /radar-widget/index.html restaurado.")
    print("✅ Todos los demás hashes de Hosting preservados.")
    print("✅ radar.json preservado.")
    print("✅ DR Audio preservado.")
    print("✅ /descargar/ preservado.")
    print("✅ /radar-alertas/ preservado.")
    print("✅ No se tocó Railway ni el motor de DR Radar.")


if __name__ == "__main__":
    main()
