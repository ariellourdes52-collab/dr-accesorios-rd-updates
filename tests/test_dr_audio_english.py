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

    def test_historical_fill_waits_until_all_newest_five_have_english(self):
        feed = [dict(self.article, url=f"https://example.com/new/{n}") for n in range(5)]
        older = dict(self.article, url="https://example.com/older-but-ready")
        feed.append(older)
        spanish = {
            "entries": [dict(self.spanish["entries"][0], url=x["url"]) for x in feed]
        }
        english = {
            "language": "en", "entries": [
                {"url": x["url"]} for x in feed[:4]
            ]
        }
        # Newest missing English ALWAYS outranks an old ready article.
        self.assertEqual(
            en.eligible_article(feed, spanish, english, historical_backfill=True)["url"],
            feed[4]["url"],
        )
        # Even if last new English is waiting for Spanish, no old story preempts it.
        spanish["entries"][4].pop("audioMaleUrl")
        self.assertIsNone(en.eligible_article(
            feed, spanish, english, historical_backfill=True,
        ))
        # Only when all latest five are EN-ready may older backfill proceed.
        english["entries"].append({"url": feed[4]["url"]})
        self.assertEqual(
            en.eligible_article(feed, spanish, english, historical_backfill=True)["url"],
            older["url"],
        )
        self.assertIsNone(en.eligible_article(
            feed, spanish, english, historical_backfill=False,
        ))

    def test_gemini_503_retries_bounded_without_touching_quota(self):
        class DemandError(Exception):
            status_code = 503

        counter = {"calls": 0}

        def eventual_success():
            counter["calls"] += 1
            if counter["calls"] < 3:
                raise DemandError("503 UNAVAILABLE")
            return "English content"

        with patch.object(en.time, "sleep") as sleep:
            self.assertEqual(en.retry_english_503(eventual_success), "English content")
        self.assertEqual(counter["calls"], 3)
        self.assertEqual([x.args[0] for x in sleep.call_args_list], [5, 15])

        counter["calls"] = 0
        with patch.object(en.time, "sleep") as sleep:
            with self.assertRaises(DemandError):
                en.retry_english_503(
                    lambda: (_ for _ in ()).throw(DemandError("503")), max_attempts=2
                )
        sleep.assert_called_once_with(5)

        with patch.object(en.time, "sleep") as sleep:
            with self.assertRaisesRegex(Exception, "429"):
                en.retry_english_503(
                    lambda: (_ for _ in ()).throw(Exception("429 quota"))
                )
        sleep.assert_not_called()

    def test_translation_failure_does_not_mark_tts_or_touch_spanish(self):
        class DemandError(Exception):
            status_code = 503

        env = {"GITHUB_ACTIONS": "true",
               "GITHUB_REF": "refs/heads/main",
               "EVENT_NAME": "schedule",
               "GEMINI_API_KEY_DR_AUDIO_EN": "dummy-test-key"}
        original_spanish = copy.deepcopy(self.spanish)
        with tempfile.TemporaryDirectory() as tmp:
            with (patch.dict("os.environ", env),
                  patch.object(sys, "argv", [
                      "english", "--mode", "generate", "--output", tmp,
                  ]),
                  patch.object(en, "english_quota_blocked_until", return_value=None),
                  patch.object(en, "load_live_index", return_value=self.spanish),
                  patch.object(en, "load_english_index", return_value=self.english),
                  patch.object(en, "parse_feed", return_value=[self.article]),
                  patch.object(en, "english_recent_tts_attempt", return_value=False),
                  patch.object(en, "translate_article", side_effect=DemandError("503")),
                  patch.object(en, "pause_english_quota", return_value=False),
                  patch.object(en, "tts_timeout_error", return_value=False),
                  patch.object(en, "mark_english_tts_attempt") as tts_marker,
                  patch.object(en, "generate_wav") as tts):
                en.main()  # Transient 503 is deferred, not a failed workflow.
                tts_marker.assert_not_called()
                tts.assert_not_called()
        self.assertEqual(self.spanish, original_spanish)

    def test_503_translation_never_marks_tts_or_caches_bad_output(self):
        class DemandError(Exception):
            status_code = 503

        self.assertTrue(en.english_temporary_503(DemandError("UNAVAILABLE")))
        self.assertTrue(en.english_temporary_503(Exception("503 UNAVAILABLE")))
        self.assertFalse(en.english_temporary_503(Exception("429 RESOURCE_EXHAUSTED")))
        self.assertFalse(en.english_temporary_503(ValueError("Bad translation")))
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "english-only"
            env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main",
                   "EVENT_NAME": "schedule", "GEMINI_API_KEY_DR_AUDIO_EN": "test"}
            with (patch.dict("os.environ", env),
                  patch.object(en, "ENGLISH_TRANSLATION_CACHE_DIR", cache_path),
                  patch.object(sys, "argv", ["english", "--mode", "generate",
                                           "--output", str(Path(tmp) / "build")]),
                  patch.object(en, "english_quota_blocked_until", return_value=None),
                  patch.object(en, "load_live_index", return_value=self.spanish),
                  patch.object(en, "load_english_index", return_value=self.english),
                  patch.object(en, "parse_feed", return_value=[self.article]),
                  patch.object(en, "english_recent_tts_attempt", return_value=False),
                  patch.object(en, "translate_article", side_effect=DemandError("503")),
                  patch.object(en.genai, "Client"),
                  patch.object(en, "mark_english_tts_attempt") as marker,
                  patch.object(en, "generate_wav") as tts):
                en.main()
            marker.assert_not_called()
            tts.assert_not_called()
            self.assertFalse(cache_path.exists())

    def test_503_tts_preserves_english_translation_for_next_run(self):
        class DemandError(Exception):
            status_code = 503

        translated = "The FBI seizes devices. Apple AirTag made news. " * 13
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main",
               "EVENT_NAME": "schedule", "GEMINI_API_KEY_DR_AUDIO_EN": "test"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_path = root / "english-transcripts"
            output_path = root / "prepared"
            es_snapshot = copy.deepcopy(self.spanish)
            with (patch.dict("os.environ", env),
                  patch.object(sys, "argv", ["english", "--mode", "generate",
                                           "--output", str(output_path)]),
                  patch.object(en, "ENGLISH_TRANSLATION_CACHE_DIR", cache_path),
                  patch.object(en, "english_quota_blocked_until", return_value=None),
                  patch.object(en, "load_live_index", return_value=self.spanish),
                  patch.object(en, "load_english_index", return_value=self.english),
                  patch.object(en, "parse_feed", return_value=[self.article]),
                  patch.object(en, "english_recent_tts_attempt", return_value=False),
                  patch.object(en.genai, "Client"),
                  patch.object(en, "mark_english_tts_attempt"),
                  patch.object(en, "translate_article", return_value=translated) as translation,
                  patch.object(en, "generate_wav", side_effect=DemandError("503")) as tts):
                en.main()
                translation.assert_called_once()
                # TTS retries exactly twice on 503 before deferring.
                self.assertEqual(tts.call_count, 2)
                transcripts = list(cache_path.glob("*.en.txt"))
                self.assertEqual(len(transcripts), 1)
                self.assertEqual(transcripts[0].read_text(), translated)
                self.assertFalse((output_path / "dr-audio/index.json").exists())
                with (patch.object(en, "translate_article",
                                   side_effect=AssertionError("Duplicate translation")),
                      patch.object(en, "generate_wav", return_value=b"WAV"),
                      patch.object(en, "wav_to_m4a",
                                   side_effect=lambda wav, p: p.write_bytes(b"A" * 2048))):
                    en.main()
                self.assertTrue((output_path / "dr-audio/index.json").is_file())
                self.assertEqual(self.spanish, es_snapshot)

    def test_english_quota_cache_is_separate_and_time_bounded(self):
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".dr-audio-english-state/gemini-daily-quota.json"
            other = Path(tmp) / ".dr-audio-state/gemini-daily-quota.json"
            other.parent.mkdir()
            other.write_text("existing Spanish quota", encoding="utf-8")
            self.assertTrue(en.pause_english_quota(Exception("429 rate limit"), path))
            self.assertTrue(path.exists())
            self.assertTrue((path.parent / ".new-quota").exists())
            self.assertIsNotNone(en.english_quota_blocked_until(path))
            self.assertEqual(other.read_text(encoding="utf-8"), "existing Spanish quota")
            self.assertFalse(en.pause_english_quota(ValueError("Invalid title"), path))
            data = __import__("json").loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["key"], "GEMINI_API_KEY_DR_AUDIO_EN")
            data["blockedUntil"] = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
            path.write_text(__import__("json").dumps(data), encoding="utf-8")
            self.assertIsNone(en.english_quota_blocked_until(path))

    def test_schedule_requires_main_and_github_actions(self):
        article_already_english = {
            "schemaVersion": 1, "language": "en",
            "entries": [{"url": self.article["url"]}],
        }
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main",
               "EVENT_NAME": "schedule"}
        with (patch.dict("os.environ", env),
              patch.object(sys, "argv", ["english", "--mode", "generate"]),
              patch.object(en, "english_quota_blocked_until", return_value=None),
              patch.object(en, "load_live_index", return_value=self.spanish),
              patch.object(en, "load_english_index", return_value=article_already_english),
              patch.object(en, "parse_feed", return_value=[self.article]),
              patch.object(en.genai, "Client") as client,
              tempfile.TemporaryDirectory() as tmp):
            with patch.object(sys, "argv", [
                "english", "--mode", "generate", "--output", tmp,
            ]):
                en.main()
            client.assert_not_called()
        with (patch.dict("os.environ", {"GITHUB_ACTIONS": "true",
                                       "GITHUB_REF": "refs/heads/other",
                                       "EVENT_NAME": "schedule"}),
              patch.object(sys, "argv", ["english", "--mode", "generate"])):
            with self.assertRaisesRegex(RuntimeError, "confirmation"):
                en.main()
        with (patch.dict("os.environ", {"GITHUB_ACTIONS": "true",
                                       "GITHUB_REF": "refs/heads/main",
                                       "EVENT_NAME": "workflow_dispatch"}),
              patch.object(sys, "argv", ["english", "--mode", "generate",
                                         "--confirm", "BAD"])):
            with self.assertRaisesRegex(RuntimeError, "confirmation"):
                en.main()

    def test_successful_spanish_workflow_completes_then_wakes_english(self):
        """Production EN launches from verified Spanish completion, not PRs."""
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/generate-dr-audio-english.yml").read_text()
        self.assertIn("  workflow_run:", workflow)
        self.assertIn('workflows: ["Generate shared DR Audio"]', workflow)
        self.assertIn("types: [completed]", workflow)
        self.assertIn("branches: [main]", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", workflow)
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name == github.repository",
            workflow,
        )
        self.assertIn("  schedule:", workflow)  # fallback retained
        self.assertIn("  pull_request:", workflow)  # offline PR tests retained
        self.assertIn("  workflow_dispatch:", workflow)  # manual fallback retained

    def test_workflow_run_authorized_only_on_main_without_gemini_if_already_done(self):
        english = {"schemaVersion": 1, "language": "en",
                   "entries": [{"url": self.article["url"]}]}
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main",
               "EVENT_NAME": "workflow_run"}
        with (tempfile.TemporaryDirectory() as tmp,
              patch.dict("os.environ", env),
              patch.object(sys, "argv", [
                  "english", "--mode", "generate", "--output", tmp,
              ]),
              patch.object(en, "english_quota_blocked_until", return_value=None),
              patch.object(en, "load_live_index", return_value=self.spanish),
              patch.object(en, "load_english_index", return_value=english),
              patch.object(en, "parse_feed", return_value=[self.article]),
              patch.object(en.genai, "Client") as client):
            en.main()
            client.assert_not_called()
        with (patch.dict("os.environ", {
                  "GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/unsafe",
                  "EVENT_NAME": "workflow_run",
              }),
              patch.object(sys, "argv", ["english", "--mode", "generate"])):
            with self.assertRaisesRegex(RuntimeError, "confirmation"):
                en.main()

    def test_restored_old_manifest_cannot_deploy_when_quota_blocked(self):
        from datetime import datetime, timedelta, timezone
        blocked = datetime.now(timezone.utc) + timedelta(hours=1)
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main",
               "EVENT_NAME": "schedule"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dr-audio"
            root.mkdir()
            manifest = root / "index.json"
            manifest.write_text('{"stale":true}', encoding="utf-8")
            with (patch.dict("os.environ", env),
                  patch.object(sys, "argv", [
                      "english", "--mode", "generate", "--output", tmp,
                  ]),
                  patch.object(en, "english_quota_blocked_until",
                               return_value=blocked),
                  patch.object(en, "load_live_index") as spanish_get):
                en.main()
                spanish_get.assert_not_called()
            self.assertFalse(manifest.exists())

    def test_workflow_scheduler_never_uses_spanish_queue_key_or_secret(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/generate-dr-audio-english.yml").read_text()
        self.assertIn("8,18,28,38,48,58 * * * *", workflow)
        self.assertIn("group: dr-audio-english-independent-release", workflow)
        self.assertNotIn("group: dr-accesorios-rd-production-release", workflow)
        self.assertIn("GEMINI_API_KEY_DR_AUDIO_EN", workflow)
        self.assertNotIn("secrets.GEMINI_API_KEY_DR_AUDIO }}", workflow)
        self.assertIn("dr-audio-en-quota-", workflow)
        self.assertIn("dr-audio-english-pending-", workflow)
        self.assertIn("dr_audio_english_build", workflow)
        self.assertIn("scripts/deploy_dr_audio_english.py", workflow)
        self.assertNotIn("scripts/deploy_shared_dr_audio.py", workflow)

    def test_english_watchdog_matches_spanish_backup_without_crossing_schedules(self):
        workflows = Path(__file__).resolve().parents[1] / ".github/workflows"
        en_wd = (workflows / "dr-audio-english-scheduler-watchdog.yml").read_text()
        es_wd = (workflows / "dr-audio-scheduler-watchdog.yml").read_text()
        en = (workflows / "generate-dr-audio-english.yml").read_text()
        self.assertIn("TARGET_WORKFLOW: generate-dr-audio-english.yml", en_wd)
        self.assertIn('"confirm": "GENERATE-DR-AUDIO-EN"', en_wd)
        self.assertIn('"historical_backfill": "0"', en_wd)
        self.assertIn('"scheduler_guard": "true"', en_wd)
        self.assertIn('run.get("head_branch") == "main"', en_wd)
        self.assertIn('run.get("event") in {"schedule", "push", "workflow_dispatch", "workflow_run"}', en_wd)
        self.assertIn("per_page=100", en_wd)
        self.assertIn("2,12,22,32,42,52 * * * *", en_wd)
        self.assertIn("7,17,27,37,47,57 * * * *", es_wd)
        self.assertIn("8,18,28,38,48,58 * * * *", en)
        self.assertIn("3,13,23,33,43,53 * * * *",
                      (workflows / "generate-dr-audio.yml").read_text())
        self.assertNotIn("TARGET_WORKFLOW: generate-dr-audio.yml", en_wd)
        self.assertNotIn("dr-audio-scheduler-watchdog\n", en_wd)
        self.assertIn('newest.get("conclusion") in {"failure", "timed_out"}', en_wd)
        self.assertIn("if failed and age_seconds < 5 * 60:", en_wd)
        self.assertIn("if not failed and age_seconds < 8 * 60:", en_wd)
        self.assertIn("group: dr-audio-english-scheduler-watchdog", en_wd)
        self.assertIn("group: dr-audio-english-independent-release", en)

    def test_english_attempt_marker_is_json_and_only_en_state(self):
        import json
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 10, 16, 5, 40, tzinfo=timezone.utc)
            path = Path(tmp) / ".dr-audio-english-state/last-tts-attempt.json"
            es_marker = Path(tmp) / ".dr-audio-state/last-tts-attempt.json"
            es_marker.parent.mkdir()
            es_marker.write_text("KEEP-SPANISH", encoding="utf-8")
            self.assertFalse(en.english_recent_tts_attempt(path, now))
            en.mark_english_tts_attempt(path, now)
            self.assertEqual(json.loads(path.read_text())["attemptedAt"], now.isoformat())
            self.assertTrue(en.english_recent_tts_attempt(path, now + timedelta(minutes=7)))
            self.assertFalse(en.english_recent_tts_attempt(path, now + timedelta(minutes=8)))
            self.assertFalse(en.english_recent_tts_attempt(path, now + timedelta(minutes=20)))
            self.assertEqual(es_marker.read_text(), "KEEP-SPANISH")

    def test_english_push_trigger_and_cache_never_share_spanish_resources(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/generate-dr-audio-english.yml").read_text()
        self.assertIn("  push:\n    branches:\n      - main", workflow)
        self.assertIn("  schedule:", workflow)
        self.assertIn("SCHEDULER_GUARD:", workflow)
        self.assertIn("dr-audio-en-attempt-", workflow)
        self.assertIn(".dr-audio-english-state/last-tts-attempt.json", workflow)
        self.assertIn("GEMINI_API_KEY_DR_AUDIO_EN", workflow)
        self.assertNotIn("group: dr-accesorios-rd-production-release", workflow)
        self.assertNotIn("scripts/deploy_shared_dr_audio.py", workflow)
        self.assertNotIn("scripts/generate_shared_dr_audio.py'\n", workflow)


if __name__ == "__main__":
    unittest.main()
