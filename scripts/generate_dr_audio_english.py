#!/usr/bin/env python3
"""Independent English DR Audio: reads Spanish catalog; writes ONLY English site files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from google import genai
from generate_shared_dr_audio import (
    MODEL, LATEST_REQUIRED, RENDER_MODEL, VOICE_CONFIGS, canonical_url,
    generate_wav, load_live_index, parse_feed, wav_to_m4a,
)

# Dedicated Firebase Hosting SITE, NOT the production Spanish Hosting site.
ENGLISH_SITE_ID = "dr-accesorios-rd-audio-en"
ENGLISH_PUBLIC_BASE = "https://" + ENGLISH_SITE_ID + ".web.app"
ENGLISH_INDEX = ENGLISH_PUBLIC_BASE + "/dr-audio/index.json"
TRANSLATION_MODEL = "gemini-3.5-flash-lite"
VOICE_KEY = "english"
VOICE_NAME = VOICE_CONFIGS["female"]["name"]
PUBLISH_CONFIRM = "GENERATE-DR-AUDIO-EN"
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


def translate_article(client: genai.Client, spanish_text: str) -> str:
    prompt = (
        "Translate this entire Spanish technology news article to fluent US "
        "English for natural spoken news narration. Keep all facts, brands, "
        "product names, dates, numbers, entire headline and article text; "
        "no omission, summary, commentary or markdown. Preserve AirTag and "
        "Nintendo Switch exactly, translate 'incauta' contextually as 'seizes'. "
        "Return only complete English narration.\n\n" + spanish_text
    )
    result = client.models.generate_content(
        model=TRANSLATION_MODEL,
        contents=prompt,
        config={"temperature": 0.1, "max_output_tokens": 8192},
    )
    return verify_translation(spanish_text, str(result.text or ""))


def eligible_article(
    feed: list[dict], spanish_index: dict, english_index: dict
) -> dict | None:
    # Spanish is READ ONLY, used only to ensure that both existing voices
    # receive priority, and that these articles already exist in the library.
    spanish_by_url = {
        canonical_url(str(e.get("url") or "")): e
        for e in spanish_index.get("entries", []) if isinstance(e, dict)
    }
    existing_en_urls = {
        canonical_url(str(e.get("url") or ""))
        for e in english_index.get("entries", []) if isinstance(e, dict)
    }
    for article in feed[:LATEST_REQUIRED]:
        if article["url"] in existing_en_urls:
            continue
        spanish = spanish_by_url.get(article["url"])
        if not spanish:
            continue
        if not spanish.get("audioFemaleUrl") or not spanish.get("audioMaleUrl"):
            continue
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
    args = parser.parse_args()

    if args.mode == "generate" and (
        args.confirm != PUBLISH_CONFIRM
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise RuntimeError("Manual production confirmation from main required")

    spanish = load_live_index()  # GET only! Never publishes or writes ES.
    if not spanish.get("entries"):
        raise RuntimeError("Spanish catalog unavailable; skip English")
    english = load_english_index()
    article = eligible_article(parse_feed(20), spanish, english)
    if article is None:
        print("No eligible English news; Spanish and English data unchanged.")
        return
    print("English candidate:", article["title"])
    if args.mode == "scan":
        print("SCAN ONLY, no TTS, translation or Firebase writes")
        return

    key = os.environ.get("GEMINI_API_KEY_DR_AUDIO_EN", "").strip()
    if not key:
        raise RuntimeError("The dedicated English API key is missing")
    client = genai.Client(api_key=key)
    transcript = translate_article(client, article["text"])
    audio_dir = Path(args.output) / "dr-audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    file_path = audio_dir / (english_digest(transcript)[:28] + ".m4a")
    VOICE_CONFIGS[VOICE_KEY] = {
        "name": VOICE_NAME, "style": ENGLISH_STYLE, "label": "English"
    }
    if not file_path.exists() or file_path.stat().st_size <= 1024:
        wav_to_m4a(generate_wav(client, transcript, VOICE_KEY), file_path)

    # Re-read only the English catalog before publication preparation:
    # never overwrite English records created during generation.
    current_en = load_english_index()
    if current_en != english:
        raise RuntimeError("English catalog changed; fail closed")
    updated = extend_index(current_en, article, transcript, file_path)
    (audio_dir / "index.json").write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("New English file prepared; no change to Spanish Hosting.")

if __name__ == "__main__":
    main()
