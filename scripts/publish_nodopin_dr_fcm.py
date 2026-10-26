#!/usr/bin/env python3
"""Public official DR catalog entries to NodoPin FCM topics only after verification.

Dry-run by default. Never sends chat, location, PIN or private data.
To send: require --send AND NODOPIN_DR_FCM_ENABLED=true AND dedicated
NODOPIN_DR_FCM_SERVICE_ACCOUNT JSON with FCM API Admin on target project.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

PROJECT_ID = "nexlink-3d017"
PACKAGE = "org.nodaviva.mesh"
SOURCES = {
    "radar_general": ("https://dr-accesorios-rd.web.app/radar.json", "nodopin_dr_radar_general_v1"),
    "seismic": ("https://dr-accesorios-rd.web.app/radar-seismic-v231.json", "nodopin_dr_seismic_v1"),
    "agenda": ("https://dr-accesorios-rd-agenda.web.app/agenda/events.json", "nodopin_dr_agenda_v1"),
}
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def parse_date(value):
    try:
        text = str(value or "")
        if not text:
            return None
        obj = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if obj.utcoffset() is None:
            return None
        return obj.timestamp()
    except (ValueError, OverflowError):
        return None


def match_catalog(category, alert_id, content, now=None):
    """Pure function. Returns metadata only after validating the official feed."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if category not in SOURCES or not ID_RE.fullmatch(alert_id):
        raise ValueError("Categoría o ID inválidos")
    if category == "agenda" and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", alert_id):
        raise ValueError("ID de DR Agenda inválido")
    if not isinstance(content, dict) or content.get("schemaVersion", 1) != 1:
        raise ValueError("Esquema JSON inválido")
    entries = content.get("events" if category == "agenda" else "alerts")
    if not isinstance(entries, list):
        raise ValueError("Catálogo inválido")
    found = [x for x in entries if isinstance(x, dict) and x.get("id") == alert_id]
    if len(found) != 1:
        raise ValueError("ID ausente o duplicado en catálogo oficial")
    item = found[0]
    if category == "agenda":
        if item.get("status") not in {"confirmed", "postponed"}:
            raise ValueError("Evento no público")
        start = parse_date(item.get("startAt"))
        end = parse_date(item.get("endAt")) or start
        if start is None or end is None or end < now:
            raise ValueError("Evento caducado o sin fecha")
        ttl = 3600
    else:
        if content.get("enabled") is False or item.get("active") is not True:
            raise ValueError("Alerta inactiva")
        expires = parse_date(item.get("expiresAt"))
        if category == "seismic" and (content.get("enabled") is not True or expires is None):
            raise ValueError("Estado sísmico inválido")
        if expires is not None and expires <= now:
            raise ValueError("Alerta caducada")
        if category == "seismic":
            if item.get("type") != "earthquake" or alert_id.startswith("earthquake-test-"):
                raise ValueError("No es evento sísmico real")
            quake = item.get("earthquake") or {}
            if quake.get("status") == "test" or quake.get("source") != "USGS":
                raise ValueError("Origen sísmico no confiable")
            at = quake.get("eventTime")
            if not isinstance(at, (float, int)) or not -300 <= now - at / 1000.0 <= 2400:
                raise ValueError("Evento sísmico viejo o futuro")
            ttl = 300
        else:
            if item.get("type") in {"earthquake", "seismic", "sismo"}:
                raise ValueError("Evento sísmico en feed general")
            created = parse_date(item.get("createdAt") or item.get("publishedAt"))
            if created is None or not -300 <= now - created <= 86400:
                raise ValueError("Alerta general vieja o con fecha inválida")
            ttl = 900
    return {"category": category, "id": alert_id, "ttl": ttl, "topic": SOURCES[category][1]}


def fetch_live(category):
    url = SOURCES[category][0]
    response = requests.get(
        url,
        timeout=(6, 12),
        allow_redirects=False,
        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
        params={"nodopin_fcm_verify": str(int(datetime.now(timezone.utc).timestamp()))},
    )
    response.raise_for_status()
    if response.status_code != 200 or len(response.content) > 512_000:
        raise ValueError("Feed inaccesible o demasiado grande")
    if not response.headers.get("Content-Type", "").lower().startswith("application/json"):
        raise ValueError("Respuesta no JSON")
    return response.json()


def payload(checked, test_token=None):
    # Data-only: app verifies the live published feed before notifying.
    target = {"token": test_token} if test_token else {"topic": checked["topic"]}
    return {"message": {
        **target,
        "data": {"kind": "nodopin_dr_public_v1", "category": checked["category"], "id": checked["id"]},
        "android": {"priority": "high", "ttl": str(checked["ttl"]) + "s", "restricted_package_name": PACKAGE},
    }}



