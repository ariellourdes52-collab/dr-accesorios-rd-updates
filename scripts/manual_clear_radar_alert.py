#!/usr/bin/env python3
"""Safely deactivate or delete one DR Radar alert without touching any other Hosting file."""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

PROJECT_ID = "dr-accesorios-rd"
SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
RADAR_PATH = "/radar.json"
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"
RADAR_URL = PUBLIC_BASE + RADAR_PATH
FCM_URL = f"https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send"

CRITICAL_PATHS = (
    "/radar.json",
    "/descargar/index.html",
    "/dr-audio/index.json",
    "/radar-widget/index.html",
    "/radar-alertas/index.html",
    "/radar-alertas/manifest.webmanifest",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert-id", required=True)
    parser.add_argument(
        "--action",
        choices=("deactivate", "delete"),
        default="deactivate",
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()

    alert_id = args.alert_id.strip()
    require(alert_id, "El ID de la alerta está vacío.")
    require(
        args.confirm.strip().upper() == "BORRAR",
        "Confirmación inválida. Debes escribir exactamente BORRAR.",
    )

    secret = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(secret, "FIREBASE_SERVICE_ACCOUNT no está configurado.")

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(secret),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    session = AuthorizedSession(credentials)

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
                require(
                    path not in result,
                    f"Ruta duplicada en Hosting: {path}",
                )
                result[path] = item["hash"]

            token = page.get("nextPageToken")
            if not token:
                return result

    # 1) Read the exact live Radar state; never trust a repository copy.
    response = requests.get(
        RADAR_URL,
        params={"manual_clear": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    response.raise_for_status()
    radar = response.json()

    alerts = radar.get("alerts")
    require(isinstance(alerts, list), "radar.json no contiene una lista alerts válida.")

    matches = [
        (index, alert)
        for index, alert in enumerate(alerts)
        if str(alert.get("id", "")).strip() == alert_id
    ]
    require(
        len(matches) == 1,
        (
            f"Se esperaba exactamente una alerta con id={alert_id!r}; "
            f"se encontraron {len(matches)}. No se modificó nada."
        ),
    )

    index, alert = matches[0]
    title = str(alert.get("title") or "").strip()

    if args.action == "deactivate":
        if alert.get("active") is False:
            print(f"OK: la alerta {alert_id} ya estaba desactivada. No se publica nada.")
            return
        alerts[index] = copy.deepcopy(alert)
        alerts[index]["active"] = False
        operation_label = "desactivada"
    else:
        del alerts[index]
        operation_label = "eliminada"

    radar["updatedAt"] = now_iso()

    print(f"Objetivo: {alert_id} | {title}")
    print(f"Acción: {args.action}")
    print("Preparando deploy quirúrgico de /radar.json...")

    # 2) Snapshot the current live Hosting release and all file hashes.
    old_release, old_version = active()
    old_version_data = api("GET", old_version)
    old_files = files(old_version)

    require(bool(old_files), "El inventario activo de Hosting está vacío.")

    missing = [path for path in CRITICAL_PATHS if path not in old_files]
    require(
        not missing,
        "ABORTADO: faltan rutas críticas antes del deploy: " + " | ".join(missing),
    )

    # 3) Preserve the complete live Hosting config and inventory.
    config = copy.deepcopy(old_version_data.get("config", {}))
    headers = config.setdefault("headers", [])

    radar_header = next(
        (item for item in reversed(headers) if item.get("glob") == RADAR_PATH),
        None,
    )
    if radar_header is None:
        radar_header = {"glob": RADAR_PATH, "headers": {}}
        headers.append(radar_header)

    radar_header.setdefault("headers", {})["Cache-Control"] = (
        "no-cache, no-store, must-revalidate"
    )

    raw = (
        json.dumps(radar, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    body = gzip.compress(raw, mtime=0)
    digest = hashlib.sha256(body).hexdigest()

    expected = dict(old_files)
    expected[RADAR_PATH] = digest

    # 4) Create a new Hosting version containing every existing hash,
    #    except the new /radar.json hash.
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
            (
                "ABORTADO: Firebase pidió subir un archivo diferente de "
                "/radar.json. No se activará esta versión."
            ),
        )

        if digest in required_hashes and not uploaded:
            upload = session.post(
                batch["uploadUrl"] + "/" + digest,
                data=body,
                headers={"Content-Type": "application/octet-stream"},
                timeout=90,
            )
            upload.raise_for_status()
            uploaded = True

    new_files = files(new_version)
    require(
        new_files == expected,
        "ABORTADO: el inventario de la nueva versión no coincide exactamente.",
    )

    # Explicit invariant: every non-Radar hash must be byte-for-byte identical.
    changed_non_radar = [
        path
        for path, old_hash in old_files.items()
        if path != RADAR_PATH and new_files.get(path) != old_hash
    ]
    require(
        not changed_non_radar,
        "ABORTADO: cambió un archivo ajeno a Radar: " + " | ".join(changed_non_radar),
    )

    api(
        "PATCH",
        new_version,
        params={"updateMask": "status"},
        json={"status": "FINALIZED"},
    )

    # 5) Race guard: if anything else deployed while we prepared this release,
    #    stop before changing live Hosting.
    require(
        active() == (old_release, old_version),
        (
            "ABORTADO: otro deploy cambió Hosting mientras se preparaba "
            "la operación. Ejecuta el workflow otra vez."
        ),
    )

    api(
        "POST",
        SITE + "/releases",
        params={"versionName": new_version},
        json={
            "message": (
                f"DR Radar manual: {args.action} {alert_id}; "
                "solo radar.json"
            )
        },
    )

    require(active()[1] == new_version, "La versión nueva no quedó activa.")

    # 6) Verify public Radar state.
    live = requests.get(
        RADAR_URL,
        params={"verify_manual_clear": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    live.raise_for_status()
    live_radar = live.json()
    live_alerts = live_radar.get("alerts", [])

    live_matches = [
        item
        for item in live_alerts
        if str(item.get("id", "")).strip() == alert_id
    ]

    if args.action == "deactivate":
        require(
            len(live_matches) == 1 and live_matches[0].get("active") is False,
            "La alerta pública no quedó desactivada como se esperaba.",
        )
    else:
        require(
            not live_matches,
            "La alerta todavía aparece en radar.json después de eliminarla.",
        )

    # 7) Verify critical public surfaces after release.
    public_checks = (
        "/descargar/",
        "/dr-audio/index.json",
        "/radar-widget/index.html",
        "/radar-alertas/index.html",
        "/radar-alertas/manifest.webmanifest",
    )
    for route in public_checks:
        check = requests.get(
            PUBLIC_BASE + route,
            params={"verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(
            check.ok,
            f"ALERTA: {route} no responde después del deploy "
            f"(HTTP {check.status_code}).",
        )

    print(f"✅ Alerta {alert_id} {operation_label}.")
    print("✅ Solo /radar.json cambió.")
    print(f"✅ {len(old_files) - 1} archivos ajenos a Radar preservados por hash.")
    print("✅ /descargar/ preservado.")
    print("✅ DR Audio preservado.")
    print("✅ Widget Radar preservado.")
    print("✅ Web app DR Accesorios RD preservada.")

    # 8) Best-effort Android refresh. A notification refresh failure must not
    #    turn a successful safe Hosting operation into a destructive retry.
    try:
        fcm = session.post(
            FCM_URL,
            json={
                "message": {
                    "topic": "blog_updates",
                    "android": {
                        "priority": "high",
                        "collapse_key": "dr_radar_refresh",
                        "restricted_package_name": "com.draccesoriosrd.app",
                    },
                    "data": {
                        "type": "radar_refresh",
                        "source": "MANUAL_CLEAR",
                        "alertId": alert_id,
                    },
                }
            },
            timeout=60,
        )
        if fcm.ok:
            print("✅ radar_refresh enviado a Android.")
        else:
            print(
                "⚠️ Radar quedó actualizado, pero FCM respondió "
                f"HTTP {fcm.status_code}. El widget web refresca por sí solo."
            )
    except Exception as exc:
        print(
            "⚠️ Radar quedó actualizado, pero no se pudo enviar "
            f"radar_refresh: {exc}"
        )


if __name__ == "__main__":
    main()
