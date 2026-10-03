#!/usr/bin/env python3
"""Safely deploy only /radar.json while preserving every other Hosting file."""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import subprocess
import time
from pathlib import Path

import requests

PROJECT_ID = "dr-accesorios-rd"
SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
RADAR_PATH = "/radar.json"
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"
CRITICAL_PATHS = (
    "/radar.json",
    "/descargar/index.html",
    "/dr-audio/index.json",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def command_output(command: list[str]) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or "No se pudo ejecutar: " + " ".join(command)
        )
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "radar_file",
        nargs="?",
        default="public/radar.json",
    )
    args = parser.parse_args()

    radar_file = Path(args.radar_file).resolve()
    require(radar_file.exists(), f"No existe {radar_file}")

    radar = json.loads(
        radar_file.read_text(encoding="utf-8")
    )
    require(
        isinstance(radar, dict),
        "radar.json no contiene un objeto JSON válido.",
    )

    token = command_output(
        ["gcloud", "auth", "print-access-token"]
    )
    require(bool(token), "No se pudo obtener token de gcloud.")

    session = requests.Session()
    session.headers.update(
        {"Authorization": f"Bearer {token}"}
    )

    def api(method: str, path: str, **kwargs):
        response = session.request(
            method,
            BASE + path,
            timeout=90,
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def active():
        release = api(
            "GET",
            SITE + "/channels/live",
        )["release"]
        version_id = (
            release["version"]["name"]
            .rsplit("/", 1)[1]
        )
        return (
            release["name"],
            SITE + "/versions/" + version_id,
        )

    def files(version: str) -> dict[str, str]:
        result: dict[str, str] = {}
        token_value = None

        while True:
            params = {
                "pageSize": 1000,
                "status": "ACTIVE",
            }
            if token_value:
                params["pageToken"] = token_value

            page = api(
                "GET",
                version + "/files",
                params=params,
            )

            for item in page.get("files", []):
                path = item["path"]
                require(
                    path not in result,
                    f"Ruta duplicada en Hosting: {path}",
                )
                result[path] = item["hash"]

            token_value = page.get("nextPageToken")
            if not token_value:
                return result

    old_release, old_version = active()
    old = api("GET", old_version)
    old_files = files(old_version)

    require(
        bool(old_files),
        "El inventario activo de Hosting está vacío.",
    )

    missing_critical = [
        path
        for path in CRITICAL_PATHS
        if path not in old_files
    ]
    require(
        not missing_critical,
        (
            "ABORTADO: faltan rutas críticas en Hosting antes del deploy: "
            + " | ".join(missing_critical)
        ),
    )

    # Verificación HTTP adicional antes de publicar.
    for route in ("/descargar/", "/dr-audio/index.json", "/radar.json"):
        response = requests.get(
            PUBLIC_BASE + route,
            params={"precheck": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(
            response.ok,
            f"ABORTADO: {route} no responde antes del deploy "
            f"(HTTP {response.status_code}).",
        )

    config = copy.deepcopy(old.get("config", {}))
    headers = config.setdefault("headers", [])

    radar_header = next(
        (
            item
            for item in reversed(headers)
            if item.get("glob") == RADAR_PATH
        ),
        None,
    )

    if radar_header is None:
        radar_header = {
            "glob": RADAR_PATH,
            "headers": {},
        }
        headers.append(radar_header)

    radar_header.setdefault("headers", {})[
        "Cache-Control"
    ] = "no-cache, no-store, must-revalidate"

    raw = (
        json.dumps(
            radar,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")

    body = gzip.compress(raw, mtime=0)
    digest = hashlib.sha256(body).hexdigest()

    expected = dict(old_files)
    expected[RADAR_PATH] = digest

    created = api(
        "POST",
        SITE + "/versions",
        json={"config": config},
    )

    new_version = (
        SITE
        + "/versions/"
        + created["name"].rsplit("/", 1)[1]
    )

    items = list(expected.items())
    uploaded = False

    for offset in range(0, len(items), 1000):
        batch = api(
            "POST",
            new_version + ":populateFiles",
            json={
                "files": dict(
                    items[offset : offset + 1000]
                )
            },
        )

        required_hashes = set(
            batch.get("uploadRequiredHashes", [])
        )

        require(
            required_hashes <= {digest},
            (
                "Firebase pidió volver a subir un archivo distinto "
                "de radar.json; se aborta por seguridad."
            ),
        )

        if digest in required_hashes and not uploaded:
            upload = session.post(
                batch["uploadUrl"] + "/" + digest,
                data=body,
                headers={
                    "Content-Type":
                        "application/octet-stream"
                },
                timeout=90,
            )
            upload.raise_for_status()
            uploaded = True

    require(
        files(new_version) == expected,
        (
            "El inventario de la nueva versión no coincide "
            "exactamente con el inventario preservado."
        ),
    )

    api(
        "PATCH",
        new_version,
        params={"updateMask": "status"},
        json={"status": "FINALIZED"},
    )

    require(
        active() == (old_release, old_version),
        (
            "Otro deploy cambió Hosting mientras Radar preparaba "
            "la publicación. Se abortó antes de activar la versión."
        ),
    )

    api(
        "POST",
        SITE + "/releases",
        params={"versionName": new_version},
        json={
            "message":
                "DR Radar: publicar solo radar.json preservando Hosting"
        },
    )

    require(
        active()[1] == new_version,
        "La nueva versión de Hosting no quedó activa.",
    )

    # Verificar que Radar cambió y que las superficies críticas sobrevivieron.
    live_radar = requests.get(
        PUBLIC_BASE + "/radar.json",
        params={"verify": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    live_radar.raise_for_status()
    require(
        live_radar.json().get("updatedAt") == radar.get("updatedAt"),
        "El radar.json público no coincide con el archivo local.",
    )

    landing = requests.get(
        PUBLIC_BASE + "/descargar/",
        params={"verify": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    require(
        landing.ok,
        f"ALERTA: /descargar/ no sobrevivió al deploy "
        f"(HTTP {landing.status_code}).",
    )

    audio = requests.get(
        PUBLIC_BASE + "/dr-audio/index.json",
        params={"verify": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    require(
        audio.ok,
        f"ALERTA: DR Audio no sobrevivió al deploy "
        f"(HTTP {audio.status_code}).",
    )

    print("✅ radar.json publicado de forma segura.")
    print("✅ /descargar/ preservado.")
    print("✅ DR Audio preservado.")
    print("✅ El resto del inventario de Hosting se mantuvo intacto.")


if __name__ == "__main__":
    main()
