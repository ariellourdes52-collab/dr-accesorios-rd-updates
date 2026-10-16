"""Offline regression tests for DR Audio's English female third voice."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generate_dr_audio_english as en


class EnglishAudioTests(unittest.TestCase):
    def setUp(self):
        self.article = {
            "title": "Apple AirTag noticias", "url": "https://example.com/2026/10/a.html",
            "published": "2026-10-15T17:00:00-04:00",
            "text": "Apple AirTag noticias de tecnologia " * 12,
        }
        self.index = {
            "schemaVersion": 3,
            "historyCount": 2,
            "entries": [{
                "title": self.article["title"], "url": self.article["url"],
                "audioUrl": "https://old.example/female.m4a",
                "audioFemaleUrl": "https://old.example/female.m4a",
                "audioFemalePath": "/dr-audio/female.m4a",
                "audioMaleUrl": "https://old.example/male.m4a",
                "audioMalePath": "/dr-audio/male.m4a",
                "customLegacyField": "preserved",
            }, {
                "title": "Historical", "url": "https://example.com/old",
                "audioUrl": "https://old.example/older.m4a"
            }]
        }

    def test_identical_female_voice_and_model(self):
        self.assertEqual(en.VOICE_NAME, en.VOICE_CONFIGS["female"]["name"])
        self.assertEqual(en.RENDER_MODEL, en.MODEL)

    def test_english_is_never_selected_before_both_spanish(self):
        idx = copy.deepcopy(self.index)
        idx["entries"][0].pop("audioMaleUrl")
        self.assertIsNone(en.eligible_article([self.article], idx))
        idx = copy.deepcopy(self.index)
        idx["entries"][0].pop("audioFemaleUrl")
        self.assertIsNone(en.eligible_article([self.article], idx))
        self.assertIsNotNone(en.eligible_article([self.article], self.index))

    def test_existing_english_never_regenerated(self):
        idx = copy.deepcopy(self.index)
        idx["entries"][0]["audioEnglishUrl"] = "https://existing.example/en.m4a"
        idx["entries"][0]["audioEnglishPath"] = "/dr-audio/en.m4a"
        self.assertIsNone(en.eligible_article([self.article], idx))

    def test_incomplete_or_changed_brand_translation_rejected(self):
        original = self.article["text"]
        with self.assertRaises(ValueError):
            en.verify_translation(original, "too short")
        with self.assertRaisesRegex(ValueError, "AirTag"):
            en.verify_translation(original, "The technology news was announced today. " * 10)
        self.assertIn("AirTag", en.verify_translation(
            original, "Technology news about Apple AirTag was announced. " * 10
        ))

    def test_overlay_preserves_original_spanish_history_and_metadata(self):
        translated = "English technology news about Apple AirTag. " * 9
        digest = en.english_digest(translated)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (digest[:28] + ".m4a")
            path.write_bytes(b"1" * 1400)
            result = en.extend_index(self.index, self.article, translated, path)
        self.assertEqual(self.index["entries"][0].get("audioEnglishUrl"), None)
        self.assertEqual(result["entries"][0]["audioUrl"],
                         self.index["entries"][0]["audioUrl"])
        self.assertEqual(result["entries"][0]["audioFemaleUrl"],
                         self.index["entries"][0]["audioFemaleUrl"])
        self.assertEqual(result["entries"][0]["audioMaleUrl"],
                         self.index["entries"][0]["audioMaleUrl"])
        self.assertEqual(result["entries"][0]["audioEnglishPath"],
                         "/dr-audio/" + digest[:28] + ".m4a")
        self.assertEqual(result["entries"][1], self.index["entries"][1])
        self.assertEqual(result["historyCount"], 2)
        self.assertEqual(result["schemaVersion"], 3)
        self.assertEqual(result["entries"][0]["customLegacyField"], "preserved")

    def test_subsequent_spanish_generation_preserves_english_fields(self):
        # Existing Spanish generator runs every few minutes. After English is
        # present, refreshing Female/Male must never strip English metadata.
        import generate_shared_dr_audio as es
        article = copy.deepcopy(self.article)
        hashes = {
            voice: es.voice_hash(article["text"], voice)
            for voice in ("female", "male")
        }
        article["voiceHashes"] = hashes
        article["audioIds"] = {
            voice: hashes[voice][:28] for voice in hashes
        }
        old = copy.deepcopy(self.index["entries"][0])
        old.update({
            "femaleContentHash": hashes["female"],
            "maleContentHash": hashes["male"],
            "audioEnglishUrl": "https://host/dr-audio/english.m4a",
            "audioEnglishPath": "/dr-audio/english.m4a",
            "englishContentHash": "original-english-hash",
            "englishVoice": "Achernar",
            "englishBytes": 78903,
            "englishLanguage": "en",
        })
        refreshed = es.refresh_article_entry(old, article)
        for key in (
            "audioEnglishUrl", "audioEnglishPath", "englishContentHash",
            "englishVoice", "englishBytes", "englishLanguage",
        ):
            self.assertEqual(refreshed[key], old[key], key)
        self.assertEqual(refreshed["audioFemaleUrl"], old["audioFemaleUrl"])
        self.assertEqual(refreshed["audioMaleUrl"], old["audioMaleUrl"])
        self.assertEqual(refreshed["customLegacyField"], "preserved")
        self.assertEqual(self.index["entries"][1]["title"], "Historical")

    def test_scan_never_calls_gemini_or_audio_and_never_mutates_catalog(self):
        with (patch.object(sys, "argv", ["english", "--mode", "scan"]),
              patch.object(en, "load_live_index", return_value=self.index),
              patch.object(en, "parse_feed", return_value=[self.article]),
              patch.object(en.genai, "Client") as client):
            en.main()
            client.assert_not_called()
        self.assertNotIn("audioEnglishUrl", self.index["entries"][0])

    def test_wrong_confirmation_cannot_publish(self):
        with patch.object(sys, "argv", [
            "english", "--mode", "generate", "--confirm", "WRONG"
        ]):
            with self.assertRaisesRegex(ValueError, "confirmation"):
                en.main()


if __name__ == "__main__":
    unittest.main()
