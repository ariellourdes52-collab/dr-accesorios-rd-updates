"""Opt-in EMSC preliminary notifications for DR Accesorios RD Android only.

Separate from DR Radar Web Push, NodoPin FCM and the existing Google Apps
Script USGS topic. Uses already published EMSC-RTS catalogue earthquakes,
not uncorroborated SeedLink station triggers. Never backfills historic events.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import requests
import jwt
from early_seismic import fetch_emsc, matches_usgs

TOPIC = "radar_seismic_v231"  # Existing DR Accesorios RD Android seismic topic
PROJECT_ID = "dr-accesorios-rd"
FCM_URL = f"https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send"
STATE_KEY = "dr_android_emsc_prelim_fcm_v1"
MAX_LEDGER = 800
MAX_AGE_MS = 40 * 60 * 1000
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MODE_ENV = "DR_ANDROID_EMSC_FCM_MODE"
CREDENTIAL_ENV = "DR_ANDROID_EMSC_SERVICE_ACCOUNT"
SENDER_EMAIL = "dr-radar-preliminar@dr-accesorios-rd.iam.gserviceaccount.com"
OAUTH_ENDPOINT = "https://oauth2.googleapis.com/token"
_cached_token = ""
_cached_until = 0


def mode() -> str:
    value = os.getenv(MODE_ENV, "off").strip().lower()
    if value not in ("off", "validate", "live"):
        raise ValueError("Invalid DR Android EMSC FCM mode")
    return value


def payload(event: dict[str, Any], validate_only: bool = False) -> dict[str, Any]:
    event_id = event.get("id")
    if not isinstance(event_id, str) or not ID_RE.fullmatch(event_id):
        raise ValueError("EMSC event ID invalid")
    mag = float(event["magnitude"])
    event_time_ms = int(event["eventTime"])
    place = str(event["place"]).strip()
    if not (0 < mag < 10 and "dominican republic" in place.lower() and
            isinstance(event_time_ms, int) and event_time_ms > 0):
        raise ValueError("Invalid EMSC preliminary event")
    mag_text = f"{mag:.1f}"
    return {
        "validate_only": bool(validate_only),
        "message": {
            "topic": TOPIC,
            "notification": {
                "title": "🚨 DR Accesorios RD · SISMO PRELIMINAR · M" + mag_text,
                "body": ("EMSC reporta M" + mag_text + " en " + place +
                         ". Información preliminar, sujeta a cambios. No es una predicción.")[:200],
            },
            "data": {
                "source": "EMSC-RTS",
                "kind": "dr_radar_emsc_preliminary_v1",
                "category": "seismic_preliminary",
                "id": "earthquake-emsc-" + event_id,
                "eventTimeMs": str(event_time_ms),
            },
            "android": {
                "priority": "high",
                "ttl": "300s",
                "notification": {"tag": "dr-emsc-prelim-" + event_id},
            },
        },
    }


def _get_access_token() -> str:
    global _cached_token, _cached_until
    now = int(time.time())
    if _cached_token and now < _cached_until:
        return _cached_token
    raw = os.getenv(CREDENTIAL_ENV, "")
    if not raw:
        raise RuntimeError("DR Android EMSC dedicated service account not configured")
    try:
        cred = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("DR Android EMSC invalid dedicated service-account JSON") from exc
    if (not isinstance(cred, dict) or cred.get("type") != "service_account"
            or cred.get("project_id") != PROJECT_ID
            or cred.get("client_email") != SENDER_EMAIL
            or not isinstance(cred.get("private_key"), str)):
        raise RuntimeError("DR Android EMSC sender identity mismatch")
    claim = jwt.encode({"iss": SENDER_EMAIL,
                        "scope": "https://www.googleapis.com/auth/firebase.messaging",
                        "aud": OAUTH_ENDPOINT, "iat": now, "exp": now + 1800},
                       cred["private_key"], algorithm="RS256")
    response = requests.post(OAUTH_ENDPOINT,
        data={"grant_type":"urn:ietf:params:oauth:grant-type:jwt-bearer",
              "assertion":claim}, timeout=12)
    if response.status_code != 200:
        raise RuntimeError("DR Android EMSC OAuth HTTP " + str(response.status_code))
    result = response.json()
    token = result.get("access_token") if isinstance(result, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("DR Android EMSC access token missing")
    _cached_token = token
    _cached_until = now + min(1500,max(1,int(result.get("expires_in",1800))-120))
    return token


def _fcm_request(body: dict[str, Any]) -> None:
    # Dedicated DR Accesorios RD identity only; never read NodoPin secrets.
    token = _get_access_token()
    response = requests.post(
        FCM_URL,
        json=body,
        headers={"Authorization": "Bearer " + token},
        timeout=14,
    )
    if response.status_code != 200:
        raise RuntimeError("DR Android EMSC FCM HTTP " + str(response.status_code))
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        raise RuntimeError("DR Android EMSC FCM missing response name")


def validate_transport() -> None:
    """FCM validate_only is a dry-run API call, NEVER a user notification."""
    synthetic = {
        "id": "dr-test-no-delivery", "magnitude": 4.0, "eventTime": 1,
        "place": "DOMINICAN REPUBLIC",
    }
    _fcm_request(payload(synthetic, validate_only=True))
    print("[dr-android-emsc] FCM validate_only OK; 0 notifications delivered", flush=True)


def send(event: dict[str, Any]) -> None:
    _fcm_request(payload(event))


def _parse_ledger(raw: str) -> list[str]:
    value = json.loads(raw)
    if (not isinstance(value, list) or len(value) > MAX_LEDGER
            or any(not isinstance(i, str) or not ID_RE.fullmatch(i) for i in value)
            or len(set(value)) != len(value)):
        raise ValueError("DR Android EMSC invalid ledger")
    return value


def relay_once(
    session: Any,
    get_meta: Callable[[str], str | None],
    set_meta: Callable[[str, str], None],
    official_alerts: list[dict[str, Any]],
    sender: Callable[[dict[str, Any]], None] = send,
    now_ms: int | None = None,
) -> dict[str, Any]:
    current_mode = mode()
    if current_mode == "off":
        return {"mode": "off", "sent": 0}
    if not os.getenv(CREDENTIAL_ENV, "").strip():
        raise RuntimeError("DR Android EMSC requires its own service-account credential")
    if current_mode == "validate":
        # No repeated FCM validation broadcasts; no message is delivered.
        # On permission errors retry at most every 15 minutes, never every poll.
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        last_ok = get_meta("dr_android_emsc_validate_ok_v1")
        if last_ok == "true":
            return {"mode": "validate", "sent": 0, "verified": True}
        last_attempt = get_meta("dr_android_emsc_validate_last_attempt_v1")
        if last_attempt is not None and now_ms - int(last_attempt) < 900_000:
            return {"mode": "validate", "sent": 0, "verified": False}
        set_meta("dr_android_emsc_validate_last_attempt_v1", str(now_ms))
        validate_transport()
        set_meta("dr_android_emsc_validate_ok_v1", "true")
        return {"mode": "validate", "sent": 0, "verified": True}
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    events = fetch_emsc(session, now_ms)
    raw = get_meta(STATE_KEY)
    if raw is None:
        set_meta(STATE_KEY, json.dumps([e["id"] for e in events]))
        print("[dr-android-emsc] baseline created; 0 historic notifications", flush=True)
        return {"mode": "live", "sent": 0, "baseline": True}

    seen_ids = _parse_ledger(raw)
    seen = set(seen_ids)
    delivered = 0
    for event in events:
        event_id = event["id"]
        if event_id in seen:
            continue
        if any(matches_usgs(event, official) for official in official_alerts):
            # USGS already announced this quake; prevent late preliminary.
            print("[dr-android-emsc] preliminary skipped; already on USGS", flush=True)
        else:
            sender(event)  # If FCM fails, the event is NOT marked as sent.
            delivered += 1
            print("[dr-android-emsc] FCM accepted preliminary ID=" + event_id, flush=True)
        seen.add(event_id)
        seen_ids.append(event_id)
        set_meta(STATE_KEY, json.dumps(seen_ids[-MAX_LEDGER:]))
    return {"mode": "live", "sent": delivered, "baseline": False}
