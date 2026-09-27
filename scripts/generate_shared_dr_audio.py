#!/usr/bin/env python3
"""Generate and preserve the shared DR Audio library with female + male voices."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_module
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from google import genai

BLOG_FEED_BASE = "https://draccesoriosrd.blogspot.com/feeds/posts/default"
PUBLIC_INDEX = "https://dr-accesorios-rd.web.app/dr-audio/index.json"
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"
MODEL = "gemini-3.8-flash-lite-tts"
LANGUAGE = "es"
LATEST_REQUIRED = 5
MAX_BACKFILL_PER_RUN = 5
MAX_LIBRARY_ENTRIES = 1500
PAGE_SIZE = 50
USER_AGENT = "DRAccesoriosRD-AudioGenerator/3.0"

VOICE_CONFIGS: dict[str, dict[str, str]] = {
    "female": {
        "name": "Achernar",
        "label": "Femenina",
        "style": (
            "Presentadora profesional de noticias tecnológicas para público latinoamericano. "
            "Voz femenina adulta joven, cálida, clara y natural; español latino neutro; "
            "ritmo conversacional ligeramente ágil, sin correr; entonación periodística moderna, "
            "serena y cercana; pausas breves y naturales; evita por completo el tono de GPS, "
            "asistente virtual, anuncio comercial o voz institucional; pronuncia marcas, siglas, "
            "números y modelos tecnológicos con claridad."
        ),
    },
    "male": {
        "name": "Charon",
        "label": "Masculina",
        "style": (
            "Presentador profesional de noticias tecnológicas para público latinoamericano. "
            "Voz masculina adulta, informativa, clara y natural; español latino neutro; "
            "ritmo conversacional ligeramente ágil, sin correr; entonación periodística moderna, "
            "segura, serena y cercana; pausas breves y naturales; evita por completo el tono de GPS, "
            "locutor publicitario, anuncio comercial o voz institucional; pronuncia marcas, siglas, "
            "números y modelos tecnológicos con claridad."
        ),
    },
}


def canonical_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = html_module.unescape(text)
    text = re.sub(r"https?://\S+", " ", text, flags=re.I)
    text = re.sub(r"www\.\S+", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonical_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    url = re.sub(r"^http://", "https://", url, flags=re.I)
    url = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return url


def voice_hash(transcript: str, voice_key: str) -> str:
    voice_name = VOICE_CONFIGS[voice_key]["name"]
    return hashlib.sha256(
        (MODEL + "\n" + voice_name + "\n" + LANGUAGE + "\n" + transcript).encode("utf-8")
    ).hexdigest()


def parse_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    title = str(((entry.get("title") or {}).get("$t") or "")).strip()
    body_html = str(
        ((entry.get("content") or {}).get("$t"))
        or ((entry.get("summary") or {}).get("$t"))
        or ""
    )

    url = ""
    for link in entry.get("link") or []:
        if link.get("rel") == "alternate":
            url = canonical_url(str(link.get("href") or ""))
            break

    if not title or not url or not body_html:
        return None

    body_text = canonical_text(body_html)
    if not body_text:
        return None

    published = str(((entry.get("published") or {}).get("$t") or "")).strip()
    transcript = f"{title}. {body_text}".strip()
    hashes = {
        voice_key: voice_hash(transcript, voice_key)
        for voice_key in VOICE_CONFIGS
    }
    audio_ids = {
        voice_key: value[:28]
        for voice_key, value in hashes.items()
    }

    # contentHash/audioId remain the legacy female values so v2.3 data stays reusable.
    return {
        "title": title,
        "url": url,
        "published": published,
        "text": transcript,
        "contentHash": hashes["female"],
        "audioId": audio_ids["female"],
        "voiceHashes": hashes,
        "audioIds": audio_ids,
    }


def parse_feed(scan_posts: int) -> list[dict[str, Any]]:
    scan_posts = max(LATEST_REQUIRED, scan_posts)
    result: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    start_index = 1

    while len(result) < scan_posts:
        requested = min(PAGE_SIZE, scan_posts - len(result))
        response = requests.get(
            BLOG_FEED_BASE,
            params={
                "alt": "json",
                "max-results": requested,
                "start-index": start_index,
            },
            headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
            timeout=30,
        )
        response.raise_for_status()
        entries = (((response.json().get("feed") or {}).get("entry")) or [])
        if not entries:
            break

        for raw_entry in entries:
            article = parse_entry(raw_entry)
            if not article:
                continue
            if article["url"] in seen_urls:
                continue
            seen_urls.add(article["url"])
            result.append(article)
            if len(result) >= scan_posts:
                break

        if len(entries) < requested:
            break

        start_index += len(entries)

    return result


def load_live_index() -> dict[str, Any]:
    try:
        response = requests.get(
            PUBLIC_INDEX,
            params={"t": time.time_ns()},
            headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
            timeout=20,
        )
        if response.status_code == 404:
            return {"entries": []}
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return {"entries": []}
        return payload
    except (requests.RequestException, ValueError):
        return {"entries": []}


def generate_wav(
    client: genai.Client,
    transcript: str,
    voice_key: str,
) -> bytes:
    voice = VOICE_CONFIGS[voice_key]
    interaction = client.interactions.create(
        model=MODEL,
        input=[
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": transcript,
                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "style": voice["style"],
                            }
                        ],
                    }
                ],
            }
        ],
        response_format={"type": "audio"},
        generation_config={
            "speech_config": [
                {"voice": voice["name"]},
            ]
        },
    )

    output_audio = getattr(interaction, "output_audio", None)
    data = getattr(output_audio, "data", None)
    if not data:
        raise RuntimeError("Gemini did not return output audio")

    if isinstance(data, (bytes, bytearray)):
        return bytes(data)

    return base64.b64decode(data)


def wav_to_m4a(wav_bytes: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp:
        temp.write(wav_bytes)
        wav_path = Path(temp.name)

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(wav_path), "-vn", "-ac", "1", "-ar", "24000",
                "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
                str(destination),
            ],
            check=True,
        )
    finally:
        try:
            wav_path.unlink()
        except FileNotFoundError:
            pass


def voice_fields(voice_key: str) -> dict[str, str]:
    if voice_key == "female":
        return {
            "url": "audioFemaleUrl",
            "path": "audioFemalePath",
            "hash": "femaleContentHash",
            "voice": "femaleVoice",
            "bytes": "femaleBytes",
        }
    return {
        "url": "audioMaleUrl",
        "path": "audioMalePath",
        "hash": "maleContentHash",
        "voice": "maleVoice",
        "bytes": "maleBytes",
    }


def current_voice_url(old: dict[str, Any] | None, voice_key: str) -> str:
    if not old:
        return ""
    fields = voice_fields(voice_key)
    if voice_key == "female":
        return str(old.get(fields["url"]) or old.get("audioUrl") or "").strip()
    return str(old.get(fields["url"]) or "").strip()


def current_voice_path(old: dict[str, Any] | None, voice_key: str) -> str:
    if not old:
        return ""
    fields = voice_fields(voice_key)
    if voice_key == "female":
        return str(old.get(fields["path"]) or old.get("audioPath") or "").strip()
    return str(old.get(fields["path"]) or "").strip()


def current_voice_hash(old: dict[str, Any] | None, voice_key: str) -> str:
    if not old:
        return ""
    fields = voice_fields(voice_key)
    if voice_key == "female":
        return str(old.get(fields["hash"]) or old.get("contentHash") or "").strip()
    return str(old.get(fields["hash"]) or "").strip()


def reusable_voice(
    old: dict[str, Any] | None,
    article: dict[str, Any],
    voice_key: str,
) -> bool:
    return bool(
        old
        and current_voice_hash(old, voice_key) == article["voiceHashes"][voice_key]
        and current_voice_url(old, voice_key)
        and current_voice_path(old, voice_key)
    )


def remove_voice_fields(entry: dict[str, Any], voice_key: str) -> None:
    fields = voice_fields(voice_key)
    for key in fields.values():
        entry.pop(key, None)

    if voice_key == "female":
        for key in ("audioUrl", "audioPath", "contentHash", "voice", "bytes"):
            entry.pop(key, None)


def normalize_reusable_voice(
    entry: dict[str, Any],
    old: dict[str, Any],
    article: dict[str, Any],
    voice_key: str,
) -> None:
    fields = voice_fields(voice_key)
    voice = VOICE_CONFIGS[voice_key]
    url = current_voice_url(old, voice_key)
    path = current_voice_path(old, voice_key)
    hash_value = article["voiceHashes"][voice_key]

    entry[fields["url"]] = url
    entry[fields["path"]] = path
    entry[fields["hash"]] = hash_value
    entry[fields["voice"]] = voice["name"]

    old_bytes = old.get(fields["bytes"])
    if old_bytes is None and voice_key == "female":
        old_bytes = old.get("bytes")
    if old_bytes is not None:
        entry[fields["bytes"]] = old_bytes

    if voice_key == "female":
        # Legacy aliases are intentionally preserved for every installed v2.3 client.
        entry["audioUrl"] = url
        entry["audioPath"] = path
        entry["contentHash"] = hash_value
        entry["voice"] = voice["name"]
        if old_bytes is not None:
            entry["bytes"] = old_bytes


def apply_generated_voice(
    entry: dict[str, Any],
    article: dict[str, Any],
    voice_key: str,
    m4a_path: Path,
) -> None:
    fields = voice_fields(voice_key)
    voice = VOICE_CONFIGS[voice_key]
    audio_id = article["audioIds"][voice_key]
    audio_path = f"/dr-audio/{audio_id}.m4a"
    audio_url = PUBLIC_BASE + audio_path
    hash_value = article["voiceHashes"][voice_key]
    size = m4a_path.stat().st_size

    entry[fields["url"]] = audio_url
    entry[fields["path"]] = audio_path
    entry[fields["hash"]] = hash_value
    entry[fields["voice"]] = voice["name"]
    entry[fields["bytes"]] = size

    if voice_key == "female":
        # Backward compatibility with v2.3 and earlier shared-audio readers.
        entry["audioUrl"] = audio_url
        entry["audioPath"] = audio_path
        entry["contentHash"] = hash_value
        entry["voice"] = voice["name"]
        entry["bytes"] = size


def refresh_article_entry(
    old: dict[str, Any] | None,
    article: dict[str, Any],
) -> dict[str, Any]:
    entry = dict(old or {})
    entry.update(
        {
            "title": article["title"],
            "url": article["url"],
            "published": article["published"],
            "model": MODEL,
            "format": "audio/mp4",
        }
    )

    for voice_key in VOICE_CONFIGS:
        if reusable_voice(old, article, voice_key):
            normalize_reusable_voice(entry, old or {}, article, voice_key)
        else:
            remove_voice_fields(entry, voice_key)

    return entry


def quota_exhausted_error(error: Exception) -> bool:
    error_text = str(error).lower()
    return any(
        marker in error_text
        for marker in (
            "429",
            "resource_exhausted",
            "resource exhausted",
            "quota",
            "rate limit",
            "rate_limit",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dr_audio_build")
    parser.add_argument("--scan-posts", type=int, default=500)
    parser.add_argument("--backfill-count", type=int, default=0)
    parser.add_argument(
        "--opportunistic-backfill",
        action="store_true",
        help=(
            "Backfill only when no newest voice needs generation; "
            "if Gemini daily quota is exhausted, stop backfill without failing."
        ),
    )
    args = parser.parse_args()

    backfill_count = max(0, min(MAX_BACKFILL_PER_RUN, args.backfill_count))

    api_key = os.environ.get("GEMINI_API_KEY_DR_AUDIO", "").strip()
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY_DR_AUDIO is missing. Add it as a GitHub Actions secret before running."
        )

    output = Path(args.output)
    audio_dir = output / "dr-audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    feed = parse_feed(args.scan_posts)
    if not feed:
        raise RuntimeError("Blogger feed returned no usable posts")

    live_index = load_live_index()
    old_entries_list = [
        entry
        for entry in (live_index.get("entries") or [])
        if isinstance(entry, dict) and entry.get("url")
    ]

    # Keep every historical entry already published, including legacy female-only entries.
    final_by_url: dict[str, dict[str, Any]] = {
        canonical_url(str(entry.get("url") or "")): dict(entry)
        for entry in old_entries_list
        if canonical_url(str(entry.get("url") or ""))
    }
    old_by_url = dict(final_by_url)

    latest = feed[:LATEST_REQUIRED]

    # Refresh metadata and discard stale voice links before planning generation.
    for article in feed:
        old = old_by_url.get(article["url"])
        refreshed = refresh_article_entry(old, article)
        if current_voice_url(refreshed, "female") or current_voice_url(refreshed, "male"):
            final_by_url[article["url"]] = refreshed
        else:
            final_by_url.pop(article["url"], None)

    # Priority is deliberate: all female gaps first (v2.3 compatibility), then male gaps.
    latest_tasks: list[tuple[dict[str, Any], str]] = []
    for voice_key in ("female", "male"):
        for article in latest:
            current = final_by_url.get(article["url"])
            if not reusable_voice(current, article, voice_key):
                latest_tasks.append((article, voice_key))

    if args.opportunistic_backfill and latest_tasks:
        print(
            "Opportunistic backfill skipped: newest DR Audio voices have priority.",
            flush=True,
        )
        backfill_count = 0

    backfill_tasks: list[tuple[dict[str, Any], str]] = []
    if backfill_count:
        for article in feed[LATEST_REQUIRED:]:
            if len(backfill_tasks) >= backfill_count:
                break
            current = final_by_url.get(article["url"])
            for voice_key in ("female", "male"):
                if len(backfill_tasks) >= backfill_count:
                    break
                if not reusable_voice(current, article, voice_key):
                    backfill_tasks.append((article, voice_key))

    client = genai.Client(api_key=api_key)
    generated_count = 0
    female_generated = 0
    male_generated = 0
    backfilled_count = 0
    quota_exhausted = False

    tasks = [
        (article, voice_key, "latest")
        for article, voice_key in latest_tasks
    ] + [
        (article, voice_key, "backfill")
        for article, voice_key in backfill_tasks
    ]

    for article, voice_key, role in tasks:
        if quota_exhausted:
            break

        voice = VOICE_CONFIGS[voice_key]
        print(
            f"Generating DR Audio ({role}, {voice['label']} / {voice['name']}): "
            f"{article['title']}",
            flush=True,
        )

        try:
            wav = generate_wav(client, article["text"], voice_key)
        except Exception as error:
            if quota_exhausted_error(error):
                quota_exhausted = True

                current = final_by_url.get(article["url"])
                female_ready = reusable_voice(current, article, "female")

                # Female coverage of the newest five is the compatibility floor.
                # A missing male voice can safely retry on the next 10-minute run.
                if role == "latest" and voice_key == "female" and not female_ready:
                    raise

                print(
                    f"Gemini TTS quota exhausted; pending {voice['label'].lower()} voice "
                    "will retry on a future run.",
                    flush=True,
                )
                break

            raise

        audio_id = article["audioIds"][voice_key]
        m4a_path = audio_dir / f"{audio_id}.m4a"
        wav_to_m4a(wav, m4a_path)

        current = final_by_url.get(article["url"], {})
        current.update(
            {
                "title": article["title"],
                "url": article["url"],
                "published": article["published"],
                "model": MODEL,
                "format": "audio/mp4",
            }
        )
        apply_generated_voice(current, article, voice_key, m4a_path)
        final_by_url[article["url"]] = current

        generated_count += 1
        if voice_key == "female":
            female_generated += 1
        else:
            male_generated += 1
        if role == "backfill":
            backfilled_count += 1

        # Gentle spacing keeps consecutive TTS requests inside RPM limits.
        time.sleep(1.0)

    missing_latest_female = [
        article["title"]
        for article in latest
        if not reusable_voice(final_by_url.get(article["url"]), article, "female")
    ]
    if missing_latest_female:
        raise RuntimeError(
            "Newest DR Audio female coverage is incomplete: "
            + " | ".join(missing_latest_female)
        )

    latest_male_ready = sum(
        1
        for article in latest
        if reusable_voice(final_by_url.get(article["url"]), article, "male")
    )

    all_final_entries = sorted(
        final_by_url.values(),
        key=lambda entry: str(entry.get("published") or ""),
        reverse=True,
    )

    pruned_count = max(0, len(all_final_entries) - MAX_LIBRARY_ENTRIES)
    final_entries = all_final_entries[:MAX_LIBRARY_ENTRIES]

    index = {
        "schemaVersion": 3,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        # Legacy metadata retained for older tooling.
        "voice": VOICE_CONFIGS["female"]["name"],
        "voices": {
            key: {
                "name": config["name"],
                "label": config["label"],
            }
            for key, config in VOICE_CONFIGS.items()
        },
        "latestCount": min(LATEST_REQUIRED, len(feed)),
        "latestMaleReady": latest_male_ready,
        "historyCount": len(final_entries),
        "maxLibraryEntries": MAX_LIBRARY_ENTRIES,
        "prunedCount": pruned_count,
        "entries": final_entries,
    }

    (audio_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"DR Audio dual-voice ready: {len(latest)} newest female voices protected; "
        f"{latest_male_ready}/{len(latest)} newest male voices ready; "
        f"{generated_count} voice files generated this run "
        f"({female_generated} female, {male_generated} male); "
        f"{backfilled_count} historical voice files backfilled; "
        f"{len(final_entries)} total article entries preserved; "
        f"{pruned_count} oldest entries pruned; "
        f"library cap={MAX_LIBRARY_ENTRIES}.",
        flush=True,
    )


if __name__ == "__main__":
    main()
