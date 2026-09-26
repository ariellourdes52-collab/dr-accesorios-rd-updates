#!/usr/bin/env python3
"""Fail closed unless the combined Firebase legacy campaign is scheduled.

Release 1.8 is protected by one notification-only Firebase Notifications
Composer campaign targeted to exactly App versions 1.6 and 1.7.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_TARGET_CODE = 9
EXPECTED_TARGET_NAME = "1.8"
EXPECTED_SOURCES = {"1.6", "1.7"}
EXPECTED_TITLE = "Nueva versión de DR Accesorios RD"
EXPECTED_BODY = "La versión 1.8 ya está disponible. Toca para actualizar."
MIN_DELAY_SECONDS = 15 * 60
MAX_DELAY_SECONDS = 2 * 60 * 60


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("LEGACY CAMPAIGN GATE BLOCKED: " + message)


def parse_utc(value: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), "UTC timestamps must end in Z")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise SystemExit(
            f"LEGACY CAMPAIGN GATE BLOCKED: invalid timestamp {value!r}"
        ) from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "config",
        nargs="?",
        default="config/legacy-update-campaigns-1.8.json",
    )
    args = parser.parse_args()

    path = Path(args.config)
    require(path.is_file(), f"missing config: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    target = data.get("targetRelease") or {}
    require(target.get("versionCode") == EXPECTED_TARGET_CODE, "target versionCode must be 9")
    require(target.get("versionName") == EXPECTED_TARGET_NAME, "target versionName must be 1.8")

    release_at = parse_utc(target.get("scheduledReleaseUtc", ""))

    campaign = data.get("legacyCampaign")
    require(isinstance(campaign, dict), "legacyCampaign object is required")

    source_versions = campaign.get("sourceVersions")
    require(
        isinstance(source_versions, list)
        and len(source_versions) == 2
        and set(source_versions) == EXPECTED_SOURCES,
        "sourceVersions must contain exactly 1.6 and 1.7",
    )

    segment = campaign.get("segment") or {}
    require(segment.get("field") == "App version", "segment field must be App version")
    require(segment.get("operator") == "equals", "segment operator must be equals")

    values = segment.get("values")
    require(
        isinstance(values, list)
        and len(values) == 2
        and set(values) == EXPECTED_SOURCES,
        "segment values must contain exactly 1.6 and 1.7",
    )

    notification = campaign.get("notification") or {}
    require(notification.get("title") == EXPECTED_TITLE, "unexpected notification title")
    require(notification.get("body") == EXPECTED_BODY, "unexpected notification body")

    custom_data = campaign.get("customData")
    require(custom_data == {}, "custom data must be empty for notification-only legacy delivery")

    delivery = campaign.get("delivery") or {}
    require(delivery.get("ttlHours") == 24, "TTL must be 24 hours")
    require(delivery.get("sound") is True, "sound must be enabled")
    require(
        delivery.get("timezone") == "America/Santo_Domingo",
        "timezone must be America/Santo_Domingo",
    )

    scheduled_at = parse_utc(delivery.get("scheduledUtc", ""))
    delay = (scheduled_at - release_at).total_seconds()
    require(delay >= MIN_DELAY_SECONDS, "campaign must be at least 15 minutes after release")
    require(delay <= MAX_DELAY_SECONDS, "campaign must be within 2 hours after release")

    require(
        campaign.get("confirmationSource") == "manual_firebase_console",
        "confirmationSource must be manual_firebase_console after scheduling in Firebase",
    )
    require(
        campaign.get("confirmedScheduled") is True,
        "confirmedScheduled must be true only after the Firebase campaign is scheduled",
    )

    print("Legacy campaign gate OK.")
    print("Target release: 1.8 (code 9)")
    print("Protected source versions: 1.6 and 1.7")
    print("Delivery mode: one notification-only exact-version campaign")
    print("Scheduled delivery: 2026-09-30 20:30 America/Santo_Domingo")


if __name__ == "__main__":
    main()
