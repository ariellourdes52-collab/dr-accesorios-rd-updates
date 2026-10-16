#!/usr/bin/env python3
"""Independent English DR Audio: reads Spanish catalog; writes ONLY English site files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from google import genai
from generate_shared_dr_audio import (
    MODEL, LATEST_REQUIRED, RENDER_MODEL, VOICE_CONFIGS, canonical_url,
    generate_wav, load_live_index, parse_feed, wav_to_m4a,
    quota_exhausted_error, daily_quota_exhausted_error,
    next_gemini_daily_reset_iso, parse_blocked_until, tts_timeout_error,
)

# Dedicated Firebase Hosting SITE, NOT the production Spanish Hosting site.
ENGLISH_SITE_ID = "dr-accesorios-rd-audio-en"
ENGLISH_PUBLIC_BASE = "https://" + ENGLISH_SITE_ID + ".web.app"
ENGLISH_INDEX = ENGLISH_PUBLIC_BASE + "/dr-audio/index.json"
TRANSLATION_MODEL = "gemini-3.5-flash-lite"
VOICE_KEY = "english"
VOICE_NAME = VOICE_CONFIGS["female"]["name"]
PUBLISH_CONFIRM = "GENERATE-DR-AUDIO-EN"
ENGLISH_QUOTA_STATE = Path(".dr-audio-english-state/gemini-daily-quota.json")
ENGLISH_TTS_ATTEMPT_STATE = Path(".dr-audio-english-state/last-tts-attempt.json")
ENGLISH_MIN_TTS_INTERVAL_SECONDS = 8 * 60
TEMPORARY_QUOTA_COOLDOWN_MINUTES = 20
ENGLISH_STYLE = (
    "Professional female technology news presenter. Natural, clear American "
    "English; warm and conversational delivery, smooth newsroom cadence and "
    "human pauses; never GPS, advertising or robotic. Pronounce technology "
    "brands, acronyms, numbers and product names accurately."
)


def load_english_index() -> dict[str, Any]:
    response = requests.get(
        ENGLISH_INDEX,
        params={"live": "1"},
        headers={"Cache-Control": "no-cache"},
        timeout=25,
    )
    if response.status_code == 404:
        # Brand-new English site may not have an index yet.
        return {"schemaVersion": 1, "language": "en", "entries": []}
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise RuntimeError("English catalog invalid; refusing to overwrite")
    if value.get("language") != "en":
        raise RuntimeError("English catalog language mismatch")
    return value



def english_quota_blocked_until(state_path: Path = ENGLISH_QUOTA_STATE) -> datetime | None:
    """Separate from .dr-audio-state used by Spanish. No cross-language cooldown."""
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return parse_blocked_until(str(state.get("blockedUntil") or ""))


def pause_english_quota(error: Exception, state_path: Path = ENGLISH_QUOTA_STATE) -> bool:
    """On English-key 429, stop retry storms; never write Spanish quota state."""
    if not quota_exhausted_error(error):
        return False
    daily = daily_quota_exhausted_error(error)
    blocked_until = (
        next_gemini_daily_reset_iso() if daily
        else (datetime.now(timezone.utc) +
              timedelta(minutes=TEMPORARY_QUOTA_COOLDOWN_MINUTES)).isoformat()
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "blockedUntil": blocked_until,
        "reason": "english_gemini_daily_quota" if daily else "english_gemini_rate_limit",
        "key": "GEMINI_API_KEY_DR_AUDIO_EN",
    }, indent=2) + "\n", encoding="utf-8")
    (state_path.parent / ".new-quota").write_text("new\n", encoding="utf-8")
    print(
        f"English-only Gemini quota cooldown until {blocked_until}; "
        "will retry automatically. Spanish quota and audio are untouched.",
        flush=True,
    )
    return True



def english_recent_tts_attempt(
    state_path: Path = ENGLISH_TTS_ATTEMPT_STATE,
    now: datetime | None = None,
) -> bool:
    """Only the English cooldown cache is read. A delayed duplicate cron skips."""
    if not state_path.exists():
        return False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        raw = str(data.get("attemptedAt") or "")
        attempted = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if attempted.tzinfo is None:
            attempted = attempted.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        age = (current - attempted.astimezone(timezone.utc)).total_seconds()
        return 0 <= age < ENGLISH_MIN_TTS_INTERVAL_SECONDS
    except (OSError, ValueError, TypeError):
        return False


def mark_english_tts_attempt(
    state_path: Path = ENGLISH_TTS_ATTEMPT_STATE,
    now: datetime | None = None,
) -> None:
    """Prevent duplicate English TTS billing without touching Spanish state."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    current = now or datetime.now(timezone.utc)
    state_path.write_text(
        json.dumps({"attemptedAt": current.astimezone(timezone.utc).isoformat()},
                   indent=2) + "\n",
        encoding="utf-8",
    )


