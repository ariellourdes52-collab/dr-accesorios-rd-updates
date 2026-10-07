#!/usr/bin/env python3
"""Send version-aware DR Accesorios RD update notifications.

Legacy clients receive notification+data so Android can display the update even
when app code is not running. Clients at/after the background-update fix receive
data-only and keep using their own FCM + WorkManager logic.

Targets come from Firestore /devices and are addressed directly by FID (preferred)
or legacy FCM token. There is intentionally no broad topic fallback: a topic
notification cannot exclude already-fixed/current clients safely.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

PROJECT_ID = "dr-accesorios-rd"
DATABASE_ID = "(default)"
DEVICES_COLLECTION = "devices"
FCM_ENDPOINT = f"https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send"
FIRESTORE_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/"
    f"{DATABASE_ID}/documents/{DEVICES_COLLECTION}"
)
SCOPES = [
    "https://www.googleapis.com/auth/firebase.messaging",
    "https://www.googleapis.com/auth/datastore",
]
DEFAULT_LEGACY_MAX_VERSION_CODE = 14  # Existing v2.2/code 14 predates the fix; fixed builds start at code 15.
DEFAULT_TTL_SECONDS = 86400
MAX_WORKERS = 8
MAX_TRANSIENT_RETRIES = 4


@dataclass(frozen=True)
class Device:
    document_name: str
    installed_version_code: int
    installed_version_name: str
    fid: str
    token: str

    @property
    def target(self) -> tuple[str, str] | None:
        if self.fid:
            return ("fid", self.fid)
        if self.token:
            return ("token", self.token)
        return None


def _field_value(fields: dict[str, Any], name: str) -> Any:
    value = fields.get(name) or {}
    if "integerValue" in value:
        return int(value["integerValue"])
    if "stringValue" in value:
        return value["stringValue"]
    if "timestampValue" in value:
        return value["timestampValue"]
    return None


def parse_device(document: dict[str, Any]) -> Device | None:
    fields = document.get("fields") or {}
    version_code = _field_value(fields, "appVersionCode")
    if not isinstance(version_code, int) or version_code <= 0:
        return None

    return Device(
        document_name=str(document.get("name") or ""),
        installed_version_code=version_code,
        installed_version_name=str(_field_value(fields, "appVersionName") or ""),
        fid=str(_field_value(fields, "fcmInstallationId") or "").strip(),
        token=str(_field_value(fields, "fcmToken") or "").strip(),
    )


def credentials_from_env():
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not raw:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT is missing")
    info = json.loads(raw)
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=SCOPES,
    )
    credentials.refresh(Request())
    return credentials


def auth_headers(credentials) -> dict[str, str]:
    if not credentials.valid or credentials.expired:
        credentials.refresh(Request())
    return {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def load_devices(credentials) -> list[Device]:
    devices: list[Device] = []
    page_token = ""

    while True:
        params = {"pageSize": 300}
        if page_token:
            params["pageToken"] = page_token

        response = requests.get(
            FIRESTORE_BASE,
            headers=auth_headers(credentials),
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        for document in payload.get("documents", []):
            device = parse_device(document)
            if device is not None:
                devices.append(device)

        page_token = payload.get("nextPageToken", "")
        if not page_token:
            break

    return devices


def delete_device_document(credentials, device: Device) -> None:
    if not device.document_name:
        raise RuntimeError("Device document name is missing")

    response = requests.delete(
        "https://firestore.googleapis.com/v1/" + device.document_name,
        headers=auth_headers(credentials),
        timeout=30,
    )

    if response.status_code not in (200, 204, 404):
        response.raise_for_status()


def build_message(
    device: Device,
    target_version_code: int,
    target_version_name: str,
    legacy_max_version_code: int,
    ttl_seconds: int,
) -> tuple[dict[str, Any], str]:
    target = device.target
    if target is None:
        raise ValueError("Device has no FCM registration")

    target_field, target_value = target
    legacy = device.installed_version_code <= legacy_max_version_code

    message: dict[str, Any] = {
        target_field: target_value,
        "android": {
            "priority": "high",
            "ttl": f"{ttl_seconds}s",
            "collapse_key": "dr_app_update",
        },
        "data": {
            "type": "app_update",
            "versionCode": str(target_version_code),
            "versionName": target_version_name,
        },
        "fcm_options": {
            "analytics_label": "app_update",
        },
    }

    if legacy:
        message["notification"] = {
            "title": "Nueva versión de DR Accesorios RD",
            "body": (
                f"La versión {target_version_name} ya está disponible. "
                "Toca para actualizar."
            ),
        }
        message["android"]["notification"] = {
            "tag": "dr_app_update",
            "default_sound": True,
            "default_vibrate_timings": True,
            "notification_priority": "PRIORITY_HIGH",
        }
        mode = "legacy_hybrid"
    else:
        mode = "fixed_data"

    return {"message": message}, mode


def _error_status(response: requests.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") or {}
        status = str(error.get("status") or "")
        message = str(error.get("message") or "")
        return f"{status}: {message}".strip(": ")[:300]
    except Exception:
        return response.text[:300]


def send_one(credentials, payload: dict[str, Any]) -> tuple[str, str]:
    for attempt in range(MAX_TRANSIENT_RETRIES + 1):
        response = requests.post(
            FCM_ENDPOINT,
            headers=auth_headers(credentials),
            json=payload,
            timeout=30,
        )

        if response.ok:
            return ("sent", "")

        if response.status_code in (429, 500, 502, 503, 504):
            if attempt < MAX_TRANSIENT_RETRIES:
                retry_after = response.headers.get("Retry-After", "").strip()
                if retry_after.isdigit():
                    delay = min(int(retry_after), 60)
                else:
                    delay = min((2 ** attempt) + random.random(), 30)
                time.sleep(delay)
                continue
            return ("transient_failed", _error_status(response))

        if response.status_code in (400, 404):
            return ("invalid_registration", _error_status(response))

        return ("fatal", f"HTTP {response.status_code}: {_error_status(response)}")

    return ("fatal", "Unexpected send loop exit")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument(
        "--legacy-max-version-code",
        type=int,
        default=DEFAULT_LEGACY_MAX_VERSION_CODE,
    )
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument(
        "--prune-not-registered",
        action="store_true",
        help=(
            "Delete /devices documents only when FCM confirms the "
            "registration is no longer registered (HTTP 404 / NotRegistered)."
        ),
    )
    args = parser.parse_args()

    if args.version_code <= 0:
        raise SystemExit("version-code must be positive")
    if not (0 <= args.ttl_seconds <= 2419200):
        raise SystemExit("ttl-seconds must be between 0 and 2419200")

    credentials = credentials_from_env()
    all_devices = load_devices(credentials)

    eligible: list[Device] = []
    skipped_no_registration = 0
    skipped_current_or_newer = 0
    versions = collections.Counter()

    for device in all_devices:
        versions[device.installed_version_code] += 1
        if device.installed_version_code >= args.version_code:
            skipped_current_or_newer += 1
            continue
        if device.target is None:
            skipped_no_registration += 1
            continue
        eligible.append(device)

    legacy_count = sum(
        d.installed_version_code <= args.legacy_max_version_code for d in eligible
    )
    fixed_count = len(eligible) - legacy_count

    print("FCM targeting mode: per-installation; no update broadcast topic")
    print("Target version:", args.version_name, f"(code {args.version_code})")
    print("Legacy compatibility cutoff:", args.legacy_max_version_code)
    print("Firestore device records:", len(all_devices))
    print("Eligible installations:", len(eligible))
    print("  legacy notification+data:", legacy_count)
    print("  fixed data-only:", fixed_count)
    print("Skipped current/newer:", skipped_current_or_newer)
    print("Skipped without FCM registration:", skipped_no_registration)
    print("Registered versions:", dict(sorted(versions.items())))

    if args.preflight:
        print("Preflight OK: Firestore registrations are readable; no FCM messages sent.")
        return

    if not eligible:
        print("No eligible registered installations; nothing to send.")
        return

    results = collections.Counter()
    error_samples: list[str] = []

    def task(device: Device) -> tuple[str, str, str, Device]:
        payload, mode = build_message(
            device,
            target_version_code=args.version_code,
            target_version_name=args.version_name,
            legacy_max_version_code=args.legacy_max_version_code,
            ttl_seconds=args.ttl_seconds,
        )
        result, detail = send_one(credentials, payload)

        if (
            result == "invalid_registration"
            and device.fid
            and device.token
            and device.token != device.fid
        ):
            fallback = json.loads(json.dumps(payload))
            fallback["message"].pop("fid", None)
            fallback["message"]["token"] = device.token
            result, detail = send_one(credentials, fallback)

        return result, mode, detail, device

    pruned_not_registered = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(task, device) for device in eligible]
        for future in concurrent.futures.as_completed(futures):
            result, mode, detail, device = future.result()
            results[f"{mode}:{result}"] += 1

            confirmed_not_registered = (
                result == "invalid_registration"
                and "NotRegistered" in detail
            )

            if args.prune_not_registered and confirmed_not_registered:
                delete_device_document(credentials, device)
                pruned_not_registered += 1

            if detail and len(error_samples) < 5:
                error_samples.append(detail)

    print("Send summary:")
    for key, value in sorted(results.items()):
        print(f"  {key}: {value}")

    if args.prune_not_registered:
        print("Pruned NotRegistered Firestore device records:", pruned_not_registered)

    if error_samples:
        print("Sanitized error samples (registration IDs omitted):")
        for sample in error_samples:
            print(" -", sample)

    fatal = sum(v for k, v in results.items() if k.endswith(":fatal"))
    transient_failed = sum(
        v for k, v in results.items() if k.endswith(":transient_failed")
    )
    invalid = sum(
        v for k, v in results.items() if k.endswith(":invalid_registration")
    )

    if fatal or transient_failed:
        raise SystemExit(
            f"FCM send incomplete: fatal={fatal}, transient_failed={transient_failed}"
        )

    if (
        invalid >= 5
        and invalid * 2 >= len(eligible)
        and not args.prune_not_registered
    ):
        raise SystemExit(
            "Too many registrations were rejected; inspect FCM registration data before continuing."
        )

    print("Version-aware FCM update delivery completed.")


if __name__ == "__main__":
    main()
