#!/usr/bin/env python3
"""Restore the full DR Audio catalog from a historical Firebase Hosting release.

This recovery never calls Gemini. It unions the pre-incident catalog with the
current live catalog, always preferring the current live entry for duplicate
article URLs, and downloads every historical audio asset that is no longer
present in the live catalog so Firebase Hosting can republish it safely.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

MAX_LIBRARY_ENTRIES = 1500
PUBLIC_INDEX = "https://dr-accesorios-rd.web.app/dr-audio/index.json"


def canonical_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    url = re.sub(r"^http://", "https://", url, flags=re.I)
    url = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return url



def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def get_json(url: str) -> dict[str, Any]:
    response = requests.get(
        url,
        params={"recovery": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    require(isinstance(payload, dict), f"Expected JSON object from {url}")
    return payload


def valid_audio_path(value: object) -> str:
    path = str(value or "").strip()
    if path.startswith("/dr-audio/") and path.endswith(".m4a"):
        return path
    return ""


def entry_audio_paths(entry: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("audioPath", "audioFemalePath", "audioMalePath"):
        path = valid_audio_path(entry.get(key))
        if path and path not in paths:
            paths.append(path)
    return paths


def download_preview_asset(base_url: str, path: str, destination: Path) -> None:
    require(path.startswith("/dr-audio/"), f"Unsafe DR Audio path: {path}")
    url = base_url.rstrip("/") + path
    response = requests.get(
        url,
        params={"recovery": time.time_ns()},
        headers={"Cache-Control": "no-cache"},
        timeout=120,
    )
    response.raise_for_status()
    require(
        len(response.content) > 1024,
        f"Historical audio is unexpectedly small: {path}",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)


def normalize_entries(index: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(entry)
        for entry in (index.get("entries") or [])
        if isinstance(entry, dict) and canonical_url(str(entry.get("url") or ""))
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-base-url", required=True)
    parser.add_argument("--output", default="dr_audio_recovery_build")
    args = parser.parse_args()

    preview_base = args.preview_base_url.rstrip("/")
    output = Path(args.output)
    audio_dir = output / "dr-audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    old_index = get_json(preview_base + "/dr-audio/index.json")
    live_index = get_json(PUBLIC_INDEX)

    old_entries = normalize_entries(old_index)
    live_entries = normalize_entries(live_index)

    require(old_entries, "Historical release has no DR Audio entries")
    require(live_entries, "Current live DR Audio catalog has no entries")

    old_by_url = {
        canonical_url(str(entry.get("url") or "")): dict(entry)
        for entry in old_entries
    }
    live_by_url = {
        canonical_url(str(entry.get("url") or "")): dict(entry)
        for entry in live_entries
    }

    historical_count = len(old_by_url)
    live_count = len(live_by_url)

    # The selected pre-incident release is expected to be the healthy catalog
    # that existed before today's reduction. Refuse to deploy a stale/wrong
    # release that does not actually contain the historical library.
    require(
        historical_count >= 27,
        f"Historical release only contains {historical_count} entries; expected at least 27. Aborting.",
    )
    require(
        historical_count >= live_count,
        f"Historical release ({historical_count}) is unexpectedly smaller than live ({live_count}). Aborting.",
    )

    # Start with the historical library, then overlay the live catalog.
    # This guarantees that today's current entries/metadata win on duplicates.
    final_by_url: dict[str, dict[str, Any]] = dict(old_by_url)
    final_by_url.update(live_by_url)

    entries = sorted(
        final_by_url.values(),
        key=lambda entry: str(entry.get("published") or ""),
        reverse=True,
    )[:MAX_LIBRARY_ENTRIES]

    final_urls = {
        canonical_url(str(entry.get("url") or ""))
        for entry in entries
    }
    live_urls = set(live_by_url)
    historical_urls = set(old_by_url)

    require(
        live_urls <= final_urls,
        "Recovery would lose one or more currently live DR Audio entries. Aborting.",
    )
    require(
        historical_urls <= final_urls,
        "Recovery would fail to restore one or more historical DR Audio entries. Aborting.",
    )

    # Confirm that sorting/merge does not replace today's newest live articles
    # with stale historical metadata.
    live_top_urls = [
        canonical_url(str(entry.get("url") or ""))
        for entry in live_entries[: min(5, len(live_entries))]
    ]
    final_top_urls = [
        canonical_url(str(entry.get("url") or ""))
        for entry in entries[: len(live_top_urls)]
    ]
    require(
        final_top_urls == live_top_urls,
        "Historical merge would alter the order/content of the current newest live entries. Aborting.",
    )

    live_audio_paths = {
        path
        for entry in live_entries
        for path in entry_audio_paths(entry)
    }
    historical_audio_paths = {
        path
        for entry in old_entries
        for path in entry_audio_paths(entry)
    }

    # Any historical path no longer referenced by live Hosting must be copied
    # from the pre-incident preview into this build, otherwise the next Hosting
    # version would have no bytes available to restore it.
    paths_to_restore = sorted(historical_audio_paths - live_audio_paths)
    restored_paths: list[str] = []
    for path in paths_to_restore:
        destination = audio_dir / Path(path).name
        if not destination.exists():
            download_preview_asset(preview_base, path, destination)
        restored_paths.append(path)
        print(f"RECOVERED HISTORICAL ASSET: {path}", flush=True)

    # The current live entries are authoritative. Preserve their metadata and
    # readiness counters, only replacing library-wide fields.
    index = dict(live_index)
    index.update(
        {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "historyCount": len(entries),
            "maxLibraryEntries": MAX_LIBRARY_ENTRIES,
            "prunedCount": 0,
            "entries": entries,
        }
    )

    (audio_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest = {
        "historicalCount": historical_count,
        "liveCountBeforeRecovery": live_count,
        "expectedFinalCount": len(entries),
        "historicalUrls": sorted(historical_urls),
        "liveUrls": sorted(live_urls),
        "liveTopUrls": live_top_urls,
        "historicalAudioPaths": sorted(historical_audio_paths),
        "liveAudioPaths": sorted(live_audio_paths),
        "restoredAudioPaths": restored_paths,
    }
    (output / "recovery-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "FULL DR AUDIO RECOVERY BUILD READY: "
        f"historical={historical_count}, live_before={live_count}, "
        f"final={len(entries)}, assets_restored={len(restored_paths)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
