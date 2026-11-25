"""DR Web Push only: correlate catalog aliases without changing Android/NodoPin."""
import json
import math
from early_seismic import _distance, _timestamp, _parse_state, STATE_KEY

HISTORY_KEY = "dr_webpush_seismic_identity_v2"
RETENTION_MS = 72 * 3600 * 1000
MAX_HISTORY = 1000

def identity(alert):
    q = alert.get("earthquake")
    if not isinstance(q, dict):
        return None
    source = {"USGS": "usgs", "EMSC-RTS": "emsc", "EMSC": "emsc"}.get(q.get("source"))
    if source is None:
        return None
    ids = {k: str(q[v]).strip() for k, v in
           (("usgs", "usgsEventId"), ("emsc", "emscEventId")) if q.get(v)}
    if q.get("sourceEventId"):
        ids.setdefault(source, str(q["sourceEventId"]).strip())
    key = str(alert.get("id") or "")
    for k in ("usgs", "emsc"):
        prefix = "earthquake-" + k + "-"
        if key.startswith(prefix) and len(key) > len(prefix):
            ids.setdefault(k, key[len(prefix):])
    if not ids:
        return None
    t = _timestamp(q.get("eventTime"))
    try:
        lat, lon, mag = (float(q[k]) for k in ("latitude", "longitude", "magnitude"))
        if not (all(math.isfinite(v) for v in (lat, lon, mag)) and
                -90 <= lat <= 90 and -180 <= lon <= 180 and 0 < mag < 10):
            raise ValueError("invalid coordinates")
    except (KeyError, TypeError, ValueError, OverflowError):
        lat = lon = mag = None
    return {"source": source, "ids": ids, "eventTime": t,
            "latitude": lat, "longitude": lon, "magnitude": mag}

def direct_identity(event):
    return identity({"id": "earthquake-emsc-" + event["id"],
                     "earthquake": {**event, "source": "EMSC-RTS"}})

def exact(a, b):
    common = set(a["ids"]) & set(b["ids"])
    return bool(common) and all(a["ids"][k] == b["ids"][k] for k in common)

def probable(a, b):
    if a["source"] == b["source"] or set(a["ids"]) & set(b["ids"]):
        return False
    if not a["eventTime"] or not b["eventTime"]:
        return False
    if any(x[k] is None for x in (a, b) for k in ("latitude", "longitude", "magnitude")):
        return False
    # Conservative fallback; explicit catalog aliases take precedence.
    return (abs(a["eventTime"] - b["eventTime"]) <= 30000 and
            abs(a["magnitude"] - b["magnitude"]) <= 0.8 and
            _distance(a["latitude"], a["longitude"], b["latitude"], b["longitude"]) <= 40)

def already_seen(candidate, known):
    if candidate is None:
        return False
    if any(exact(candidate, old) for old in known):
        return True
    # Never collapse an ambiguous match between several distinct earthquakes.
    matches = [old for old in known if probable(candidate, old)]
    return bool(matches) and all(exact(matches[0], old) for old in matches)

def read_history(get_meta, now_ms):
    raw = get_meta(HISTORY_KEY)
    if raw is None:
        return []
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) > MAX_HISTORY:
        raise ValueError("Invalid Web Push seismic history")
    return [r for r in rows if now_ms - int(r["recordedAt"]) <= RETENTION_MS]

def remember(history, item, now_ms, set_meta):
    if item is None:
        return
    # Keep explicit alias information and avoid unlimited repeated entries.
    history[:] = [r for r in history if r["ids"] != item["ids"]]
    history.append({**item, "recordedAt": now_ms})
    del history[:-MAX_HISTORY]
    set_meta(HISTORY_KEY, json.dumps(history, separators=(",", ":")))

def direct_history(get_meta):
    raw = get_meta(STATE_KEY)
    if raw is None:
        return []
    return [r for event in _parse_state(raw) if event.get("sent") is True
            for r in [direct_identity(event)] if r is not None]

def dispatch_feed(alerts, seen, key_fn, sender, get_meta, set_meta, now_ms, baseline=False):
    history = read_history(get_meta, now_ms)
    direct = direct_history(get_meta)
    # Import the legacy seen IDs without notifying historic rows.
    for alert in alerts:
        item = identity(alert)
        if item and (baseline or key_fn(alert) in seen):
            remember(history, item, now_ms, set_meta)
    if baseline:
        return
    fresh = [a for a in alerts if key_fn(a) not in seen]
    # Prefer USGS when both representations first arrive in the same batch.
    fresh.sort(key=lambda a: 0 if (identity(a) or {}).get("source") == "usgs" else 1)
    for alert in fresh:
        item = identity(alert)
        if item and already_seen(item, history + direct):
            print("[webpush] duplicate seismic suppressed key=" + repr(key_fn(alert)), flush=True)
        else:
            sent, removed = sender(alert)
            print(f"[webpush] new alert key={key_fn(alert)!r} sent={sent} removed={removed}", flush=True)
        if item:
            remember(history, item, now_ms, set_meta)

def skip_direct(event, get_meta, now_ms):
    return already_seen(direct_identity(event), read_history(get_meta, now_ms))