def english_digest(transcript: str) -> str:
    return hashlib.sha256(
        (MODEL + "\n" + VOICE_NAME + "\nen\n" + transcript).encode("utf-8")
    ).hexdigest()


def verify_translation(original: str, translated: str) -> str:
    text = translated.strip()
    if not text or len(text) < max(90, int(len(original) * 0.40)):
        raise ValueError("English translation empty or truncated")
    if len(text) > len(original) * 2.5:
        raise ValueError("English translation unexpectedly expanded")
    for name in ("AirTag", "Monster Hunter Wilds", "Nintendo Switch",
                 "WhatsApp", "Android", "iPhone"):
        if name.casefold() in original.casefold() and name.casefold() not in text.casefold():
            raise ValueError("Translation modified/dropped a brand name: " + name)
    if re.search(r"\b(?:incauta|incautados|incautadas)\b", text, re.I):
        raise ValueError("Spanish words remained in translated article")
    return text


def retry_english_503(operation, *, max_attempts: int = 3):
    """Bounded retry for transient Gemini 503 only; never retry 429/quota.

    Keep the English-only workflow independent. No retry on malformed
    translations, invalid credentials, or permanent Gemini errors.
    """
    if max_attempts < 1 or max_attempts > 3:
        raise ValueError("English 503 retry attempts must be between 1 and 3")
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as error:
            if getattr(error, "status_code", None) != 503 or attempt == max_attempts:
                raise
            delay = 5 if attempt == 1 else 15
            print(
                f"English Gemini 503 transient demand; retry "
                f"{attempt + 1}/{max_attempts} after {delay}s.",
                flush=True,
            )
            time.sleep(delay)


def translate_article(client: genai.Client, spanish_text: str) -> str:
    prompt = (
        "Translate this entire Spanish technology news article to fluent US "
        "English for natural spoken news narration. Keep all facts, brands, "
        "product names, dates, numbers, entire headline and article text; "
        "no omission, summary, commentary or markdown. Preserve AirTag and "
        "Nintendo Switch exactly, translate 'incauta' contextually as 'seizes'. "
        "Return only complete English narration.\n\n" + spanish_text
    )
    result = retry_english_503(lambda: client.models.generate_content(
        model=TRANSLATION_MODEL,
        contents=prompt,
        config={"temperature": 0.1, "max_output_tokens": 8192},
    ))
    return verify_translation(spanish_text, str(result.text or ""))


def eligible_article(
    feed: list[dict], spanish_index: dict, english_index: dict,
    historical_backfill: bool = False,
) -> dict | None:
    # Spanish index is read-only. Both Spanish voices MUST exist first.
    spanish_by_url = {
        canonical_url(str(e.get("url") or "")): e
        for e in spanish_index.get("entries", []) if isinstance(e, dict)
    }
    existing_en_urls = {
        canonical_url(str(e.get("url") or ""))
        for e in english_index.get("entries", []) if isinstance(e, dict)
    }

    def spanish_ready(article: dict) -> bool:
        spanish = spanish_by_url.get(canonical_url(article["url"]), {})
        return bool(spanish.get("audioFemaleUrl") and spanish.get("audioMaleUrl"))

    latest = feed[:LATEST_REQUIRED]
    for article in latest:
        if canonical_url(article["url"]) not in existing_en_urls and spanish_ready(article):
            return article

    # Historical English fill is optional and LOW PRIORITY. Never backfill
    # while any newest article is awaiting English or the Spanish voices.
    if not historical_backfill or any(
        canonical_url(article["url"]) not in existing_en_urls for article in latest
    ):
        return None
    for article in feed[LATEST_REQUIRED:]:
        if canonical_url(article["url"]) not in existing_en_urls and spanish_ready(article):
            return article
    return None


