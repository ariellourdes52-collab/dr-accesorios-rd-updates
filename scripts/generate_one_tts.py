#!/usr/bin/env python3
"""Generate exactly one Gemini TTS WAV in an isolated process."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from google import genai

MODEL = "gemini-3.8-flash-lite-tts"
REQUEST_TIMEOUT_MS = 55_000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = json.load(sys.stdin)
    transcript = str(payload.get("transcript") or "")
    voice_name = str(payload.get("voice_name") or "")
    voice_style = str(payload.get("voice_style") or "")

    if not transcript.strip():
        raise RuntimeError("TTS transcript is empty")
    if not voice_name.strip():
        raise RuntimeError("TTS voice name is empty")
    if not voice_style.strip():
        raise RuntimeError("TTS voice style is empty")

    api_key = os.environ.get("GEMINI_API_KEY_DR_AUDIO", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY_DR_AUDIO is missing")

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
                                "style": voice_style,
                            }
                        ],
                    }
                ],
            }
        ],
        response_format={"type": "audio"},
        generation_config={
            "speech_config": [
                {"voice": voice_name},
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
