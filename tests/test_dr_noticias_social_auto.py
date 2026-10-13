import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_noticias_social_auto as auto


class SocialAutoTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://draccesoriosrd.blogspot.com/2026/10/prueba-tecnologia-2026.html"
        self.now = datetime(2026, 10, 13, 1, 30, tzinfo=timezone.utc)
        self.post = {
            "url": self.url,
            "title": "Noticias tecnológicas de República Dominicana",
            "cover": "https://blogger.googleusercontent.com/img/a.jpg",
            "published": (self.now - timedelta(hours=1)).isoformat()
        }
        self.enabled_env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_TOKEN": "fake-github-token",
            "DR_NOTICIAS_AUTO_START_UTC": "2026-10-13T00:00:00+00:00",
            "DR_NOTICIAS_FB_AUTO_ENABLED": "true",
            "DR_NOTICIAS_THREADS_AUTO_ENABLED": "true",
            "DR_NOTICIAS_FB_PAGE_TOKEN": "fake-facebook-token",
            "DR_NOTICIAS_THREADS_ACCESS_TOKEN": "fake-threads-token"
        }

    def test_daily_paths_are_isolated_by_platform(self):
        d = self.now.date()
        fb = auto.daily_path("facebook", d, 1)
        th = auto.daily_path("threads", d, 1)
        self.assertNotEqual(fb, th)
        self.assertTrue(fb.startswith("dr-noticias-social-daily/facebook/"))
        self.assertTrue(th.startswith("dr-noticias-social-daily/threads/"))
        self.assertRaises(ValueError, auto.daily_path, "instagram", d, 1)
        self.assertRaises(ValueError, auto.daily_path, "threads", d, 4)

    def test_available_slot_returns_none_after_3_reservations(self):
        day = self.now.date()
        requests = []
        def get(path):
            requests.append(path)
            return {"sha": "taken"}
        self.assertIsNone(auto.available_slot("facebook", day, request=get))
        self.assertEqual(len(requests), 3)
        self.assertEqual(len(set(requests)), 3)

    def test_available_slot_is_independent(self):
        def get(path):
            return {"sha": "taken"} if "/facebook/" in path else None
        self.assertIsNone(auto.available_slot("facebook", self.now.date(), request=get))
        self.assertEqual(auto.available_slot("threads", self.now.date(), request=get), 1)

    def test_reserve_slot_detects_prior_reservation(self):
        with self.assertRaisesRegex(RuntimeError, "Reserva diaria existente"):
            auto.reserve_slot("facebook", self.now.date(), 1, self.url,
                              request=lambda _: {"sha": "existing"},
                              write=lambda *args: self.fail("No debió escribir"))

    def test_fails_closed_when_not_enabled_or_not_main(self):
        for platform, key in [
            ("facebook", "DR_NOTICIAS_FB_AUTO_ENABLED"),
            ("threads", "DR_NOTICIAS_THREADS_AUTO_ENABLED")
        ]:
            env = dict(self.enabled_env)
            env[key] = "false"
            with self.assertRaisesRegex(ValueError, "no habilitado"):
                auto.run(platform, "run", environ=env, now=self.now)
            env[key] = "true"
            env["GITHUB_REF"] = "refs/heads/feature"
            with self.assertRaisesRegex(ValueError, "no habilitado"):
                auto.run(platform, "run", environ=env, now=self.now)

    def test_scan_cannot_upload_images_or_publish(self):
        feed = {"feed": {"entry": []}}
        for platform in ("facebook", "threads"):
            cfg = auto.PLATFORMS[platform]
            with patch.object(auto, "read_url", return_value=(b'{"feed":{"entry":[]}}', "application/json")), \
                    patch.object(auto, "entries_from_feed", return_value=[self.post]), \
                    patch.object(auto, "editorial_ready", return_value=None), \
                    patch.object(auto, "prepare_post", side_effect=AssertionError("NO PREPARE")), \
                    patch.object(auto, "available_slot", side_effect=AssertionError("NO LEDGER")), \
                    patch.dict(cfg, {"verify":lambda token:None,
                                     "publish":lambda *args:self.fail("NO POST")}):
                auto.run(platform, "scan", environ=self.enabled_env, now=self.now)

    def test_run_publishes_one_and_uses_own_caption(self):
        for platform in ("facebook", "threads"):
            cfg = auto.PLATFORMS[platform]
            published = []
            reservations = []
            def prepared(feed, post):
                return post, "a"*64, "https://raw.githubusercontent.com/example/cover.jpg", "texto IG"
            def caption(*args):
                if platform == "facebook":
                    self.assertEqual(len(args), 2)
                else:
                    self.assertEqual(len(args), 3)
                return "texto " + platform + " " + self.url
            with patch.object(auto, "read_url", return_value=(b'{"feed":{"entry":[]}}', "application/json")), \
                    patch.object(auto, "entries_from_feed", return_value=[self.post]), \
                    patch.object(auto, "editorial_ready", return_value=None), \
                    patch.object(auto, "prepare_post", side_effect=prepared), \
                    patch.object(auto, "available_slot", return_value=1), \
                    patch.object(auto, "reserve_slot", side_effect=lambda *a:reservations.append(a)), \
                    patch.object(auto, "github_api", return_value=None), \
                    patch.dict(cfg, {
                        "verify":lambda token:None,
                        "caption":caption,
                        "publish":lambda *a:published.append(a)
                    }):
                auto.run(platform, "run", environ=self.enabled_env, now=self.now)
            self.assertEqual(len(reservations), 1)
            self.assertEqual(reservations[0][0], platform)
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0][3], "texto " + platform + " " + self.url)


    def test_prepare_post_reuses_one_jpeg_and_verifies_integrity(self):
        import hashlib
        jpeg = bytes([255, 216, 255]) + b"valid-jpeg-test"
        digest = hashlib.sha256(jpeg).hexdigest()
        url = auto.MEDIA_BASE + digest + ".jpg"
        feed = {"feed": {"entry": [{"link": [{"rel": "alternate", "href": self.url}],
                                    "content": {"$t": "<p>Noticia verificada</p>"}}]}}
        with (
            patch.object(auto, "high_resolution_cover_urls", return_value=[self.post["cover"]]),
            patch.object(auto, "read_url", side_effect=[(b"original", "image/png"),
                                                        (jpeg, "image/jpeg")]) as fetch,
            patch.object(auto, "adequate_cover_quality", return_value=True),
            patch.object(auto, "jpeg_from_png", return_value=jpeg) as convert,
            patch.object(auto, "stage_jpeg", return_value=url) as stage,
            patch.object(auto, "make_caption", return_value="texto verificado"),
        ):
            selected, actual_digest, actual_url, caption = auto.prepare_post(feed, self.post)
        self.assertEqual(selected, self.post)
        self.assertEqual(actual_digest, digest)
        self.assertEqual(actual_url, url)
        self.assertEqual(caption, "texto verificado")
        self.assertEqual(fetch.call_count, 2)
        convert.assert_called_once_with(b"original")
        stage.assert_called_once_with(self.post, jpeg)

    def test_prepare_post_rejects_staged_jpeg_with_wrong_content(self):
        import hashlib
        jpeg = bytes([255, 216, 255]) + b"correct"
        digest = hashlib.sha256(jpeg).hexdigest()
        url = auto.MEDIA_BASE + digest + ".jpg"
        with (
            patch.object(auto, "high_resolution_cover_urls", return_value=[self.post["cover"]]),
            patch.object(auto, "read_url", side_effect=[(b"original", "image/png"),
                                                        (b"wrong-jpeg", "image/jpeg")]),
            patch.object(auto, "adequate_cover_quality", return_value=True),
            patch.object(auto, "jpeg_from_png", return_value=jpeg),
            patch.object(auto, "stage_jpeg", return_value=url),
        ):
            with self.assertRaisesRegex(RuntimeError, "no coincide"):
                auto.prepare_post({"feed": {"entry": []}}, self.post)

    def test_prepare_post_rejects_unexpected_media_url(self):
        jpeg = bytes([255, 216, 255]) + b"correct"
        with (
            patch.object(auto, "high_resolution_cover_urls", return_value=[self.post["cover"]]),
            patch.object(auto, "read_url", return_value=(b"original", "image/png")) as fetch,
            patch.object(auto, "adequate_cover_quality", return_value=True),
            patch.object(auto, "jpeg_from_png", return_value=jpeg),
            patch.object(auto, "stage_jpeg", return_value="https://invalid.example/cover.jpg"),
        ):
            with self.assertRaisesRegex(RuntimeError, "URL de portada"):
                auto.prepare_post({"feed": {"entry": []}}, self.post)
        fetch.assert_called_once()

    def test_repeated_article_is_skipped_without_publishing(self):
        for platform in ("facebook", "threads"):
            cfg = auto.PLATFORMS[platform]
            with patch.object(auto, "read_url", return_value=(b'{"feed":{"entry":[]}}', "application/json")), \
                    patch.object(auto, "entries_from_feed", return_value=[self.post]), \
                    patch.object(auto, "available_slot", return_value=1), \
                    patch.object(auto, "github_api", return_value={"sha":"already"}), \
                    patch.object(auto, "prepare_post", side_effect=AssertionError("No debe convertir")), \
                    patch.dict(cfg, {"verify":lambda token:None,
                                     "publish":lambda *args:self.fail("No publicar duplicados")}):
                auto.run(platform, "run", environ=self.enabled_env, now=self.now)

    def test_old_or_pre_activation_posts_never_published(self):
        feed = {"feed": {"entry": []}}
        stale = {**self.post, "published": (self.now - timedelta(hours=9)).isoformat()}
        early = {**self.post, "published": "2026-10-12T23:00:00+00:00"}
        cfg = auto.PLATFORMS["facebook"]
        with patch.object(auto, "read_url", return_value=(b'{"feed":{"entry":[]}}', "application/json")), \
                patch.object(auto, "entries_from_feed", return_value=[early, stale]), \
                patch.dict(cfg, {"verify":lambda token:None,
                                 "publish":lambda *args:self.fail("No publicar viejas")}):
            auto.run("facebook", "run", environ=self.enabled_env, now=self.now)


if __name__ == "__main__":
    unittest.main()
