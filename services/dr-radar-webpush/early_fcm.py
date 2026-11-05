"""Separate, opt-in FCM notification for published EMSC preliminary earthquakes.

Only previously validated events from early_seismic.fetch_emsc are passed here.
This module never edits the USGS catalog, Android clients, or the official
NodoPin data-only topic relay. No historical broadcasts on first use.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import requests

from early_seismic import matches_usgs

STATE_KEY = "nodopin_railway_emsc_preliminary_fcm_v1"
MAX_KNOWN = 2000
MAX_EVENTS = 80
MAX_AGE_MS = 40 * 60 * 1000
EVENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
TOPIC = "nodopin_dr_seismic_v1"
PACKAGE = "org.nodaviva.mesh"
FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/nexlink-3d017/messages:send"


def build_message(event: dict[str, Any]) -> dict[str, Any]:
    """An Android background-visible notification, clearly NOT USGS reviewed.

    Existing Android versions cannot be assumed to render this in foreground.
    We therefore don't label the payload as an already-published USGS feed item.
    """
    event_id = str(event["id"])
    if not EVENT_ID.fullmatch(event_id):
        raise ValueError("Invalid EMSC event identifier")
    magnitude = float(event["magnitude"])
    place = str(event["place"])
    if not (0 < magnitude < 10 and "dominican republic" in place.casefold()):
        raise ValueError("Invalid EMSC preliminary details")
    mag = f"{magnitude:.1f}"
    title = "🚨 DR Radar · SISMO PRELIMINAR · M" + mag
    body = ("EMSC reporta M" + mag + " en " + place +
            ". Información preliminar; USGS pendiente. No es predicción.")[:200]
    return {"message": {
        "topic": TOPIC,
        "data": {
            "title": title,
            "body": body,
            "kind": "nodopin_dr_emsc_preliminary_v1",
            "category": "seismic_preliminary",
            "id": "earthquake-emsc-" + event_id,
            "source": "EMSC-RTS",
            "nodopin_open_centro_dr": "true",
            "eventTimeMs": str(int(event["eventTime"])),
        },
        "android": {
            "priority": "high",
            "ttl": "300s",
            "restricted_package_name": PACKAGE,
        },
    }}


def send_preliminary(event: dict[str, Any]) -> None:
    """FCM topic send; no secrets logged and no mark-as-sent on failure."""
    from nodopin_fcm_bridge import _get_access_token
    token = _get_access_token()
    response = requests.post(
        FCM_ENDPOINT,
        json=build_message(event),
        headers={"Authorization": "Bearer " + token},
        timeout=12,
    )
    if response.status_code != 200:
        raise RuntimeError("NodoPin EMSC FCM HTTP " + str(response.status_code))
    result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get("name"), str):
        raise RuntimeError("NodoPin EMSC FCM missing accepted message name")


def _checked_events(events: list[dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("EMSC FCM catalogue invalid")
    ids = set()
    for item in events:
        if not isinstance(item, dict) or not EVENT_ID.fullmatch(str(item.get("id") or "")):
            raise ValueError("EMSC FCM event identifier invalid")
        if item["id"] in ids:
            raise ValueError("EMSC FCM duplicated event identifier")
        ids.add(item["id"])
        event_ms = item.get("eventTime")
        if not isinstance(event_ms, (int, float)) or not -300000 <= now_ms - event_ms <= MAX_AGE_MS:
            raise ValueError("EMSC FCM stale event")
        build_message(item)  # Validates the public preliminary fields.
    return events


def relay_preliminary(
    events: list[dict[str, Any]],
    official_alerts: list[dict[str, Any]],
    get_meta: Callable[[str], str | None],
    set_meta: Callable[[str, str], None],
    sender: Callable[[dict[str, Any]], None] = send_preliminary,
    now_ms: int | None = None,
) -> dict[str, int | bool]:
    """Independent persisted dedup for Android, compatible with Web Push loop."""
    if os.getenv("NODOPIN_EMSC_PRELIMINARY_FCM_ENABLED", "").lower() != "true":
        return {"enabled": False, "baseline": False, "accepted": 0}
    if os.getenv("NODOPIN_RAILWAY_FCM_ENABLED", "").lower() != "true":
        return {"enabled": False, "baseline": False, "accepted": 0}
    if not os.getenv("NODOPIN_RAILWAY_FCM_SERVICE_ACCOUNT", ""):
        raise RuntimeError("NodoPin EMSC FCM enabled without existing sender credentials")
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    rows = _checked_events(events, now_ms)
    raw = get_meta(STATE_KEY)
    if raw is None:
        set_meta(STATE_KEY, json.dumps([x["id"] for x in rows]))
        print("[nodopin-emsc-fcm] safe baseline initialized; 0 FCM", flush=True)
        return {"enabled": True, "baseline": True, "accepted": 0}
    try:
        ids = json.loads(raw)
        if not isinstance(ids, list) or len(ids) > MAX_KNOWN or any(
            not isinstance(x, str) or not EVENT_ID.fullmatch(x) for x in ids
        ) or len(set(ids)) != len(ids):
            raise ValueError("invalid ledger")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("NodoPin EMSC FCM invalid ledger; no notification") from exc

    seen = set(ids)
    sent = 0
    for item in rows:
        event_id = item["id"]
        if event_id in seen:
            continue
        if any(matches_usgs(item, x) for x in official_alerts):
            # Prevent a late PRELIMINARY after USGS; same one-event ledger.
            seen.add(event_id)
            ids.append(event_id)
        else:
            sender(item)   # Raises on FCM failure; no mark-as-sent.
            seen.add(event_id)
            ids.append(event_id)
            sent += 1
            print(f"[nodopin-emsc-fcm] preliminary accepted id={event_id!r}", flush=True)
        set_meta(STATE_KEY, json.dumps(ids[-MAX_KNOWN:]))
    return {"enabled": True, "baseline": False, "accepted": sent}