def ping_payload(token):
    """Diagnostic transport-only FCM; not a DR alert, one device only."""
    if not isinstance(token, str) or not 20 <= len(token) <= 4096:
        raise ValueError("Token FCM de prueba inválido")
    return {"message": {
        "token": token,
        "notification": {
            "title": "PRUEBA FCM · NodoPin",
            "body": "Conexión de prueba correcta. NO es una alerta real de DR Radar ni de DR Agenda."
        },
        "android": {
            "priority": "high", "ttl": "300s",
            "restricted_package_name": PACKAGE,
        },
    }}

def send_ping(token):
    """Exactly one targeted message; never a topic broadcast."""
    raw = os.getenv("NODOPIN_DR_FCM_SERVICE_ACCOUNT", "").strip()
    if not raw:
        raise RuntimeError("Falta NODOPIN_DR_FCM_SERVICE_ACCOUNT")
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    response = AuthorizedSession(credentials).post(
        f"https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send",
        json=ping_payload(token), timeout=20)
    response.raise_for_status()
    return response.json().get("name", "")


def publish(checked, test_token=None):
    raw = os.getenv("NODOPIN_DR_FCM_SERVICE_ACCOUNT", "").strip()
    if not raw:
        raise RuntimeError("Falta NODOPIN_DR_FCM_SERVICE_ACCOUNT")
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError as e:
        raise RuntimeError("Instalar google-auth") from e
    secret = json.loads(raw)
    credentials = service_account.Credentials.from_service_account_info(
        secret, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    session = AuthorizedSession(credentials)
    url = f"https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send"
    response = session.post(url, json=payload(checked, test_token=test_token), timeout=20)
    response.raise_for_status()
    return response.json().get("name", "")


def verify_published_with_retry(category, alert_id, attempts=1, delay=1.5):
    """Bounded read-after-publish retry: Firebase Hosting CDN may lag by seconds.

    This never bypasses the official catalog verification; each attempt
    revalidates the exact public entry before sending FCM.
    """
    last = None
    for attempt in range(max(1, min(attempts, 6))):
        try:
            return match_catalog(category, alert_id, fetch_live(category))
        except (ValueError, requests.RequestException) as error:
            last = error
            if attempt + 1 < attempts:
                time.sleep(delay)
    raise RuntimeError("La alerta aún no aparece válida en el catálogo público; no se envió FCM") from last


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True, choices=sorted(SOURCES))
    parser.add_argument("--id", required=True)
    parser.add_argument("--send", action="store_true", help="Send to topic; requires production permission gate")
    parser.add_argument("--test", action="store_true", help="Send a published alert only to one test device")
    parser.add_argument("--ping", action="store_true", help="Transport smoke-test to one token, clearly labelled TEST")
    args = parser.parse_args(argv)
    if args.ping:
        if args.send or args.test:
            raise RuntimeError("No combinar --ping con otros modos")
        if os.getenv("NODOPIN_DR_FCM_TEST_ENABLED", "").strip().lower() != "true":
            raise RuntimeError("PRUEBA deshabilitada: NODOPIN_DR_FCM_TEST_ENABLED != true")
        token = os.getenv("NODOPIN_DR_FCM_TEST_DEVICE_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Falta el secreto NODOPIN_DR_FCM_TEST_DEVICE_TOKEN")
        print("FCM de diagnóstico (token único, NO alerta real):", send_ping(token))
        return 0
    if not ID_RE.fullmatch(args.id):
        parser.error("ID inválido")
    checked = verify_published_with_retry(args.category, args.id, attempts=5 if (args.send or args.test) else 1)
    if not args.send and not args.test:
        print("DRY RUN: evento público verificado. No se envió FCM.", checked)
        return 0
    if args.send and args.test:
        raise RuntimeError("No combinar --send y --test")
    if args.test:
        if os.getenv("NODOPIN_DR_FCM_TEST_ENABLED", "").strip().lower() != "true":
            raise RuntimeError("TEST deshabilitado")
        token = os.getenv("NODOPIN_DR_FCM_TEST_DEVICE_TOKEN", "").strip()
        if not token or len(token) > 4096:
            raise RuntimeError("Falta token válido de dispositivo TEST")
        print("FCM de PRUEBA (solo token):", publish(checked, test_token=token))
        return 0
    if os.getenv("NODOPIN_DR_FCM_ENABLED", "").strip().lower() != "true":
        raise RuntimeError("ENV NODOPIN_DR_FCM_ENABLED debe ser true; sin envío")
    print("FCM publicado al tópico:", publish(checked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
