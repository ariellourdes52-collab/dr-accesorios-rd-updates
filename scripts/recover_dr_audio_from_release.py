#!/usr/bin/env python3
"""Recover reusable DR Audio voices from a historical Firebase Hosting preview."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from generate_shared_dr_audio import (
    LATEST_REQUIRED,
    MODEL,
    MAX_LIBRARY_ENTRIES,
    PUBLIC_BASE,
    PUBLIC_INDEX,
    VOICE_CONFIGS,
    canonical_url,
    current_voice_path,
    parse_feed,
    refresh_article_entry,
    reusable_voice,
    normalize_reusable_voice,
)


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

    old_entries = [
        entry
        for entry in (old_index.get("entries") or [])
        if isinstance(entry, dict) and entry.get("url")
    ]
    live_entries = [
        entry
        for entry in (live_index.get("entries") or [])
        if isinstance(entry, dict) and entry.get("url")
    ]

    old_by_url = {
        canonical_url(str(entry.get("url") or "")): dict(entry)
        for entry in old_entries
        if canonical_url(str(entry.get("url") or ""))
    }
    final_by_url = {
        canonical_url(str(entry.get("url") or "")): dict(entry)
        for entry in live_entries
        if canonical_url(str(entry.get("url") or ""))
    }

    feed = parse_feed(max(20, LATEST_REQUIRED))
    latest = feed[:LATEST_REQUIRED]
    require(len(latest) == LATEST_REQUIRED, "Could not resolve the newest five Blogger posts")

    recovered: list[str] = []

    for article in latest:
        url = article["url"]
        current = refresh_article_entry(final_by_url.get(url), article)
        historical = old_by_url.get(url)

        for voice_key in ("female", "male"):
            if reusable_voice(current, article, voice_key):
                continue
            if not historical or not reusable_voice(historical, article, voice_key):
                print(
                    f"Historical release has no reusable {voice_key} voice for: "
                    f"{article['title']}",
                    flush=True,
                )
                continue

            old_path = current_voice_path(historical, voice_key)
            require(old_path, f"Historical {voice_key} path missing for {article['title']}")
            destination = audio_dir / Path(old_path).name
            if not destination.exists():
                download_preview_asset(preview_base, old_path, destination)

            normalize_reusable_voice(current, historical, article, voice_key)
            recovered.append(f"{voice_key}: {article['title']}")
            print(
                f"RECOVERED {voice_key}: {article['title']} -> {old_path}",
                flush=True,
            )

        if reusable_voice(current, article, "female") or reusable_voice(current, article, "male"):
            final_by_url[url] = current

    latest_female_ready = sum(
        1
        for article in latest
        if reusable_voice(final_by_url.get(article["url"]), article, "female")
    )
    latest_male_ready = sum(
        1
        for article in latest
        if reusable_voice(final_by_url.get(article["url"]), article, "male")
    )

    require(recovered, "Historical release did not contain any reusable missing voice")
    require(
        latest_female_ready == LATEST_REQUIRED,
        f"Recovery would still leave female coverage at {latest_female_ready}/{LATEST_REQUIRED}; aborting deploy",
    )

    entries = sorted(
        final_by_url.values(),
        key=lambda entry: str(entry.get("published") or ""),
        reverse=True,
    )[:MAX_LIBRARY_ENTRIES]

    index = dict(live_index)
    index.update(
        {
            "schemaVersion": max(3, int(live_index.get("schemaVersion") or 0)),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "model": MODEL,
            "voice": VOICE_CONFIGS["female"]["name"],
            "voices": {
                key: {"name": cfg["name"], "label": cfg["label"]}
                for key, cfg in VOICE_CONFIGS.items()
            },
            "latestCount": LATEST_REQUIRED,
            "latestFemaleReady": latest_female_ready,
            "latestMaleReady": latest_male_ready,
            "historyCount": len(entries),
            "maxLibraryEntries": MAX_LIBRARY_ENTRIES,
            "entries": entries,
        }
    )
    index.pop("quotaBlockedUntil", None)
    index.pop("quotaBlockedReason", None)

    (audio_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Recovery build ready: {latest_female_ready}/{LATEST_REQUIRED} female, "
        f"{latest_male_ready}/{LATEST_REQUIRED} male; "
        f"{len(recovered)} historical voice file(s) recovered.",
        flush=True,
    )

    # Require the recovery to improve male coverage too. The pre-incident release
    # is expected to contain 5/5; this guards against accidentally publishing a
    # stale or unrelated historical version.
    require(
        latest_male_ready > int(live_index.get("latestMaleReady") or 0),
        "Historical release did not improve newest male coverage; aborting deploy",
    )


if __name__ == "__main__":
    main()
