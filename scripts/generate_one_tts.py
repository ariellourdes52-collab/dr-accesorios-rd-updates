#!/usr/bin/env python3
"""Generate one Gemini TTS WAV in an isolated process."""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from google import genai

MODEL = "gemini-3.8-flash-lite-tts"
REQUEST_TIMEOUT_MS = 120_000

VOICE_CONFIGS = {
    "female": {
        "name": "Achernar",
        "style": (
            "Natural Latin American Spanish female news presenter. "
            "Warm, clear, professional, neutral accent, steady pace."
        ),
    },
    "male": {
        "name": "Charon",
        "style": (
            "Natural Latin American Spanish male news presenter. "
            "Warm, clear, professional, neutral accent, steady pace."
        ),
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", choices=sorted(VOICE_CONFIGS), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    transcript = sys.stdin.read()
    if not transcript.strip():
        raise RuntimeError("TTS transcript is empty")

    api_key = os.environ.get("GEMINI_API_KEY_DR_AUDIO", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_DR_AUDIO is missing")

    voice = VOICE_CONFIGS[args.voice]
    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": REQUEST_TIMEOUT_MS},
    )

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
        wav = bytes(data)
    else:
        wav = base64.b64decode(data)

    Path(args.output).write_bytes(wav)


if __name__ == "__main__":
    main()
