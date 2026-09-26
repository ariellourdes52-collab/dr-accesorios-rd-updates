#!/usr/bin/env python3
"""Fail closed unless the two legacy Firebase Composer campaigns are scheduled.

This gate protects the 1.8 release. Versions 1.6 and 1.7 predate the Firestore
installation registry, so they must receive notification-only campaigns
segmented by exact App version in Firebase Notifications composer.
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
        raise SystemExit(f"LEGACY CAMPAIGN GATE BLOCKED: invalid timestamp {value!r}") from exc


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

    campaigns = data.get("legacyCampaigns")
    require(isinstance(campaigns, list) and len(campaigns) == 2, "exactly two legacy campaigns are required")

    seen_sources: set[str] = set()

    for campaign in campaigns:
        source = campaign.get("sourceVersion")
        require(source in EXPECTED_SOURCES, f"unexpected source version: {source!r}")
        require(source not in seen_sources, f"duplicate campaign for version {source}")
        seen_sources.add(source)

        segment = campaign.get("segment") or {}
        require(segment.get("field") == "App version", f"{source}: segment field must be App version")
        require(segment.get("operator") == "equals", f"{source}: segment operator must be equals")
        require(segment.get("value") == source, f"{source}: segment must target only app version {source}")

        notification = campaign.get("notification") or {}
        require(notification.get("title") == EXPECTED_TITLE, f"{source}: unexpected notification title")
        require(notification.get("body") == EXPECTED_BODY, f"{source}: unexpected notification body")

        custom_data = campaign.get("customData")
        require(custom_data == {}, f"{source}: custom data must be empty for notification-only legacy delivery")

        delivery = campaign.get("delivery") or {}
        require(delivery.get("ttlHours") == 24, f"{source}: TTL must be 24 hours")
        require(delivery.get("sound") is True, f"{source}: sound must be enabled")

        scheduled_at = parse_utc(delivery.get("scheduledUtc", ""))
        delay = (scheduled_at - release_at).total_seconds()
        require(delay >= MIN_DELAY_SECONDS, f"{source}: campaign must be at least 15 minutes after release")
        require(delay <= MAX_DELAY_SECONDS, f"{source}: campaign must be within 2 hours after release")

        require(
            delivery.get("timezone") == "America/Santo_Domingo",
            f"{source}: timezone must be America/Santo_Domingo",
        )

        campaign_name = str(campaign.get("firebaseCampaignName") or "").strip()
        require(campaign_name, f"{source}: record the Firebase campaign name after scheduling it")

        require(
            campaign.get("confirmedScheduled") is True,
            f"{source}: confirmedScheduled must be true only after the Firebase campaign is scheduled",
        )

    require(seen_sources == EXPECTED_SOURCES, "campaigns must cover exactly versions 1.6 and 1.7")

    print("Legacy campaign gate OK.")
    print("Target release: 1.8 (code 9)")
    print("Protected source versions: 1.6, 1.7")
    print("Delivery mode: notification-only, exact App version segments")
    print("Scheduled delivery: 2026-09-30 20:30 America/Santo_Domingo")


if __name__ == "__main__":
    main()
