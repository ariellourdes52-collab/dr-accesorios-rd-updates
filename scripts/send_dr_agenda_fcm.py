#!/usr/bin/env python3
"""Send ONE DR Agenda FCM data message after secondary Hosting confirms publication.

Never called in audit mode. Dedicated topic; no blog/radar/OTA destinations.
No notification payload: Android's AgendaPushWorker validates catalog before alert.
"""
from __future__ import annotations

import json
import os
import re
import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

TOPIC = "dr_agenda_events_v1"
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{2,95}$")
PROJECT = "dr-accesorios-rd"


def build_message(event: dict, revision: int, *, test_token: str = "") -> dict:
    event_id = event.get("id")
    if (not isinstance(event_id, str) or not ID.fullmatch(event_id) or
            event.get("status") not in ("confirmed", "postponed") or
            not isinstance(revision, int) or revision < 1):
        raise ValueError("Evento Agenda o revision no verificados")
    target = {"token": test_token} if test_token else {"topic": TOPIC}
    return {"message": {
        **target,
        "data": {
            "type": "agenda_event",
            "kind": "published",
            "event_id": event_id,
            "revision": str(revision),
        },
        "android": {"priority": "HIGH", "ttl": "86400s"}
    }}


def send_confirmed(event: dict, revision: int, *, test_token: str = "") -> str:
    if os.environ.get("DR_AGENDA_FCM_ENABLED") != "true":
        return "disabled"
    info = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    if info.get("project_id") != PROJECT:
        raise ValueError("Firebase project mismatch")
    if test_token and len(test_token) > 4096:
        raise ValueError("Invalid test token")
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    credentials.refresh(Request())
    response = requests.post(
        f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send",
        headers={"Authorization": "Bearer " + credentials.token,
                 "Content-Type": "application/json"},
        json=build_message(event, revision, test_token=test_token),
        timeout=20,
    )
    response.raise_for_status()
    return "accepted" if response.json().get("name", "").startswith("projects/") else "unknown"


if __name__ == "__main__":
    raise SystemExit("Run only after verified secondary Hosting release.")
