#!/usr/bin/env python3
"""Generate and preserve the shared DR Audio library with female + male voices."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import html as html_module
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

import httpx
import requests
from bs4 import BeautifulSoup
from google import genai

BLOG_FEED_BASE = "https://draccesoriosrd.blogspot.com/feeds/posts/default"
PUBLIC_INDEX = "https://dr-accesorios-rd.web.app/dr-audio/index.json"
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"
# MODEL remains the legacy content-identity key so already-published Lite audio
# is reusable. RENDER_MODEL is the active renderer for new audio.
MODEL = "gemini-3.8-flash-lite-tts"
RENDER_MODEL = MODEL
LEGACY_RESCUE_MODEL = "gemini-3.1-flash-tts-preview"
LANGUAGE = "es"
LATEST_REQUIRED = 5
MAX_BACKFILL_PER_RUN = 5
MAX_LIBRARY_ENTRIES = 1500
PAGE_SIZE = 50
USER_AGENT = "DRAccesoriosRD-AudioGenerator/3.0.7"
TTS_REQUEST_TIMEOUT_MS = 600_000
TTS_HARD_TIMEOUT_SECONDS = 600
TTS_STALL_TIMEOUT_SECONDS = 75
TTS_GLOBAL_BUDGET_SECONDS = 900
TTS_CHUNK_TRIGGER_CHARS = 20_000
TTS_CHUNK_TARGET_CHARS = 1800
TTS_MIN_SCHEDULE_INTERVAL_SECONDS = 8 * 60
TTS_DIRECT_REQUEST_TIMEOUT_SECONDS = 240

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
    """Never rebuild over an unreadable or malformed live catalog."""
    response = requests.get(
        PUBLIC_INDEX,
        params={"t": time.time_ns()},
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise RuntimeError("Live DR Audio catalog is invalid; refusing to replace it")
    return payload


_PUBLIC_AUDIO_EXISTS_CACHE: dict[str, bool] = {}


def public_audio_exists(url: str) -> bool:
    """Return False only for a confirmed missing public asset.

    Network/transient errors are treated as unknown/available so a temporary
    connectivity problem never burns Gemini quota by forcing regeneration.
    """
    url = str(url or "").strip()
    if not url:
        return False
    if url in _PUBLIC_AUDIO_EXISTS_CACHE:
        return _PUBLIC_AUDIO_EXISTS_CACHE[url]

    try:
        response = requests.head(
            url,
            headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
            timeout=15,
            allow_redirects=True,
        )
        if response.status_code in (404, 410):
            result = False
        elif response.ok:
            result = True
        else:
            print(
                f"Could not confirm DR Audio asset HTTP {response.status_code}; "
                f"keeping metadata for now: {url}",
                flush=True,
            )
            result = True
    except requests.RequestException as error:
        print(
            f"Could not verify DR Audio asset due to transient error; "
            f"keeping metadata for now: {url} ({error})",
            flush=True,
        )
        result = True

    _PUBLIC_AUDIO_EXISTS_CACHE[url] = result
    return result


class TTSHardTimeoutError(TimeoutError):
    pass


def generate_wav(
    client: genai.Client,
    transcript: str,
    voice_key: str,
) -> bytes:
    voice = VOICE_CONFIGS[voice_key]

    def _hard_timeout_handler(_signum: int, _frame: Any) -> None:
        raise TTSHardTimeoutError(
            f"Gemini direct TTS exceeded {TTS_DIRECT_REQUEST_TIMEOUT_SECONDS}s"
        )

    previous_handler = signal.signal(signal.SIGALRM, _hard_timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, TTS_DIRECT_REQUEST_TIMEOUT_SECONDS)
    try:
        interaction = client.interactions.create(
            model=MODEL,
            timeout=TTS_DIRECT_REQUEST_TIMEOUT_SECONDS,
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

    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)

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
    entry[f"{voice_key}RenderModel"] = RENDER_MODEL

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


def daily_quota_exhausted_error(error: Exception) -> bool:
    error_text = str(error).lower()
    return quota_exhausted_error(error) and any(
        marker in error_text
        for marker in (
            "requests per day",
            "request per day",
            "per day",
            "daily quota",
        )
    )


def tts_timeout_error(error: Exception) -> bool:
    if isinstance(
        error,
        (
            TimeoutError,
            TTSHardTimeoutError,
            httpx.TimeoutException,
        ),
    ):
        return True

    error_text = str(error).lower()
    return any(
        marker in error_text
        for marker in (
            "timeout",
            "timed out",
            "deadline exceeded",
            "read timeout",
            "connect timeout",
        )
    )


def next_gemini_daily_reset_iso() -> str:
    # Gemini Free Tier daily quotas reset on the provider's Pacific-time day.
    # Add a 5-minute safety margin so the first post-reset run does not race the reset.
    pacific = ZoneInfo("America/Los_Angeles")
    now_pacific = datetime.now(pacific)
    next_date = (now_pacific + timedelta(days=1)).date()
    reset_pacific = datetime(
        next_date.year,
        next_date.month,
        next_date.day,
        0,
        5,
        tzinfo=pacific,
    )
    return reset_pacific.astimezone(timezone.utc).isoformat()


def parse_blocked_until(raw: str) -> datetime | None:
    raw = str(raw or "").strip()
    if not raw:
        return None

    try:
        blocked_until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    if blocked_until.tzinfo is None:
        blocked_until = blocked_until.replace(tzinfo=timezone.utc)

    if blocked_until > datetime.now(timezone.utc):
        return blocked_until.astimezone(timezone.utc)

    return None


def active_daily_quota_block_until(live_index: dict[str, Any]) -> datetime | None:
    return parse_blocked_until(str(live_index.get("quotaBlockedUntil") or ""))


def local_daily_quota_block_until(state_path: Path) -> datetime | None:
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None

    return parse_blocked_until(str(state.get("blockedUntil") or ""))


def write_daily_quota_state(state_path: Path, blocked_until: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "blockedUntil": blocked_until,
                "reason": "gemini_free_tier_daily_tts_limit",
                "model": MODEL,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dr_audio_build")
    parser.add_argument("--scan-posts", type=int, default=500)
    parser.add_argument(
        "--backfill-count",
        type=int,
        default=0,
        help="Number of older articles to complete with every missing voice (0-5).",
    )
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

    state_dir = Path(os.environ.get("DR_AUDIO_STATE_DIR", ".dr-audio-state"))
    quota_state_path = state_dir / "gemini-daily-quota.json"

    api_key = os.environ.get("GEMINI_API_KEY_DR_AUDIO", "").strip()
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY_DR_AUDIO is missing. Add it as a GitHub Actions secret before running."
        )

    output = Path(args.output)
    audio_dir = output / "dr-audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    chunk_cache_dir = Path(
        os.environ.get("DR_AUDIO_CHUNK_CACHE_DIR", ".dr-audio-chunks")
    )
    chunk_cache_dir.mkdir(parents=True, exist_ok=True)
    # Keep the cache directory non-empty so each run can save a fresh snapshot
    # and supersede stale partial chunks from older runs.
    (chunk_cache_dir / ".cache-version").write_text(
        "dr-audio-chunks-v1\n",
        encoding="utf-8",
    )

    feed = parse_feed(args.scan_posts)
    if not feed:
        raise RuntimeError("Blogger feed returned no usable posts")

    live_index = load_live_index()

    live_blocked_until = active_daily_quota_block_until(live_index)
    local_blocked_until = local_daily_quota_block_until(quota_state_path)
    blocked_candidates = [
        value
        for value in (live_blocked_until, local_blocked_until)
        if value is not None
    ]
    blocked_until = max(blocked_candidates) if blocked_candidates else None

    if blocked_until is not None:
        # Preserve the currently published catalog while the daily TTS quota is
        # known to be unavailable. The local marker is cached by GitHub Actions,
        # so this still works even if Firebase Hosting itself is rate-limited.
        (audio_dir / "index.json").write_text(
            json.dumps(live_index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            "Gemini TTS daily quota cooldown active until "
            f"{blocked_until.isoformat()}; skipping generation cleanly.",
            flush=True,
        )
        return

    try:
        quota_state_path.unlink()
    except FileNotFoundError:
        pass

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

    # The catalog can only be reused when the newest public assets really exist.
    # This specifically prevents index.json from claiming a male voice is ready
    # while its M4A is missing from Firebase Hosting.
    for article in latest:
        old = old_by_url.get(article["url"])
        if not old:
            continue
        for voice_key in ("female", "male"):
            if not reusable_voice(old, article, voice_key):
                continue
            audio_url = current_voice_url(old, voice_key)
            if public_audio_exists(audio_url):
                continue
            print(
                f"Live DR Audio asset is missing; regenerating "
                f"{VOICE_CONFIGS[voice_key]['label'].lower()} voice: "
                f"{article['title']}",
                flush=True,
            )
            remove_voice_fields(old, voice_key)

    # Refresh metadata and discard stale voice links before planning generation.
    for article in latest:
        old = old_by_url.get(article["url"])
        refreshed = refresh_article_entry(old, article)
        if current_voice_url(refreshed, "female") or current_voice_url(refreshed, "male"):
            final_by_url[article["url"]] = refreshed
        else:
            final_by_url.pop(article["url"], None)

    # Hard priority: the newest five must have the default female voice first.
    # Do not spend this run's Gemini budget on male voices while any of the
    # newest five is still missing its female audio.
    latest_female_tasks: list[tuple[dict[str, Any], str]] = []
    latest_male_tasks: list[tuple[dict[str, Any], str]] = []

    deferred_voice_titles = {
        (
            "Tesla sorprende al mercado: vende más de 486 mil vehículos "
            "mientras su futuro empieza a mirar más allá del automóvil",
            "male",
        ),
    }

    def intentionally_deferred_voice(
        article: dict[str, Any],
        voice_key: str,
    ) -> bool:
        return (article["title"], voice_key) in deferred_voice_titles

    for article in latest:
        current = final_by_url.get(article["url"])
        if not reusable_voice(current, article, "female"):
            latest_female_tasks.append((article, "female"))
        if not reusable_voice(current, article, "male"):
            if intentionally_deferred_voice(article, "male"):
                print(
                    "DR Audio voice intentionally deferred by operator: "
                    f"male | {article['title']}",
                    flush=True,
                )
            else:
                latest_male_tasks.append((article, "male"))

    def has_completed_partial_chunks(
        article: dict[str, Any],
        voice_key: str,
    ) -> bool:
        audio_id = article["audioIds"][voice_key]
        return any(
            path.is_file() and path.stat().st_size > 44
            for path in chunk_cache_dir.glob(
                f"{audio_id}-{voice_key}-*.wav"
            )
        )

    if latest_female_tasks:
        partial_female_tasks = [
            task
            for task in latest_female_tasks
            if has_completed_partial_chunks(task[0], task[1])
        ]
        partial_female_keys = {
            (task[0]["url"], task[1])
            for task in partial_female_tasks
        }
        fresh_female_tasks = [
            task
            for task in latest_female_tasks
            if (task[0]["url"], task[1]) not in partial_female_keys
        ]
        latest_tasks = partial_female_tasks + fresh_female_tasks
        if latest_male_tasks:
            latest_tasks.append(latest_male_tasks[0])
        print(
            f"Female-first mode: {len(latest_female_tasks)} of the newest "
            f"{len(latest)} article(s) still need the default female voice. "
            "The newest eligible male voice may follow only after female "
            "coverage reaches 5/5 in this same run.",
            flush=True,
        )
    else:
        latest_tasks = latest_male_tasks

    if args.opportunistic_backfill and latest_tasks:
        print(
            "Opportunistic backfill skipped: newest DR Audio voices have priority.",
            flush=True,
        )
        backfill_count = 0

    # backfill_count is measured in ARTICLES, not individual voice files.
    # One historical article is selected only if it is incomplete, and every
    # missing voice for that article is queued so BACKFILL=1 completes one article.
    backfill_tasks: list[tuple[dict[str, Any], str]] = []
    backfill_articles_selected = 0
    if backfill_count:
        for article in feed[LATEST_REQUIRED:]:
            current = final_by_url.get(article["url"])
            missing_voices = [
                voice_key
                for voice_key in ("female", "male")
                if not reusable_voice(current, article, voice_key)
            ]
            if not missing_voices:
                continue

            for voice_key in missing_voices:
                backfill_tasks.append((article, voice_key))

            backfill_articles_selected += 1
            if backfill_articles_selected >= backfill_count:
                break

    client = genai.Client(api_key=api_key)
    generated_count = 0
    female_generated = 0
    male_generated = 0
    pending_reused_count = 0
    backfilled_count = 0
    quota_exhausted = False
    quota_blocked_until = ""
    deferred_timeout_tasks: set[tuple[str, str]] = set()

    tasks = [
        (article, voice_key, "latest")
        for article, voice_key in latest_tasks
    ] + [
        (article, voice_key, "backfill")
        for article, voice_key in backfill_tasks
    ]

    # GitHub schedule is intentionally redundant (every 5 minutes). Persist the
    # actual TTS attempt time in the restored chunk cache so a delayed/duplicate
    # scheduler event cannot double-spend Gemini quota.
    attempt_state_path = chunk_cache_dir / "last-tts-attempt.json"
    event_name = os.environ.get("EVENT_NAME", "").strip()
    scheduler_guard = (
        os.environ.get("SCHEDULER_GUARD", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    if (
        tasks
        and (event_name == "schedule" or scheduler_guard)
        and attempt_state_path.exists()
    ):
        try:
            attempt_state = json.loads(
                attempt_state_path.read_text(encoding="utf-8")
            )
            attempted_at = datetime.fromisoformat(
                str(attempt_state.get("attemptedAt") or "").replace("Z", "+00:00")
            )
            if attempted_at.tzinfo is None:
                attempted_at = attempted_at.replace(tzinfo=timezone.utc)
            elapsed = (
                datetime.now(timezone.utc) - attempted_at.astimezone(timezone.utc)
            ).total_seconds()
        except (OSError, ValueError, TypeError):
            elapsed = TTS_MIN_SCHEDULE_INTERVAL_SECONDS

        if (
            elapsed < TTS_MIN_SCHEDULE_INTERVAL_SECONDS
            and bool(live_index.get("entries"))
        ):
            (audio_dir / "index.json").write_text(
                json.dumps(live_index, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                "DR Audio scheduler guard: previous real TTS attempt was "
                f"{int(elapsed)}s ago; skipping this redundant schedule event "
                "without calling Gemini.",
                flush=True,
            )
            return

    if tasks:
        attempt_state_path.write_text(
            json.dumps(
                {
                    "attemptedAt": datetime.now(timezone.utc).isoformat(),
                    "pendingTasks": len(tasks),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    tts_batch_started = time.monotonic()
    tts_deadline = tts_batch_started + TTS_GLOBAL_BUDGET_SECONDS

    for task_index, (article, voice_key, role) in enumerate(tasks):
        if quota_exhausted:
            break

        if time.monotonic() - tts_batch_started >= TTS_GLOBAL_BUDGET_SECONDS:
            for (
                pending_article,
                pending_voice_key,
                _pending_role,
            ) in tasks[task_index:]:
                deferred_timeout_tasks.add(
                    (pending_article["url"], pending_voice_key)
                )

            print(
                f"Gemini TTS global generation budget "
                f"({TTS_GLOBAL_BUDGET_SECONDS}s) reached; "
                "publishing completed/reused audio now and "
                "deferring the remaining voices to the next run.",
                flush=True,
            )
            break

        if role == "latest" and voice_key == "male":
            missing_latest_female = [
                latest_article["title"]
                for latest_article in latest
                if not reusable_voice(
                    final_by_url.get(latest_article["url"]),
                    latest_article,
                    "female",
                )
            ]
            if missing_latest_female:
                print(
                    "Male generation deferred because newest female coverage "
                    "is still incomplete: "
                    + " | ".join(missing_latest_female),
                    flush=True,
                )
                continue

        voice = VOICE_CONFIGS[voice_key]
        audio_id = article["audioIds"][voice_key]
        m4a_path = audio_dir / f"{audio_id}.m4a"

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

        # A previous run may have generated this exact content-hash audio before
        # Firebase Hosting rejected the publish. Reuse that cached .m4a instead
        # of spending another Gemini TTS request for identical content.
        if m4a_path.exists() and m4a_path.stat().st_size > 1024:
            print(
                f"Reusing unpublished DR Audio ({role}, "
                f"{voice['label']} / {voice['name']}): {article['title']}",
                flush=True,
            )
            apply_generated_voice(current, article, voice_key, m4a_path)
            final_by_url[article["url"]] = current
            pending_reused_count += 1
            if role == "backfill":
                backfilled_count += 1
            continue

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

                if daily_quota_exhausted_error(error):
                    quota_blocked_until = next_gemini_daily_reset_iso()
                    write_daily_quota_state(quota_state_path, quota_blocked_until)
                    print(
                        "Gemini TTS daily Free Tier quota exhausted; "
                        f"pausing TTS attempts until {quota_blocked_until}. "
                        f"Pending {voice['label'].lower()} voice will resume automatically.",
                        flush=True,
                    )
                else:
                    print(
                        f"Gemini TTS temporary rate limit reached; pending "
                        f"{voice['label'].lower()} voice will retry on the next scheduled run.",
                        flush=True,
                    )
                break

            if tts_timeout_error(error):
                deferred_timeout_tasks.add(
                    (article["url"], voice_key)
                )
                print(
                    "Gemini TTS attempt timed out; "
                    f"deferring {voice['label'].lower()} voice until the "
                    f"next run while preserving any completed chunks: "
                    f"{article['title']}",
                    flush=True,
                )
                continue

            raise

        wav_to_m4a(wav, m4a_path)
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
        missing_latest_female_urls = {
            article["url"]
            for article in latest
            if not reusable_voice(
                final_by_url.get(article["url"]),
                article,
                "female",
            )
        }
        deferred_female_urls = {
            url
            for url, voice_key in deferred_timeout_tasks
            if voice_key == "female"
        }
        all_missing_female_deferred = (
            bool(missing_latest_female_urls)
            and missing_latest_female_urls <= deferred_female_urls
        )

        if not quota_exhausted and not all_missing_female_deferred:
            raise RuntimeError(
                "Newest DR Audio female coverage is incomplete: "
                + " | ".join(missing_latest_female)
            )

        reason = (
            "Gemini TTS quota is unavailable"
            if quota_exhausted
            else "one or more Gemini TTS requests timed out"
        )
        print(
            "Newest DR Audio female coverage is temporarily incomplete because "
            + reason
            + "; publishing every completed voice now and retrying the pending "
            "voice automatically on the next run. Pending: "
            + " | ".join(missing_latest_female),
            flush=True,
        )

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
        "renderModel": RENDER_MODEL,
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
        "latestFemaleReady": latest_female_ready,
        "latestMaleReady": latest_male_ready,
        "historyCount": len(final_entries),
        "maxLibraryEntries": MAX_LIBRARY_ENTRIES,
        "prunedCount": pruned_count,
        "entries": final_entries,
    }

    if quota_blocked_until:
        index["quotaBlockedUntil"] = quota_blocked_until
        index["quotaBlockedReason"] = "gemini_free_tier_daily_tts_limit"

    (audio_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"DR Audio dual-voice ready: {latest_female_ready}/{len(latest)} "
        f"newest female voices ready; "
        f"{latest_male_ready}/{len(latest)} newest male voices ready; "
        f"{generated_count} voice files generated this run "
        f"({female_generated} female, {male_generated} male); "
        f"{pending_reused_count} unpublished cached voice files reused; "
        f"{len(deferred_timeout_tasks)} voice files deferred after timeout; "
        f"{backfilled_count} historical voice files backfilled across "
        f"{backfill_articles_selected} selected historical article(s); "
        f"{len(final_entries)} total article entries preserved; "
        f"{pruned_count} oldest entries pruned; "
        f"library cap={MAX_LIBRARY_ENTRIES}.",
        flush=True,
    )


if __name__ == "__main__":
    main()
