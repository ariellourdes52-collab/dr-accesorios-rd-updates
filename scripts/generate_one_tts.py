#!/usr/bin/env python3
"""Generate exactly one Gemini TTS WAV in an isolated streaming process."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import wave
from pathlib import Path

from google import genai

DEFAULT_MODEL = "gemini-3.8-flash-lite-tts"
REQUEST_TIMEOUT_MS = 600_000
SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--heartbeat", default="")
    args = parser.parse_args()

    payload = json.load(sys.stdin)
    transcript = str(payload.get("transcript") or "")
    voice_name = str(payload.get("voice_name") or "")
    voice_style = str(payload.get("voice_style") or "")
    model = str(payload.get("model") or DEFAULT_MODEL).strip()
    stream_audio = bool(payload.get("stream", True))

    if not transcript.strip():
        raise RuntimeError("TTS transcript is empty")
    if not voice_name.strip():
        raise RuntimeError("TTS voice name is empty")
    if not voice_style.strip():
        raise RuntimeError("TTS voice style is empty")
    if not model:
        raise RuntimeError("TTS model is empty")

    api_key = os.environ.get("GEMINI_API_KEY_DR_AUDIO", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_DR_AUDIO is missing")

    heartbeat_path = Path(args.heartbeat) if args.heartbeat else None

    def heartbeat() -> None:
        if heartbeat_path is not None:
            heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            heartbeat_path.write_text(str(time.time_ns()), encoding="utf-8")

    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": REQUEST_TIMEOUT_MS},
    )

    request_input = [
        {
            "type": "user_input",
            "content": [
                {
                    "type": "text",
                    "text": transcript,
                    "annotations": [
                        {
                            "type": "speech_metadata",
                            "style": voice_style,
                        }
                    ],
                }
            ],
        }
    ]
    speech_config = {
        "speech_config": [
            {"voice": voice_name},
        ]
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    heartbeat()

    if not stream_audio:
        interaction = client.interactions.create(
            model=model,
            input=request_input,
            response_format={"type": "audio"},
            generation_config=speech_config,
        )

        output_audio = getattr(interaction, "output_audio", None)
        data = getattr(output_audio, "data", None)
        if not data:
            raise RuntimeError("Gemini unary TTS returned no audio")

        if isinstance(data, (bytes, bytearray)):
            wav_bytes = bytes(data)
        else:
            wav_bytes = base64.b64decode(data)

        if not wav_bytes:
            raise RuntimeError("Gemini unary TTS returned empty audio")

        if wav_bytes[:4] == b"RIFF":
            output_path.write_bytes(wav_bytes)
        else:
            with wave.open(str(output_path), "wb") as writer:
                writer.setnchannels(CHANNELS)
                writer.setsampwidth(SAMPLE_WIDTH)
                writer.setframerate(SAMPLE_RATE)
                writer.writeframes(wav_bytes)

        heartbeat()
        return

    stream = client.interactions.create(
        model=model,
        input=request_input,
        response_format={
            "type": "audio",
            "mime_type": "audio/l16",
            "sample_rate": SAMPLE_RATE,
        },
        generation_config=speech_config,
        stream=True,
    )

    pcm = bytearray()

    for event in stream:
        if getattr(event, "event_type", "") != "step.delta":
            continue

        delta = getattr(event, "delta", None)
        if getattr(delta, "type", "") != "audio":
            continue

        data = getattr(delta, "data", None)
        if not data:
            continue

        if isinstance(data, (bytes, bytearray)):
            audio_bytes = bytes(data)
        else:
            audio_bytes = base64.b64decode(data)

        if not audio_bytes:
            continue

        pcm.extend(audio_bytes)
        heartbeat()

    if not pcm:
        raise RuntimeError("Gemini streaming TTS returned no audio")

    with wave.open(str(output_path), "wb") as writer:
        writer.setnchannels(CHANNELS)
        writer.setsampwidth(SAMPLE_WIDTH)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(bytes(pcm))

    heartbeat()


if __name__ == "__main__":
    main()
