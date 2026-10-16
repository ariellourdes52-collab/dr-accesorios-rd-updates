#!/usr/bin/env python3
"""Optional DR Audio English female sidecar; preserves all Spanish audio."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from google import genai
from generate_shared_dr_audio import (
    MODEL, PUBLIC_BASE, LATEST_REQUIRED, RENDER_MODEL,
    canonical_url, generate_wav, load_live_index, parse_feed,
    wav_to_m4a, VOICE_CONFIGS,
)

VOICE_KEY = "english"
VOICE_NAME = VOICE_CONFIGS["female"]["name"]
TRANSLATION_MODEL = "gemini-3.5-flash-lite"
PUBLISH_CONFIRM = "GENERATE-DR-AUDIO-EN"
ENGLISH_STYLE = (
    "Professional female technology news presenter. Natural, clear American "
    "English; warm and conversational delivery, smooth newsroom cadence, "
    "human pauses, never GPS, advertising or robotic. Pronounce technology "
    "brands, acronyms, numbers and product names accurately."
)

def english_digest(transcript: str) -> str:
    return hashlib.sha256(
        (MODEL + "\n" + VOICE_NAME + "\nen\n" + transcript).encode("utf-8")
    ).hexdigest()

def verify_translation(original: str, translated: str) -> str:
    text = translated.strip()
    if not text or len(text) < max(90, int(len(original) * 0.40)):
        raise ValueError("English translation is empty or severely truncated")
    if len(text) > len(original) * 2.5:
        raise ValueError("English translation unexpectedly expanded")
    for name in ("AirTag", "Monster Hunter Wilds", "Nintendo Switch",
                 "WhatsApp", "Android", "iPhone"):
        if name.casefold() in original.casefold() and name.casefold() not in text.casefold():
            raise ValueError("Translation changed or dropped name: " + name)
    if re.search(r"\b(?:incauta|incautados|incautadas)\b", text, re.IGNORECASE):
        raise ValueError("Untranslated Spanish editorial wording")
    return text

def translate_article(client: genai.Client, spanish_text: str) -> str:
    prompt = (
        "Translate the following COMPLETE Spanish technology news article "
        "into fluent US English for spoken radio narration. Translate the "
        "headline AND entire article without summary, omissions, additions "
        "or markdown. Preserve all facts, dates, numbers, names, brand and "
        "product names precisely, including AirTag, Android, WhatsApp and "
        "Nintendo Switch. Render 'incauta' as 'seizes' when appropriate. "
        "Return ONLY English narration text.\n\n" + spanish_text
    )
    response = client.models.generate_content(
        model=TRANSLATION_MODEL, contents=prompt,
        config={"temperature": 0.1, "max_output_tokens": 8192},
    )
    return verify_translation(spanish_text, str(response.text or ""))

def existing_audio(entry: dict) -> bool:
    return bool(str(entry.get("audioEnglishUrl") or "").strip()
                and str(entry.get("audioEnglishPath") or "").strip())

def eligible_article(feed: list[dict], index: dict) -> tuple[dict, dict] | None:
    by_url = {
        canonical_url(str(e.get("url") or "")): e
        for e in (index.get("entries") or [])
        if isinstance(e, dict)
    }
    for article in feed[:LATEST_REQUIRED]:
        entry = by_url.get(article["url"])
        if not entry:
            continue
        # English waits until BOTH Spanish narrations already exist.
        if not entry.get("audioFemaleUrl") or not entry.get("audioMaleUrl"):
            continue
        if existing_audio(entry):
            continue
        return article, entry
    return None

def extend_index(index: dict, article: dict, transcript: str,
                 audio_path: Path) -> dict:
    digest = english_digest(transcript)
    path = "/dr-audio/" + digest[:28] + ".m4a"
    if audio_path.name != digest[:28] + ".m4a":
        raise ValueError("English audio path/hash mismatch")
    if audio_path.stat().st_size <= 1024:
        raise ValueError("English audio file too small")
    updated = json.loads(json.dumps(index))
    matched = 0
    for entry in updated.get("entries") or []:
        if canonical_url(str(entry.get("url") or "")) != article["url"]:
            continue
        if existing_audio(entry):
            raise RuntimeError("English already in catalog; no overwrite")
        entry.update({
            "audioEnglishUrl": PUBLIC_BASE + path,
            "audioEnglishPath": path,
            "englishContentHash": digest,
            "englishVoice": VOICE_NAME,
            "englishBytes": audio_path.stat().st_size,
            "englishRenderModel": RENDER_MODEL,
            "englishLanguage": "en",
        })
        matched += 1
    if matched != 1:
        raise RuntimeError("Expected exactly one existing article")
    updated["latestEnglishReady"] = sum(
        bool(e.get("audioEnglishUrl"))
        for e in (updated.get("entries") or [])[:LATEST_REQUIRED]
    )
    return updated

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dr_audio_english_build")
    parser.add_argument("--mode", choices=("scan", "generate"), default="scan")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.mode == "generate":
        if (args.confirm != PUBLISH_CONFIRM
                or os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("GITHUB_REF") != "refs/heads/main"):
            raise ValueError("Production confirmation on main required")

    index = load_live_index()
    if not index.get("entries"):
        raise RuntimeError("Live Spanish catalog missing")
    candidate = eligible_article(parse_feed(20), index)
    if candidate is None:
        print("English: waiting for Spanish voices or already finished")
        return
    article, entry = candidate
    print("English candidate:", article["title"])
    if args.mode == "scan":
        print("Scan only: no Gemini request, no deployment")
        return

    # Separate secret, English runs never take the Spanish key from env.
    key = os.environ.get("GEMINI_API_KEY_DR_AUDIO_EN", "").strip()
    if not key:
        raise RuntimeError("Separate English Gemini API key required")
    client = genai.Client(api_key=key)
    english = translate_article(client, article["text"])
    digest = english_digest(english)
    audio_dir = Path(args.output) / "dr-audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    filename = audio_dir / (digest[:28] + ".m4a")

    # Same model, same Achernar voice, same AAC compression as existing voice.
    VOICE_CONFIGS[VOICE_KEY] = {
        "name": VOICE_NAME, "label": "English", "style": ENGLISH_STYLE,
    }
    if not filename.exists() or filename.stat().st_size <= 1024:
        wav = generate_wav(client, english, VOICE_KEY)
        wav_to_m4a(wav, filename)

    latest = load_live_index()
    if latest != index:
        raise RuntimeError("Live index changed while generating; abort deployment")
    updated = extend_index(latest, article, english, filename)
    (audio_dir / "index.json").write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("English prepared:", filename.name, "Spanish catalog preserved")

if __name__ == "__main__":
    main()
