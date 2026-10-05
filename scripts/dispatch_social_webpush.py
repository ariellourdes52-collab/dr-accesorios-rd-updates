#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2 import service_account

BACKEND = "https://dr-radar-webpush-production.up.railway.app"
SECRET = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
OIDC_TOKEN = os.environ.get("GITHUB_OIDC_TOKEN", "").strip()


def normalize(value: Any):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def main() -> None:
    if not SECRET:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT no configurado.")
    if not OIDC_TOKEN:
        raise RuntimeError("GITHUB_OIDC_TOKEN no configurado.")

    info = json.loads(SECRET)
    credentials = service_account.Credentials.from_service_account_info(info)
    client = firestore.Client(project=info["project_id"], credentials=credentials)

    headers = {
        "Authorization": "Bearer " + OIDC_TOKEN,
        "Content-Type": "application/json",
    }

    uids_response = requests.get(
        BACKEND + "/social-uids",
        headers=headers,
        timeout=30,
    )
    uids_response.raise_for_status()
    uids = list(uids_response.json().get("uids") or [])

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=20)
    events = []

    for uid in uids:
        query = (
            client.collection("userNotifications")
            .document(uid)
            .collection("events")
            .where(filter=FieldFilter("createdAt", ">=", cutoff))
            .order_by("createdAt")
            .limit(100)
        )
        for doc in query.stream():
            data = doc.to_dict() or {}
            event_type = str(data.get("type") or "").upper()
            if "UNLIKE" in event_type:
                continue

            events.append(
                {
                    "recipientUid": uid,
                    "eventId": doc.id,
                    "eventKey": f"{uid}:{doc.id}",
                    "type": data.get("type"),
                    "actorUid": data.get("actorUid"),
                    "actorName": data.get("actorName") or data.get("actorUserName"),
                    "articleUrl": data.get("articleUrl"),
                    "articleTitle": data.get("articleTitle"),
                    "commentId": data.get("commentId"),
                    "replyId": data.get("replyId"),
                    "body": data.get("body"),
                    "createdAt": normalize(data.get("createdAt")),
                }
            )

    response = requests.post(
        BACKEND + "/dispatch-social",
        headers=headers,
        json={"events": events},
        timeout=45,
    )
    response.raise_for_status()
    result = response.json()
    print(
        "social_webpush",
        json.dumps(
            {
                "linkedUsers": len(uids),
                "eventsRead": len(events),
                "baseline": result.get("baseline"),
                "processed": result.get("processed"),
                "sent": result.get("sent"),
                "removed": result.get("removed"),
            },
            ensure_ascii=False,
        ),
    )


if __name__ == "__main__":
    main()
