#!/usr/bin/env python3
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
RADAR_URL = "https://dr-accesorios-rd.web.app/radar.json"
FCM_URL = f"https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parse_iso(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def now_iso():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def main():
    secret = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(secret, "FIREBASE_SERVICE_ACCOUNT no está configurado.")

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(secret),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    session = AuthorizedSession(credentials)

    def api(method, path, **kwargs):
        response = session.request(
            method,
            BASE + path,
            timeout=60,
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def active():
        release = api("GET", SITE + "/channels/live")["release"]
        version_id = release["version"]["name"].rsplit("/", 1)[1]
        return release["name"], SITE + "/versions/" + version_id

    def files(version):
        result = {}
        token = None

        while True:
            params = {
                "pageSize": 1000,
                "status": "ACTIVE",
            }
            if token:
                params["pageToken"] = token

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

            token = page.get("nextPageToken")
            if not token:
                return result

    response = requests.get(
        RADAR_URL,
        params={"seismic_home_expire_check": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    response.raise_for_status()
    radar = response.json()

    now = datetime.now(timezone.utc)
    expired = []

    for alert in radar.get("alerts", []):
        # Quirúrgico: SOLO sismos. Alertas tecnológicas quedan intactas.
        if str(alert.get("type") or "").strip().lower() != "earthquake":
            continue

        if not alert.get("active", False):
            continue

        raw_expires = str(alert.get("expiresAt") or "").strip()

        if not raw_expires:
            continue

        expires_at = parse_iso(raw_expires)

        if expires_at is None:
            print(
                "AVISO: expiresAt sísmico inválido; se conserva:",
                alert.get("id"),
            )
            continue

        if expires_at <= now:
            alert["active"] = False
            expired.append(
                (
                    alert.get("id", ""),
                    alert.get("title", ""),
                )
            )

    if not expired:
        print("OK: no hay sismos vencidos activos en radar.json.")
        return

    radar["updatedAt"] = now_iso()

    print(f"Desactivando {len(expired)} sismo(s) vencido(s) para DR Ahora:")
    for alert_id, title in expired:
        print("-", alert_id, "|", title)

    old_release, old_version = active()
    old = api("GET", old_version)
    old_files = files(old_version)

    require(
        bool(old_files),
        "El inventario de Hosting está vacío.",
    )
    require(
        RADAR_PATH in old_files,
        "radar.json publicado no existe.",
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

        required = set(
            batch.get("uploadRequiredHashes", [])
        )

        require(
            required <= {digest},
            "Firebase pidió un archivo preservado inesperado.",
        )

        if digest in required and not uploaded:
            upload = session.post(
                batch["uploadUrl"] + "/" + digest,
                data=body,
                headers={
                    "Content-Type":
                        "application/octet-stream"
                },
                timeout=60,
            )
            upload.raise_for_status()
            uploaded = True

    require(
        files(new_version) == expected,
        "El inventario de Hosting no coincide.",
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
            "Otro deploy ocurrió en paralelo; "
            "se abortó antes de publicar."
        ),
    )

    api(
        "POST",
        SITE + "/releases",
        params={"versionName": new_version},
        json={
            "message":
                "DR Ahora: desactivar solo sismos cuyo expiresAt venció"
        },
    )

    require(
        active()[1] == new_version,
        "La nueva versión de Hosting no quedó activa.",
    )

    print("OK: radar.json publicado; alertas tecnológicas intactas.")

    # Refresco silencioso del mismo flujo que ya usa DR Radar.
    # No crea una alerta nueva: solo obliga a releer radar.json/caché.
    fcm = session.post(
        FCM_URL,
        json={
            "message": {
                "topic": "blog_updates",
                "android": {
                    "priority": "high",
                    "collapse_key": "dr_radar_refresh",
                    "restricted_package_name":
                        "com.draccesoriosrd.app",
                },
                "data": {
                    "type": "radar_refresh",
                    "source": "SEISMIC_HOME_EXPIRE",
                },
            }
        },
        timeout=60,
    )

    fcm.raise_for_status()

    print("OK: refresco silencioso de DR Ahora enviado.")
    print(fcm.text)


if __name__ == "__main__":
    main()
