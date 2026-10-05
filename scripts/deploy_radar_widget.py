#!/usr/bin/env python3
"""Deploy DR Radar Blogger web assets while preserving every live Hosting file."""
from __future__ import annotations

import base64
import copy
import io
import gzip
import hashlib
import json
import os
import time
from pathlib import Path

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from PIL import Image

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"

ASSETS = {
    "/radar-widget/index.html": (Path("landing/radar-widget/index.html"), b"DR_RADAR_BLOGGER_WIDGET_V1"),
    "/radar-widget/sw.js": (Path("landing/radar-alertas/sw.js"), b"DR_RADAR_WEBPUSH_SW_V2"),
    "/radar-alertas/index.html": (Path("landing/radar-alertas/index.html"), b"DR_RADAR_BROWSER_ALERTS_V2"),
    "/radar-alertas/sw.js": (Path("landing/radar-alertas/sw.js"), b"DR_RADAR_WEBPUSH_SW_V2"),
    "/radar-alertas/manifest.webmanifest": (Path("landing/radar-alertas/manifest.webmanifest"), b"\"name\": \"DR Accesorios RD\""),
}

CRITICAL_PATHS = (
    "/radar.json",
    "/descargar/index.html",
    "/dr-audio/index.json",
)

ICON_SOURCE_PARTS = tuple(
    Path(f"landing/radar-alertas/icon-source/icon96.b64.{index:02d}")
    for index in range(1, 12)
)


