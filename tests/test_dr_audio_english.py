"""DR Audio English: independent generation with absolutely read-only Spanish."""
import copy
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
            "title": "FBI and AirTag", "url": "https://example.com/2026/10/a.html",
            "published": "2026-10-15T17:00:00-04:00",
            "text": "El FBI incauta dispositivos y Apple AirTag es noticia. " * 12,
        }
        self.spanish = {
            "schemaVersion": 3,
            "historyCount": 2,
            "entries": [{
                "title": self.article["title"], "url": self.article["url"],
                "audioUrl": "https://spanish.example/female.m4a",
                "audioFemaleUrl": "https://spanish.example/female.m4a",
                "audioMaleUrl": "https://spanish.example/male.m4a",
                "customLegacyField": "preserve",
            }, {
                "title": "Historical", "url": "https://example.com/old",
                "audioUrl": "https://spanish.example/older.m4a"
            }]
        }
        self.english = {
            "schemaVersion": 1, "language": "en",
            "entries": [
                {"url": "https://example.com/older",
                 "audioEnglishPath": "/dr-audio/older.m4a",
                 "audioEnglishUrl": en.ENGLISH_PUBLIC_BASE + "/dr-audio/older.m4a"}
            ],
        }

    def test_tts_uses_exact_same_existing_female_model_and_voice(self):
        self.assertEqual(en.VOICE_NAME, en.VOICE_CONFIGS["female"]["name"])
        self.assertEqual(en.RENDER_MODEL, en.MODEL)

    def test_independent_host_not_spanish_site(self):
        self.assertEqual(en.ENGLISH_SITE_ID, "dr-accesorios-rd-audio-en")
        self.assertNotEqual(en.ENGLISH_SITE_ID, "dr-accesorios-rd")
        self.assertNotIn("dr-accesorios-rd.web.app", en.ENGLISH_INDEX)

    def test_english_waits_for_existing_female_and_male_spanish(self):
        self.assertIsNotNone(en.eligible_article([self.article], self.spanish, self.english))
        for voice_key in ("audioFemaleUrl", "audioMaleUrl"):
            idx = copy.deepcopy(self.spanish)
            idx["entries"][0].pop(voice_key)
            self.assertIsNone(en.eligible_article([self.article], idx, self.english))

    def test_published_english_never_regenerated(self):
        idx = copy.deepcopy(self.english)
        idx["entries"][0]["url"] = self.article["url"]
        self.assertIsNone(en.eligible_article([self.article], self.spanish, idx))

    def test_brand_and_incomplete_translation_rejected(self):
        source = self.article["text"]
        with self.assertRaises(ValueError):
            en.verify_translation(source, "too short")
        with self.assertRaisesRegex(ValueError, "AirTag"):
            en.verify_translation(source, "The FBI announced the news. " * 14)
        text = "The FBI seizes devices. Apple AirTag made news. " * 13
        self.assertIn("AirTag", en.verify_translation(source, text))

    def test_prepared_index_preserves_old_english_and_all_spanish_data(self):
        original_es = copy.deepcopy(self.spanish)
        original_en = copy.deepcopy(self.english)
        narration = "The FBI seizes electronic devices. Apple AirTag in the news. " * 8
        digest = en.english_digest(narration)
        with tempfile.TemporaryDirectory() as tmp:
            audio_file = Path(tmp) / (digest[:28] + ".m4a")
            audio_file.write_bytes(b"0" * 2048)
            result = en.extend_index(self.english, self.article, narration, audio_file)
        self.assertEqual(self.spanish, original_es)
        self.assertEqual(self.english, original_en)
        self.assertEqual(result["entries"][1:], original_en["entries"])
        self.assertEqual(result["entries"][0]["audioEnglishPath"],
                         "/dr-audio/" + digest[:28] + ".m4a")
        self.assertNotIn("audioFemaleUrl", result["entries"][0])
        self.assertNotIn("audioMaleUrl", result["entries"][0])

    def test_scan_never_calls_gemini_or_mutates_spanish(self):
        es_snapshot = copy.deepcopy(self.spanish)
        with (patch.object(sys, "argv", ["english", "--mode", "scan"]),
              patch.object(en, "load_live_index", return_value=self.spanish),
              patch.object(en, "load_english_index", return_value=self.english),
              patch.object(en, "parse_feed", return_value=[self.article]),
              patch.object(en.genai, "Client") as client):
            en.main()
            client.assert_not_called()
        self.assertEqual(self.spanish, es_snapshot)

    def test_wrong_confirmation_cannot_start_live_generation(self):
        with patch.object(sys, "argv", [
            "english", "--mode", "generate", "--confirm", "WRONG"
        ]):
            with self.assertRaisesRegex(RuntimeError, "confirmation"):
                en.main()


if __name__ == "__main__":
    unittest.main()
