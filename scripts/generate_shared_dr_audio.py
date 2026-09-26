#!/usr/bin/env python3
"""Generate and preserve the shared DR Audio library for Blogger articles."""
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
VOICE = "Achernar"
LANGUAGE = "es"
LATEST_REQUIRED = 5
MAX_BACKFILL_PER_RUN = 5
PAGE_SIZE = 50
STYLE = (
    "Presentadora profesional de noticias tecnológicas para público latinoamericano. "
    "Voz femenina adulta joven, cálida, clara y natural; español latino neutro; "
    "ritmo conversacional ligeramente ágil, sin correr; entonación periodística moderna, "
    "serena y cercana; pausas breves y naturales; evita por completo el tono de GPS, "
    "asistente virtual, anuncio comercial o voz institucional; pronuncia marcas, siglas, "
    "números y modelos tecnológicos con claridad."
)
USER_AGENT = "DRAccesoriosRD-AudioGenerator/2.0"


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
    content_hash = hashlib.sha256(
        (MODEL + "\n" + VOICE + "\n" + LANGUAGE + "\n" + transcript).encode("utf-8")
    ).hexdigest()

    return {
        "title": title,
        "url": url,
        "published": published,
        "text": transcript,
        "contentHash": content_hash,
        "audioId": content_hash[:28],
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


def generate_wav(client: genai.Client, transcript: str) -> bytes:
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
                                "style": STYLE,
                            }
                        ],
                    }
                ],
            }
        ],
        response_format={"type": "audio"},
        generation_config={
            "speech_config": [
                {"voice": VOICE},
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


def make_index_entry(article: dict[str, Any], m4a_path: Path) -> dict[str, Any]:
    audio_path = f"/dr-audio/{article['audioId']}.m4a"
    return {
        "title": article["title"],
        "url": article["url"],
        "published": article["published"],
        "audioUrl": PUBLIC_BASE + audio_path,
        "audioPath": audio_path,
        "contentHash": article["contentHash"],
        "model": MODEL,
        "voice": VOICE,
        "format": "audio/mp4",
        "bytes": m4a_path.stat().st_size,
    }


def reusable_entry(old: dict[str, Any] | None, article: dict[str, Any]) -> bool:
    return bool(
        old
        and old.get("contentHash") == article["contentHash"]
        and old.get("audioUrl")
        and old.get("audioPath")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dr_audio_build")
    parser.add_argument("--scan-posts", type=int, default=500)
    parser.add_argument("--backfill-count", type=int, default=0)
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
        if isinstance(entry, dict) and entry.get("url") and entry.get("audioUrl")
    ]

    # Preserve every historical audio entry already published, even if it is older
    # than the current Blogger scan window.
    final_by_url: dict[str, dict[str, Any]] = {
        canonical_url(str(entry.get("url") or "")): dict(entry)
        for entry in old_entries_list
        if canonical_url(str(entry.get("url") or ""))
    }

    old_by_url = dict(final_by_url)
    client = genai.Client(api_key=api_key)

    latest = feed[:LATEST_REQUIRED]
    generate_urls: list[str] = []

    # The newest five must always be available. Only missing or changed items generate.
    for article in latest:
        old = old_by_url.get(article["url"])
        if not reusable_entry(old, article):
            generate_urls.append(article["url"])

    # Backfill older history gradually. This is deliberately capped so scheduled
    # runs cannot burn through the project quota.
    if backfill_count:
        for article in feed[LATEST_REQUIRED:]:
            if len(generate_urls) >= (len([u for u in generate_urls if u in {a["url"] for a in latest}]) + backfill_count):
                break
            old = old_by_url.get(article["url"])
            if not reusable_entry(old, article):
                generate_urls.append(article["url"])

    generate_set = set(generate_urls)
    generated_count = 0
    backfilled_count = 0
    latest_urls = {article["url"] for article in latest}

    for article in feed:
        old = old_by_url.get(article["url"])

        if article["url"] in generate_set:
            role = "latest" if article["url"] in latest_urls else "backfill"
            print(
                f"Generating DR Audio ({role}): {article['title']}",
                flush=True,
            )
            wav = generate_wav(client, article["text"])
            m4a_path = audio_dir / f"{article['audioId']}.m4a"
            wav_to_m4a(wav, m4a_path)
            final_by_url[article["url"]] = make_index_entry(article, m4a_path)
            generated_count += 1
            if role == "backfill":
                backfilled_count += 1
            # Gentle spacing helps keep preview/free-tier requests inside RPM limits.
            time.sleep(1.0)

        elif reusable_entry(old, article):
            entry = dict(old)
            entry.update(
                {
                    "title": article["title"],
                    "url": article["url"],
                    "published": article["published"],
                    "contentHash": article["contentHash"],
                }
            )
            final_by_url[article["url"]] = entry

        elif old:
            # Article text changed but this run did not have budget to regenerate it.
            # Do not serve stale narration for the edited article.
            final_by_url.pop(article["url"], None)

    missing_latest = [
        article["title"]
        for article in latest
        if article["url"] not in final_by_url
    ]
    if missing_latest:
        raise RuntimeError(
            "Newest DR Audio coverage is incomplete: " + " | ".join(missing_latest)
        )

    # ISO Blogger timestamps sort correctly as strings. Unknown legacy timestamps
    # stay at the end while remaining preserved.
    final_entries = sorted(
        final_by_url.values(),
        key=lambda entry: str(entry.get("published") or ""),
        reverse=True,
    )

    index = {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "voice": VOICE,
        "latestCount": min(LATEST_REQUIRED, len(feed)),
        "historyCount": len(final_entries),
        "entries": final_entries,
    }

    (audio_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"DR Audio ready: {len(latest)} newest protected; "
        f"{generated_count} generated this run; "
        f"{backfilled_count} historical backfilled; "
        f"{len(final_entries)} total shared entries preserved.",
        flush=True,
    )


if __name__ == "__main__":
    main()
