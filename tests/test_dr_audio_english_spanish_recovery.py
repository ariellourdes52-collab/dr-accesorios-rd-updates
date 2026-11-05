"""Offline regression: recovering cached Spanish audio never re-spends Gemini quota."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generate_shared_dr_audio as es


class SpanishPendingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.article = es.parse_entry({
            "title": {"$t": "Nueva noticia DR Audio"},
            "content": {"$t": "Noticia tecnologica validada para narracion."},
            "published": {"$t": datetime.now(timezone.utc).isoformat()},
            "link": [{"rel": "alternate", "href": "https://example.com/new"}],
        })
        assert self.article
        self.live = {
            "schemaVersion": 3,
            "entries": [{
                "title": "Artículo anterior",
                "url": "https://example.com/old",
                "published": datetime.now(timezone.utc).isoformat(),
                "audioFemalePath": "/dr-audio/old.m4a",
                "audioFemaleUrl": es.PUBLIC_BASE + "/dr-audio/old.m4a",
            }],
        }

    def exercise(self, *, cooldown=False, cached=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chunks = root / "chunks"
            chunks.mkdir()
            attempted = datetime.now(timezone.utc) - timedelta(minutes=2)
            (chunks / "last-tts-attempt.json").write_text(
                json.dumps({"attemptedAt": attempted.isoformat()}), encoding="utf-8")
            out = root / "output"
            assets = out / "dr-audio"
            assets.mkdir(parents=True)
            cached_file = assets / (self.article["audioIds"]["female"] + ".m4a")
            if cached:
                cached_file.write_bytes(b"0" * 2048)
            block = datetime.now(timezone.utc) + timedelta(hours=1)
            env = {
                "GEMINI_API_KEY_DR_AUDIO": "offline-test-only",
                "DR_AUDIO_CHUNK_CACHE_DIR": str(chunks),
                "DR_AUDIO_STATE_DIR": str(root / "state"),
                "EVENT_NAME": "schedule",
            }
            with (patch.dict(os.environ, env),
                  patch.object(sys, "argv", ["spanish", "--output", str(out)]),
                  patch.object(es, "parse_feed", return_value=[self.article]),
                  patch.object(es, "load_live_index", return_value=self.live),
                  patch.object(es, "public_audio_exists", return_value=True),
                  patch.object(es, "active_daily_quota_block_until",
                               return_value=block if cooldown else None),
                  patch.object(es, "local_daily_quota_block_until", return_value=None),
                  patch.object(es.genai, "Client") as client,
                  patch.object(es, "generate_wav") as synthesis):
                es.main()
                synthesis.assert_not_called()
            index = json.loads((assets / "index.json").read_text(encoding="utf-8"))
            if cached:
                recovered = next(x for x in index["entries"]
                                 if x["url"] == self.article["url"])
                self.assertEqual(recovered["audioFemalePath"],
                                 "/dr-audio/" + cached_file.name)
                self.assertEqual(recovered["femaleBytes"], 2048)
            else:
                self.assertEqual(index, self.live)
            self.assertEqual(len(list(assets.glob("*.m4a"))), 1 if cached else 0)
            self.assertEqual(
                json.loads((chunks / "last-tts-attempt.json").read_text())["attemptedAt"],
                attempted.isoformat(),
            )

    def test_cached_audio_publishes_during_eight_minute_guard(self):
        self.exercise(cooldown=False, cached=True)

    def test_cached_audio_publishes_during_daily_gemini_cooldown(self):
        self.exercise(cooldown=True, cached=True)

    def test_guard_without_cached_audio_keeps_catalog_and_does_not_call_tts(self):
        self.exercise(cooldown=False, cached=False)

    def test_quota_without_cached_audio_keeps_catalog_and_does_not_call_tts(self):
        self.exercise(cooldown=True, cached=False)


if __name__ == "__main__":
    unittest.main()
