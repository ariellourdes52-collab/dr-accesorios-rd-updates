"""Opt-in, read-only DR Radar feed -> NodoPin FCM fast relay.

Uses the existing Railway monitor's already published alert catalog, NOT
unverified SeedLink waveforms. Never sends a notification for a synthetic quake.
Independent persistent dedup ledger, stored in the Railway /data metadata DB.
No startup fanout; the first enabled poll only establishes a baseline.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import jwt
import requests

PROJECT_ID = "nexlink-3d017"
SOURCE_PROJECT = "dr-accesorios-rd"
SENDER_EMAIL = "nodopin-dr-fcm-sender@dr-accesorios-rd.iam.gserviceaccount.com"
PACKAGE = "org.nodaviva.mesh"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
FCM_ENDPOINT = f"https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send"
STATE_KEY = "nodopin_railway_fcm_known_v1"
MAX_KNOWN = 2000
MAX_EVENTS = 300
ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_cached_token = ""
_cached_until = 0


def timestamp_ms(value: Any) -> int:
    if not value:
        return 0
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            return 0
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return 0


def candidate(alert: dict[str, Any], now_ms: int) -> tuple[str, str] | None:
    """Accept only published, live, confirmed source entries."""
    if not isinstance(alert, dict) or alert.get("active") is not True:
        return None
    event_id = alert.get("id")
    if not isinstance(event_id, str) or not ID.fullmatch(event_id):
        return None
    if event_id.startswith("earthquake-test-"):
        return None
    expires = timestamp_ms(alert.get("expiresAt"))
    if not expires or expires <= now_ms:
        return None
    origin = alert.get("_origin")
    alert_type = str(alert.get("type", "")).lower()
    if origin == "seismic":
        if alert_type != "earthquake":
            return None
        eq = alert.get("earthquake")
        if not isinstance(eq, dict) or str(eq.get("status", "")).lower() == "test":
            return None
        event_time = eq.get("eventTime")
        try:
            event_age = now_ms - int(event_time)
        except (ValueError, TypeError):
            return None
        if event_age < -300_000 or event_age > 3_600_000:
            return None
        return ("seismic", event_id)
    if origin == "main":
        if alert_type in {"earthquake", "seismic", "sismo"}:
            return None  # Only send seismic from the dedicated feed.
        return ("radar_general", event_id)
    return None


def _get_access_token() -> str:
    global _cached_token, _cached_until
    now = int(time.time())
    if _cached_token and now < _cached_until:
        return _cached_token

    raw = os.getenv("NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT", "")
    if not raw:
        raise RuntimeError("FCM NodoPin deshabilitado: falta la credencial Railway")
    try:
        cred = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("FCM NodoPin: JSON de cuenta de servicio no válido") from exc
    if (not isinstance(cred, dict) or cred.get("type") != "service_account"
        or cred.get("project_id") != SOURCE_PROJECT
        or cred.get("client_email") != SENDER_EMAIL
        or not isinstance(cred.get("private_key"), str)):
        raise RuntimeError("FCM NodoPin: identidad de cuenta emisora incorrecta")

    assertion = jwt.encode(
        {
            "iss": SENDER_EMAIL,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": TOKEN_ENDPOINT,
            "iat": now,
            "exp": now + 1800,
        }, cred["private_key"], algorithm="RS256"
    )
    r = requests.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }, timeout=12
    )
    if r.status_code != 200:
        raise RuntimeError(f"FCM NodoPin OAuth HTTP {r.status_code}")
    obj = r.json()
    token = obj.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("FCM NodoPin: OAuth no devolvió access_token")
    _cached_token = token
    _cached_until = now + min(1500, max(1, int(obj.get("expires_in", 1800)) - 120))
    return token


def send_hint(category: str, event_id: str) -> None:
    """Data-only FCM; the Android receiver revalidates the public live feed."""
    token = _get_access_token()
    topic = {
        "seismic": "nodopin_dr_seismic_v1",
        "radar_general": "nodopin_dr_radar_general_v1",
    }[category]
    message = {
        "message": {
            "topic": topic,
            "data": {
                "kind": "nodopin_dr_public_v1",
                "category": category,
                "id": event_id,
            },
            "android": {
                "priority": "high",
                "ttl": "300s",
                "restricted_package_name": PACKAGE,
            },
        }
    }
    res = requests.post(
        FCM_ENDPOINT, json=message,
        headers={"Authorization": "Bearer " + token},
        timeout=12,
    )
    if res.status_code != 200 or not isinstance(res.json().get("name"), str):
        raise RuntimeError(f"FCM NodoPin send HTTP {res.status_code}")


def relay_once(
    alerts: list[dict[str, Any]],
    get_meta: Callable[[str], str | None],
    set_meta: Callable[[str, str], None],
    sender: Callable[[str, str], None] = send_hint,
    now_ms: int | None = None,
) -> dict[str, int | bool]:
    if os.getenv("NODOPIN_RAILWAY_FCM_ENABLED", "").lower() != "true":
        return {"enabled": False, "accepted": 0, "baseline": False}
    if not os.getenv("NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT", ""):
        raise RuntimeError("FCM NodoPin Railway habilitado sin credencial; no se envió nada")
    if len(alerts) > MAX_EVENTS:
        raise RuntimeError("Catálogo Radar inesperadamente grande; sin envío")

    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    keys = []
    for alert in alerts:
        ok = candidate(alert, now_ms)
        if ok is not None:
            keys.append(f"{ok[0]}:{ok[1]}")
    if len(keys) != len(set(keys)):
        raise RuntimeError("IDs duplicados en el feed; sin envío")

    raw_seen = get_meta(STATE_KEY)
    if raw_seen is None:
        set_meta(STATE_KEY, json.dumps(keys))
        print(f"[nodopin-railway-fcm] baseline {len(keys)} keys, no push", flush=True)
        return {"enabled": True, "accepted": 0, "baseline": True}

    try:
        parsed = json.loads(raw_seen)
        if not isinstance(parsed, list) or any(not isinstance(v, str) for v in parsed):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise RuntimeError("FCM NodoPin ledger inválido; cancelando para evitar duplicados") from exc
    known = set(parsed)
    sent = 0
    for key in keys:
        if key in known:
            continue
        category, event_id = key.split(":", 1)
        sender(category, event_id)  # failure propagates; not marked as delivered
        known.add(key)
        parsed.append(key)
        set_meta(STATE_KEY, json.dumps(parsed[-MAX_KNOWN:]))
        sent += 1
    if sent:
        print(f"[nodopin-railway-fcm] Firebase accepted {sent} new published alert(s)", flush=True)
    return {"enabled": True, "accepted": sent, "baseline": False}
