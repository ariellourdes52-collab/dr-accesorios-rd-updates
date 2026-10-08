#!/usr/bin/env python3
"""Safely publish one manual DR Radar alert by changing only /radar.json."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

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

PUBLIC_CHECKS = (
    "/descargar/",
    "/dr-audio/index.json",
    "/radar-widget/index.html",
    "/radar-alertas/index.html",
    "/radar-alertas/manifest.webmanifest",
)

ALLOWED_SEVERITIES = {"info", "warning", "critical"}
ALLOWED_CATEGORIES = {"general", "tech", "service", "outage", "weather"}
ALERT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,96}$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def clean_text(value: str, max_len: int, field: str, min_len: int = 0) -> str:
    text = str(value or "").replace("\x00", "").strip()
    require(len(text) >= min_len, f"{field} está vacío o es demasiado corto.")
    require(len(text) <= max_len, f"{field} supera {max_len} caracteres.")
    return text


def validate_url(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.upper() == "NINGUNO":
        return ""
    require(len(text) <= 1000, "La URL supera 1000 caracteres.")
    parsed = urlparse(text)
    require(parsed.scheme == "https" and bool(parsed.netloc), "La URL debe usar https://")
    return text


def main() -> None:
    alert_id = clean_text(os.environ.get("RADAR_ALERT_ID", ""), 96, "RADAR_ALERT_ID", 8)
    require(ALERT_ID_RE.fullmatch(alert_id) is not None, "RADAR_ALERT_ID contiene caracteres no permitidos.")

    title = clean_text(os.environ.get("RADAR_TITLE", ""), 160, "Título", 4)
    description = clean_text(os.environ.get("RADAR_DESCRIPTION", ""), 1800, "Descripción", 4)
    url = validate_url(os.environ.get("RADAR_URL", ""))

    severity = str(os.environ.get("RADAR_SEVERITY", "warning")).strip().lower()
    require(severity in ALLOWED_SEVERITIES, "Severidad no permitida.")

    category = str(os.environ.get("RADAR_CATEGORY", "general")).strip().lower()
    require(category in ALLOWED_CATEGORIES, "Categoría no permitida.")
    require(category not in {"earthquake", "seismic", "sismo"}, "Las alertas sísmicas reales no se publican manualmente.")

    try:
        duration_minutes = int(str(os.environ.get("RADAR_DURATION_MINUTES", "120")).strip())
    except ValueError as exc:
        raise RuntimeError("Duración inválida.") from exc
    require(5 <= duration_minutes <= 10080, "Duración fuera del rango permitido (5 min a 7 días).")

    secret = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(secret, "FIREBASE_SERVICE_ACCOUNT no está configurado.")

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(secret),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    session = AuthorizedSession(credentials)

    def api(method: str, path: str, **kwargs):
        response = session.request(method, BASE + path, timeout=90, **kwargs)
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

    # 1) Read the exact live Radar state; never trust a repository copy.
    response = requests.get(
        RADAR_URL,
        params={"manual_publish": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    response.raise_for_status()
    radar = response.json()
    require(isinstance(radar, dict), "radar.json no contiene un objeto JSON válido.")
    alerts = radar.get("alerts")
    require(isinstance(alerts, list), "radar.json no contiene una lista alerts válida.")

    duplicates = [
        item for item in alerts
        if isinstance(item, dict) and str(item.get("id") or "").strip() == alert_id
    ]
    require(not duplicates, f"Ya existe una alerta con id={alert_id!r}. No se modificó nada.")

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=duration_minutes)

    alert = {
        "id": alert_id,
        "active": True,
        "createdAt": created_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "expiresAt": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "url": url,
        "target": {
            "allDevices": True,
            "manufacturers": [],
            "models": [],
        },
        "type": category,
        "source": "TELEGRAM_ADMIN",
    }

    next_radar = copy.deepcopy(radar)
    next_radar["alerts"] = list(alerts) + [alert]
    next_radar["updatedAt"] = now_iso()

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

    # Prevent resurrecting a previously deactivated alert from stale CDN data.
    # Every DR Radar Hosting publisher uploads deterministic gzip(mtime=0).
    # Match the public bytes against the *current live release's* file hash
    # before making any change; Python 3.11/3.12 may differ in gzip OS byte.
    public_gzip = gzip.compress(response.content, mtime=0)
    possible_hashes = {hashlib.sha256(public_gzip).hexdigest()}
    if len(public_gzip) > 9 and public_gzip[9] in (3, 255):
        alternative_gzip = bytearray(public_gzip)
        alternative_gzip[9] = 255 if public_gzip[9] == 3 else 3
        possible_hashes.add(hashlib.sha256(alternative_gzip).hexdigest())

    require(
        old_files[RADAR_PATH] in possible_hashes,
        (
            "ABORTADO: radar.json devolvió una copia distinta de la versión "
            "activa de Firebase Hosting (posible caché atrasada). "
            "No se publicó nada para evitar reactivar alertas eliminadas."
        ),
    )

    # 3) HTTP preflight on critical public surfaces before any release.
    for route in ("/radar.json",) + PUBLIC_CHECKS:
        check = requests.get(
            PUBLIC_BASE + route,
            params={"precheck": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(
            check.ok,
            f"ABORTADO: {route} no responde antes del deploy (HTTP {check.status_code}).",
        )

    # 4) Preserve the complete live Hosting config and inventory.
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

    raw = (json.dumps(next_radar, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    body = gzip.compress(raw, mtime=0)
    digest = hashlib.sha256(body).hexdigest()

    expected = dict(old_files)
    expected[RADAR_PATH] = digest

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

    # 5) Race guard: another deployment means abort before touching live.
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
                f"DR Radar Telegram admin: publish {alert_id}; solo radar.json"
            )
        },
    )

    require(active()[1] == new_version, "La versión nueva no quedó activa.")

    # 6) Verify exact public Radar state.
    live = requests.get(
        RADAR_URL,
        params={"verify_manual_publish": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    live.raise_for_status()
    live_radar = live.json()
    live_matches = [
        item for item in live_radar.get("alerts", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip() == alert_id
    ]
    require(len(live_matches) == 1, "La nueva alerta no aparece exactamente una vez en radar.json.")
    live_alert = live_matches[0]
    require(live_alert.get("active") is not False, "La nueva alerta no quedó activa.")
    require(str(live_alert.get("title") or "") == title, "El título publicado no coincide.")
    require(str(live_alert.get("description") or "") == description, "La descripción publicada no coincide.")
    require(str(live_alert.get("url") or "") == url, "La URL publicada no coincide.")

    # 7) Verify critical public surfaces after release.
    for route in PUBLIC_CHECKS:
        check = requests.get(
            PUBLIC_BASE + route,
            params={"verify": time.time_ns()},
            headers={"Cache-Control": "no-cache"},
            timeout=30,
        )
        require(
            check.ok,
            f"ALERTA: {route} no responde después del deploy (HTTP {check.status_code}).",
        )

    print(f"✅ Alerta {alert_id} publicada.")
    print("✅ Solo /radar.json cambió.")
    print(f"✅ {len(old_files) - 1} archivos ajenos a Radar preservados por hash.")
    print("✅ /descargar/ preservado.")
    print("✅ DR Audio preservado.")
    print("✅ Widget Radar preservado.")
    print("✅ Web app DR Accesorios RD preservada.")

    # 8) Refresh Android after the safe publish. A notification failure does not
    #    cause another Hosting deployment.
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
                        "source": "TELEGRAM_ADMIN_PUBLISH",
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
                "⚠️ Radar quedó publicado, pero FCM respondió "
                f"HTTP {fcm.status_code}. La web y Telegram refrescan por sí solos."
            )
    except Exception as exc:
        print(
            "⚠️ Radar quedó publicado, pero no se pudo enviar "
            f"radar_refresh: {exc}"
        )


if __name__ == "__main__":
    main()