def build_icon_assets() -> dict[str, bytes]:
    for part in ICON_SOURCE_PARTS:
        require(part.exists(), f"No existe la parte del logo oficial: {part}.")
    encoded = "".join(part.read_text(encoding="utf-8").strip() for part in ICON_SOURCE_PARTS)
    try:
        source_bytes = base64.b64decode(encoded, validate=True)
        source = Image.open(io.BytesIO(source_bytes)).convert("RGBA")
    except Exception as exc:
        raise RuntimeError(f"No se pudo reconstruir el logo oficial: {exc}") from exc

    require(source.size == (96, 96), f"Logo fuente inesperado: {source.size}.")
    assets: dict[str, bytes] = {}
    for size in (180, 192, 512):
        output = io.BytesIO()
        resized = source.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(output, format="PNG", optimize=True)
        assets[f"/radar-alertas/icon-{size}.png"] = output.getvalue()
    return assets


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    secret = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(secret, "FIREBASE_SERVICE_ACCOUNT no está configurado.")

    for _, (source, _) in ASSETS.items():
        require(source.exists(), f"No existe {source}.")

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

    prepared: dict[str, tuple[bytes, str]] = {}
    digest_to_body: dict[str, bytes] = {}

    for hosting_path, (source, marker) in ASSETS.items():
        raw = source.read_bytes()
        require(marker in raw, f"Falta marcador requerido en {source}.")
        body = gzip.compress(raw, mtime=0)
        digest = hashlib.sha256(body).hexdigest()
        prepared[hosting_path] = (body, digest)
        digest_to_body[digest] = body

    for hosting_path, raw in build_icon_assets().items():
        require(raw.startswith(b"\x89PNG\r\n\x1a\n"), f"Icono PNG inválido: {hosting_path}")
        body = gzip.compress(raw, mtime=0)
        digest = hashlib.sha256(body).hexdigest()
        prepared[hosting_path] = (body, digest)
        digest_to_body[digest] = body

    widget_raw = ASSETS["/radar-widget/index.html"][0].read_bytes()
    require(b"/radar.json" in widget_raw, "El widget no referencia radar.json.")
    require(b"/radar-seismic-v231.json" in widget_raw, "El widget no referencia el feed sísmico.")
    require(b"REFRESH_MS = 30_000" in widget_raw, "El widget no quedó configurado a 30 segundos.")
    require(b"/radar-alertas/index.html" in widget_raw, "El widget no referencia la activación de alertas.")

    expected = dict(old_files)
    for hosting_path, (_, digest) in prepared.items():
        expected[hosting_path] = digest

    config = copy.deepcopy(old.get("config", {}))
    headers = config.setdefault("headers", [])

    def set_headers(glob: str, values: dict[str, str]) -> None:
        item = next((x for x in reversed(headers) if x.get("glob") == glob), None)
        if item is None:
            item = {"glob": glob, "headers": {}}
            headers.append(item)
        item.setdefault("headers", {}).update(values)

    set_headers("/radar-widget/**", {
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
    })
    set_headers("/radar-alertas/**", {
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
    })

    created = api("POST", SITE + "/versions", json={"config": config})
    new_version = SITE + "/versions/" + created["name"].rsplit("/", 1)[1]
    items = list(expected.items())
    uploaded: set[str] = set()
    allowed_hashes = set(digest_to_body)

    for offset in range(0, len(items), 1000):
        batch = api(
            "POST",
            new_version + ":populateFiles",
            json={"files": dict(items[offset : offset + 1000])},
        )
        required_hashes = set(batch.get("uploadRequiredHashes", []))
        require(
            required_hashes <= allowed_hashes,
            "Firebase pidió volver a subir un archivo preservado; se aborta.",
        )
        for digest in required_hashes:
            if digest in uploaded:
                continue
            upload = session.post(
                batch["uploadUrl"] + "/" + digest,
                data=digest_to_body[digest],
                headers={"Content-Type": "application/octet-stream"},
                timeout=60,
            )
            upload.raise_for_status()
            uploaded.add(digest)

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
        json={"message": "DR Radar Blogger widget + browser alerts; preserve all live Hosting files"},
    )
    print("Hosting release:", release.get("name", ""), flush=True)
    require(active()[1] == new_version, "La nueva versión de Hosting no quedó activa.")
    require(files(new_version) == expected, "El inventario publicado no coincide.")

    checks = (
        ("/radar-widget/index.html", "DR_RADAR_BLOGGER_WIDGET_V1"),
        ("/radar-widget/sw.js", "DR_RADAR_WEBPUSH_SW_V2"),
        ("/radar-alertas/index.html", "DR_RADAR_BROWSER_ALERTS_V2"),
        ("/radar-alertas/sw.js", "DR_RADAR_WEBPUSH_SW_V2"),
        ("/radar-alertas/manifest.webmanifest", "\"name\": \"DR Accesorios RD\""),
    )
    for route, marker in checks:
        verified = False
        for _ in range(18):
            response = requests.get(
                PUBLIC_BASE + route,
                params={"verify": time.time_ns()},
                headers={"Cache-Control": "no-cache"},
                timeout=30,
            )
            if response.ok and marker in response.text:
                verified = True
                break
            time.sleep(5)
        require(verified, f"No pudo verificarse {route} después del deploy.")

    for route in ("/radar-alertas/icon-180.png", "/radar-alertas/icon-192.png", "/radar-alertas/icon-512.png"):
        response = requests.get(
            PUBLIC_BASE + route,
            params={"verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(
            response.ok and response.content.startswith(b"\x89PNG\r\n\x1a\n"),
            f"No pudo verificarse el icono oficial {route}.",
        )

    for route in ("/radar.json", "/descargar/", "/dr-audio/index.json"):
        response = requests.get(
            PUBLIC_BASE + route,
            params={"verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(response.ok, f"ALERTA: {route} no sobrevivió al deploy.")

    print("✅ /radar-widget/index.html publicado a 30 s.")
    print("✅ /radar-widget/sw.js publicado.")
    print("✅ /radar-alertas/index.html publicado.")
    print("✅ /radar-alertas/sw.js publicado.")
    print("✅ /radar-alertas/manifest.webmanifest publicado.")
    print("✅ Logo oficial PWA 180/192/512 publicado.")
    print("✅ radar.json preservado.")
    print("✅ /descargar/ preservado.")
    print("✅ DR Audio preservado.")
    print("✅ El resto de Firebase Hosting se mantuvo intacto.")


if __name__ == "__main__":
    main()
