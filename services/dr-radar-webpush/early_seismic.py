"""Opt-in EMSC preliminary Web Push for the existing DR Radar Railway service.

Only published EMSC-RTS earthquake catalogue records qualify. Raw SeedLink
triggers never generate notifications. USGS remains the official follow-up.
This module does not mutate Firebase Hosting, Android FCM or subscriptions.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

EMSC_URL = "https://www.seismicportal.eu/fdsnws/event/1/query"
STATE_KEY = "dr_radar_emsc_preliminary_v1"
CENTER = (18.7357, -70.1627)
RULES = ((250, 4.0), (500, 4.5), (900, 5.5))
MAX_AGE_MS = 40 * 60 * 1000
MAX_EVENTS = 80
MAX_LEDGER = 400
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _timestamp(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        if isinstance(value, (float, int)):
            return int(value) if value > 1e12 else int(value * 1000)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000) if dt.utcoffset() is not None else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _distance(a: float, b: float, c: float, d: float) -> float:
    phi1, phi2 = math.radians(a), math.radians(c)
    delta_phi, delta_lon = math.radians(c - a), math.radians(d - b)
    x = (math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2)
         * math.sin(delta_lon / 2) ** 2)
    return 2 * 6371.0088 * math.atan2(math.sqrt(x), math.sqrt(max(0, 1 - x)))


def normalize_feature(feature: Any, now_ms: int) -> dict[str, Any] | None:
    """Fail closed; respect existing DR text and magnitude/distance gates."""
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        return None
    p = feature.get("properties")
    if not isinstance(p, dict) or p.get("source_catalog") != "EMSC-RTS":
        return None
    if str(p.get("evtype") or "").lower() not in {"ke", "earthquake"}:
        return None
    event_id = str(p.get("unid") or feature.get("id") or "").strip()
    if not ID_RE.fullmatch(event_id):
        return None
    place = str(p.get("flynn_region") or "").strip()
    if "dominican republic" not in place.casefold():
        return None
    geometry = feature.get("geometry")
    coordinates = geometry.get("coordinates", []) if isinstance(geometry, dict) else []
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    try:
        lon, lat, magnitude = float(coordinates[0]), float(coordinates[1]), float(p["mag"])
    except (TypeError, ValueError, KeyError):
        return None
    if not (all(math.isfinite(x) for x in (lon, lat, magnitude))
            and -180 <= lon <= 180 and -90 <= lat <= 90 and 0 < magnitude < 10):
        return None
    event_ms = _timestamp(p.get("time"))
    if not event_ms or now_ms - event_ms < -300_000 or now_ms - event_ms > MAX_AGE_MS:
        return None
    distance = _distance(*CENTER, lat, lon)
    minimum = next((mag for radius, mag in RULES if distance <= radius), None)
    if minimum is None or magnitude < minimum:
        return None
    return {"id": event_id, "eventTime": event_ms, "latitude": lat,
            "longitude": lon, "magnitude": magnitude, "place": place}


def fetch_emsc(session: Any, now_ms: int) -> list[dict[str, Any]]:
    start = datetime.fromtimestamp((now_ms - MAX_AGE_MS) / 1000, timezone.utc)
    result = session.get(
        EMSC_URL,
        params={"format": "json", "nodata": "204", "catalog": "EMSC-RTS",
                "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                "minmagnitude": "4.0", "minlatitude": "17", "maxlatitude": "21",
                "minlongitude": "-73", "maxlongitude": "-67", "limit": str(MAX_EVENTS)},
        headers={"Accept": "application/json", "Cache-Control": "no-cache"},
        timeout=(4, 8), allow_redirects=False,
    )
    if result.status_code == 204:
        return []
    result.raise_for_status()
    if len(result.content) > 512_000:
        raise ValueError("EMSC response too large")
    data = result.json()
    if not isinstance(data, dict) or data.get("type") not in ("FeatureCollection", "Feature"):
        raise ValueError("Unexpected EMSC payload")
    features = data.get("features") if data.get("type") == "FeatureCollection" else [data]
    if not isinstance(features, list) or len(features) > MAX_EVENTS:
        raise ValueError("EMSC event list invalid")
    accepted = [normalize_feature(f, now_ms) for f in features]
    accepted = [x for x in accepted if x]
    if len({x["id"] for x in accepted}) != len(accepted):
        raise ValueError("Duplicated EMSC catalogue ID")
    return accepted


def matches_usgs(early: dict[str, Any], official: dict[str, Any]) -> bool:
    quake = official.get("earthquake") if isinstance(official, dict) else None
    if not isinstance(quake, dict) or quake.get("source") != "USGS":
        return False
    try:
        event_time = float(quake["eventTime"])
        latitude, longitude = float(quake["latitude"]), float(quake["longitude"])
        magnitude = float(quake["magnitude"])
        if not all(math.isfinite(x) for x in (event_time, latitude, longitude, magnitude)):
            return False
        return (abs(early["eventTime"] - event_time) <= 90_000 and
                abs(early["magnitude"] - magnitude) <= 1.2 and
                _distance(early["latitude"], early["longitude"], latitude, longitude) <= 80)
    except (KeyError, TypeError, ValueError):
        return False


def _parse_state(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("version") != 1 or
            not isinstance(data.get("events"), list)):
        raise ValueError("EMSC ledger invalid")
    events = data["events"]
    if len(events) > MAX_LEDGER or any(not isinstance(x, dict) or
                                      not isinstance(x.get("id"), str) for x in events):
        raise ValueError("EMSC ledger entries invalid")
    if len({x["id"] for x in events}) != len(events):
        raise ValueError("Duplicated EMSC ledger ID")
    return events


def _save_state(set_meta: Callable[[str, str], None], entries: list[dict[str, Any]]) -> None:
    set_meta(STATE_KEY, json.dumps({"version": 1, "events": entries[-MAX_LEDGER:]},
                                    separators=(",", ":")))


def preliminary_payload(event: dict[str, Any]) -> dict[str, str]:
    magnitude = f'{event["magnitude"]:.1f}'
    return {
        "title": "🚨 DR Radar · SISMO PRELIMINAR · M" + magnitude,
        "body": ("EMSC reportó M" + magnitude + " en " + event["place"] +
                 ". Datos preliminares sujetos a revisión; no es una predicción.")[:220],
        "url": EMSC_URL + "?format=json&eventid=" + event["id"],
        "tag": "dr-seismic-prelim-" + event["id"],
        "category": "earthquake",
    }


def poll_preliminary(session: Any, now_ms: int, official_alerts: list[dict[str, Any]],
                     get_meta: Callable[[str], str | None],
                     set_meta: Callable[[str, str], None],
                     sender: Callable[[dict[str, str]], tuple[int, int]],
                     events: list[dict[str, Any]] | None = None) -> dict[str, int | bool]:
    """Send only new EMSC records, with an initial non-notifying baseline."""
    events = fetch_emsc(session, now_ms) if events is None else events
    raw = get_meta(STATE_KEY)
    if raw is None:
        _save_state(set_meta, [{**x, "sent": False} for x in events])
        return {"baseline": True, "sent": 0}
    entries = _parse_state(raw)
    seen = {x["id"] for x in entries}
    count = 0
    for event in events:
        if event["id"] in seen:
            continue
        if any(matches_usgs(event, x) for x in official_alerts):
            entries.append({**event, "sent": False})
        else:
            delivered, removed = sender(preliminary_payload(event))
            entries.append({**event, "sent": True})
            print(f"[webpush] EMSC preliminary id={event['id']!r} sent={delivered} removed={removed}", flush=True)
            count += 1
        seen.add(event["id"])
        _save_state(set_meta, entries)
    return {"baseline": False, "sent": count}


def previously_preliminary(official: dict[str, Any], raw: str | None) -> bool:
    if raw is None:
        return False
    for entry in _parse_state(raw):
        if entry.get("sent") is True and matches_usgs(entry, official):
            return True
    return False
