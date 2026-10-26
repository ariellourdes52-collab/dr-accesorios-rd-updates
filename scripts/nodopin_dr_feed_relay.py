#!/usr/bin/env python3
"""Fallback FCM relay for DR Radar feeds.

Checks general Radar entries not handled by the GitHub manual publisher and
the independent seismic feed. Cron is best-effort (not earthquake early warning).
A persistent GitHub-tracked ledger prevents redispatch of already-seen IDs.
First execution establishes a baseline without sending old notifications.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from publish_nodopin_dr_fcm import SOURCES, fetch_live, match_catalog, publish

LEDGER = Path(".state/nodopin-dr-fcm-feed-ledger.json")
CATEGORIES = ("radar_general", "seismic")
MAX_IDS = 1000


def eligible_ids(category, feed):
    if not isinstance(feed, dict):
        raise ValueError("Feed inválido")
    alerts = feed.get("alerts")
    if not isinstance(alerts, list):
        raise ValueError("Feed sin lista alerts")
    ids = []
    for alert in alerts:
        if not isinstance(alert, dict) or not isinstance(alert.get("id"), str):
            continue
        if category == "radar_general":
            # This source is already dispatched by manual-publish-radar-alert.yml.
            if alert.get("source") == "TELEGRAM_ADMIN":
                continue
            if str(alert.get("type") or "").lower() in {"earthquake", "seismic", "sismo"}:
                continue
        if category == "seismic":
            if alert.get("type") != "earthquake" or alert["id"].startswith("earthquake-test-"):
                continue
        ids.append(alert["id"])
    if len(ids) > MAX_IDS:
        raise ValueError("Número inesperado de alertas; se cancela")
    if len(set(ids)) != len(ids):
        raise ValueError("IDs duplicados en catálogo")
    return ids


def get_state():
    if not LEDGER.exists():
        return {"schemaVersion": 1, "known": {}}
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("known"), dict):
        raise ValueError("Ledger inválido; cancelando para no duplicar avisos")
    return data


def reconcile(category, feed, state, sender, now=None):
    """Mutates state only after a successful send or a deliberate baseline."""
    ids = eligible_ids(category, feed)
    known = state["known"]
    if category not in known:
        known[category] = ids
        print(f"{category}: baseline seguro, {len(ids)} IDs; 0 notificaciones")
        return 0
    existing = set(known[category])
    count = 0
    for event_id in ids:
        if event_id in existing:
            continue
        # Reject stale, test, inactive and expired events before sending FCM.
        try:
            checked = match_catalog(category, event_id, feed, now)
        except ValueError:
            existing.add(event_id)  # Do not retry invalid or stale alerts.
            continue
        sender(checked)  # Raises on rejection; don't mark as sent.
        existing.add(event_id)
        count += 1
        print(f"{category}: FCM accepted for published ID {event_id}")
    # Preserve last MAX_IDS IDs in catalog order. Never lose the record of
    # currently available items, even if old ones were deactivated.
    ordered = [i for i in known[category] if i in existing] + [i for i in ids if i in existing]
    known[category] = list(dict.fromkeys(ordered))[-MAX_IDS:]
    return count


def main():
    if os.getenv("NODOPIN_DR_FCM_ENABLED", "").lower() != "true":
        print("FCM relay desactivado; sin lectura ni envíos")
        return
    if not os.getenv("NODOPIN_DR_FCM_SERVICE_ACCOUNT", "").strip():
        raise RuntimeError("Falta secreto FCM")
    state = get_state()
    total = 0
    for category in CATEGORIES:
        feed = fetch_live(category)
        total += reconcile(category, feed, state, publish)
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FCM relay aceptado: {total} aviso(s); ejecución de respaldo (no inmediata)")


if __name__ == "__main__":
    main()
