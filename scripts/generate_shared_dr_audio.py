#!/usr/bin/env python3
"""Generate shared DR Audio files for the newest Blogger articles."""
from __future__ import annotations

import argparse
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

BLOG_FEED = "https://draccesoriosrd.blogspot.com/feeds/posts/default?alt=json&max-results=10"
PUBLIC_INDEX = "https://dr-accesorios-rd.web.app/dr-audio/index.json"
MODEL = "gemini-3.8-flash-lite-tts"
VOICE = "Achernar"
LANGUAGE = "es"
STYLE = (
    "Presentadora profesional de noticias tecnológicas para público latinoamericano. "
    "Voz femenina adulta joven, cálida, clara y natural; español latino neutro; "
    "ritmo conversacional ligeramente ágil, sin correr; entonación periodística moderna, "
    "serena y cercana; pausas breves y naturales; evita por completo el tono de GPS, "
    "asistente virtual, anuncio comercial o voz institucional; pronuncia marcas, siglas, "
    "números y modelos tecnológicos con claridad."
)
USER_AGENT = "DRAccesoriosRD-AudioGenerator/1.0"


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


def parse_feed() -> list[dict[str, Any]]:
    response = requests.get(
        BLOG_FEED,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
        timeout=30,
    )
    response.raise_for_status()
    root = response.json()
    entries = ((root.get("feed") or {}).get("entry") or [])
    result: list[dict[str, Any]] = []

    for entry in entries:
        title = str(((entry.get("title") or {}).get("$t") or "")).strip()
        body_html = str(
            ((entry.get("content") or {}).get("$t"))
            or ((entry.get("summary") or {}).get("$t"))
            or ""
        )
        url = ""
        for link in entry.get("link") or []:
            if link.get("rel") == "alternate":
                url = str(link.get("href") or "").strip()
                break
        if not title or not url or not body_html:
            continue

        body_text = canonical_text(body_html)
        if not body_text:
            continue

        published = str(((entry.get("published") or {}).get("$t") or "")).strip()
        transcript = f"{title}. {body_text}".strip()
        content_hash = hashlib.sha256(
            (MODEL + "\n" + VOICE + "\n" + LANGUAGE + "\n" + transcript).encode("utf-8")
        ).hexdigest()
        audio_id = content_hash[:28]

        result.append(
            {
                "title": title,
                "url": url,
                "published": published,
                "text": transcript,
                "contentHash": content_hash,
                "audioId": audio_id,
            }
        )

    return result[:10]


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
    except requests.RequestException:
        return {"entries": []}


def audio_bytes_from_response(response: Any) -> bytes:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                inline = getattr(part, "inlineData", None)
            if inline is None:
                continue
            data = getattr(inline, "data", None)
            if isinstance(data, (bytes, bytearray)) and data:
                return bytes(data)
    raise RuntimeError("Gemini did not return audio bytes")


def generate_wav(client: genai.Client, transcript: str) -> bytes:
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            {
                "role": "user",
                "parts": [
                    {
                        "text": transcript,
                        "speech_metadata": {"style": STYLE},
                    }
                ],
            }
        ],
        config={
            "response_modalities": ["AUDIO"],
            "speech_config": {"voice_config": {"voice": VOICE}},
        },
    )
    return audio_bytes_from_response(response)


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dr_audio_build")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY_DR_AUDIO", "").strip()
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY_DR_AUDIO is missing. Add it as a GitHub Actions secret before running."
        )

    output = Path(args.output)
    audio_dir = output / "dr-audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    feed = parse_feed()
    if not feed:
        raise RuntimeError("Blogger feed returned no usable posts")

    live_index = load_live_index()
    old_entries = {
        str(entry.get("url") or ""): entry
        for entry in (live_index.get("entries") or [])
        if isinstance(entry, dict) and entry.get("url")
    }

    client = genai.Client(api_key=api_key)
    final_entries: list[dict[str, Any]] = []

    for position, article in enumerate(feed):
        old = old_entries.get(article["url"])
        expected_audio_path = f"/dr-audio/{article['audioId']}.m4a"
        public_audio_url = f"https://dr-accesorios-rd.web.app{expected_audio_path}"

        reusable = bool(
            old
            and old.get("contentHash") == article["contentHash"]
            and old.get("audioUrl")
        )

        if position < 5 and not reusable:
            print(f"Generating DR Audio {position + 1}/5: {article['title']}", flush=True)
            wav = generate_wav(client, article["text"])
            m4a_path = audio_dir / f"{article['audioId']}.m4a"
            wav_to_m4a(wav, m4a_path)
            final_entries.append(
                {
                    "title": article["title"],
                    "url": article["url"],
                    "published": article["published"],
                    "audioUrl": public_audio_url,
                    "audioPath": expected_audio_path,
                    "contentHash": article["contentHash"],
                    "model": MODEL,
                    "voice": VOICE,
                    "format": "audio/mp4",
                    "bytes": m4a_path.stat().st_size,
                }
            )
        elif reusable:
            entry = dict(old)
            entry.update(
                {
                    "title": article["title"],
                    "url": article["url"],
                    "published": article["published"],
                    "contentHash": article["contentHash"],
                }
            )
            final_entries.append(entry)
            print(f"Reusing shared audio: {article['title']}", flush=True)
        elif position < 5:
            raise RuntimeError("Unexpected generation state for a required article")

    required_urls = {entry["url"] for entry in final_entries[:5]}
    if len(required_urls) < min(5, len(feed)):
        raise RuntimeError("Not all newest articles have shared audio")

    index = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "voice": VOICE,
        "latestCount": min(5, len(feed)),
        "entries": final_entries[:10],
    }

    (audio_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Prepared {len(final_entries[:5])} newest shared audio entries; "
        f"catalog contains {len(final_entries[:10])} entries.",
        flush=True,
    )


if __name__ == "__main__":
    main()