def extend_index(index: dict, article: dict, english_text: str, audio_path: Path) -> dict:
    digest = english_digest(english_text)
    name = digest[:28] + ".m4a"
    if audio_path.name != name or audio_path.stat().st_size <= 1024:
        raise ValueError("English audio file is missing or content hash disagrees")
    updated = json.loads(json.dumps(index))
    if any(canonical_url(str(e.get("url") or "")) == article["url"]
           for e in updated["entries"]):
        raise RuntimeError("English audio for this article is already published")
    path = "/dr-audio/" + name
    updated["entries"].insert(0, {
        "url": article["url"],
        "title": article["title"],
        "published": article["published"],
        "audioEnglishUrl": ENGLISH_PUBLIC_BASE + path,
        "audioEnglishPath": path,
        "englishContentHash": digest,
        "englishVoice": VOICE_NAME,
        "englishBytes": audio_path.stat().st_size,
        "englishRenderModel": RENDER_MODEL,
        "englishLanguage": "en",
    })
    updated["language"] = "en"
    updated["schemaVersion"] = 1
    return updated



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dr_audio_english_build")
    parser.add_argument("--mode", choices=("scan", "generate"), default="scan")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--scan-posts", type=int, default=20)
    parser.add_argument("--historical-backfill", type=int, choices=(0, 1), default=0)
    parser.add_argument("--opportunistic-backfill", action="store_true")
    args = parser.parse_args()

    event = os.environ.get("EVENT_NAME", "").strip()
    scheduler_guard = os.environ.get("SCHEDULER_GUARD", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    authorized = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("GITHUB_REF") == "refs/heads/main"
        and (
            event in {"schedule", "push"}
            or (event == "workflow_dispatch" and args.confirm == PUBLISH_CONFIRM)
        )
    )
    if args.mode == "generate" and not authorized:
        raise RuntimeError("English generation requires schedule from main or manual confirmation")

    audio_dir = Path(args.output) / "dr-audio"
    if args.mode == "generate":
        # A previous failed release can restore old M4As, but never republish its
        # stale index.json if today's scan decides to skip generation.
        (audio_dir / "index.json").unlink(missing_ok=True)
        blocked = english_quota_blocked_until()
        if blocked:
            print(f"English Gemini cooldown active until {blocked.isoformat()}; no TTS.")
            return

    spanish = load_live_index()  # GET only, never publish or mutate Spanish.
    if not spanish.get("entries"):
        raise RuntimeError("Spanish catalog unavailable; skip English")
    english = load_english_index()
    feed = parse_feed(args.scan_posts)
    historical = bool(args.historical_backfill)
    article = eligible_article(feed, spanish, english, historical_backfill=historical)
    if article is None:
        print("No eligible English news; Spanish and English data unchanged.")
        return
    role = "historical" if article not in feed[:LATEST_REQUIRED] else "latest"
    print(f"English {role} candidate:", article["title"])
    if args.mode == "scan":
        print("SCAN ONLY, no Gemini calls or Firebase writes")
        return

    key = os.environ.get("GEMINI_API_KEY_DR_AUDIO_EN", "").strip()
    if not key:
        raise RuntimeError("The dedicated English API key is missing")
    if (event == "schedule" or scheduler_guard) and english_recent_tts_attempt():
        print(
            "English scheduler guard: TTS was attempted in the previous 8 minutes; "
            "skipping duplicate attempt. Spanish unaffected.",
            flush=True,
        )
        return
    # The eight-minute guard is for synthesis only: translating text cannot
    # incur TTS duplication. Mark only immediately before an actual TTS call.
    client = genai.Client(api_key=key)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Keep a translation of a failed English release for a retry. The cache
    # key includes the original content, so changed articles never reuse it.
    source_digest = hashlib.sha256(
        (article["url"] + "\n" + article["text"]).encode("utf-8")
    ).hexdigest()[:28]
    transcript_path = audio_dir / (source_digest + ".en.txt")
    if transcript_path.is_file():
        transcript = verify_translation(
            article["text"], transcript_path.read_text(encoding="utf-8")
        )
        print("Reusing previously prepared English translation.")
    else:
        try:
            transcript = translate_article(client, article["text"])
        except Exception as error:
            if pause_english_quota(error) or tts_timeout_error(error):
                print("English translation deferred; Spanish unaffected.")
                return
            raise
        transcript_path.write_text(transcript, encoding="utf-8")

    file_path = audio_dir / (english_digest(transcript)[:28] + ".m4a")
    VOICE_CONFIGS[VOICE_KEY] = {
        "name": VOICE_NAME, "style": ENGLISH_STYLE, "label": "English"
    }
    if file_path.exists() and file_path.stat().st_size > 1024:
        print("Reusing English M4A cached from an unsuccessful release.")
    else:
        try:
            mark_english_tts_attempt()
            wav = retry_english_503(
                lambda: generate_wav(client, transcript, VOICE_KEY),
                max_attempts=2,
            )
        except Exception as error:
            if pause_english_quota(error) or tts_timeout_error(error):
                print("English TTS deferred; Spanish unaffected.")
                return
            raise
        wav_to_m4a(wav, file_path)

    # Re-check the English catalog immediately before preparing the overlay.
    # Any concurrent English publication fails closed, never overwrites history.
    current_en = load_english_index()
    if current_en != english:
        raise RuntimeError("English catalog changed; fail closed")
    updated = extend_index(current_en, article, transcript, file_path)
    (audio_dir / "index.json").write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("ONE new English file prepared; Spanish generator, quota and Hosting unchanged.")

if __name__ == "__main__":
    main()
