import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_noticias_threads_pilot as threads


class ThreadsPilotTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://draccesoriosrd.blogspot.com/2026/10/nintendo-switch-2-juegos-fisicos-latinoamerica.html"
        self.post = {"url": self.url, "title": "Nintendo no abandonará los juegos físicos en Switch 2"}
        self.sha = "b" * 64

    def test_exact_threads_identity(self):
        valid = lambda path, token, fields: {
            "id": threads.ACCOUNT_ID, "username": "drnoticiastech"
        }
        self.assertEqual(threads.verify_account("fake-token", request=valid)["id"], threads.ACCOUNT_ID)
        for profile in [
            {"id": "28639578679032150", "username": "drnoticiastech"},
            {"id": threads.ACCOUNT_ID, "username": "draccesoriosrd"}
        ]:
            with self.assertRaises(RuntimeError):
                threads.verify_account("fake-token", request=lambda *args, **kwargs: profile)

    def test_threads_post_is_under_500_with_article_url(self):
        instagram = (
            "📰 Nintendo no abandonará los juegos físicos en Switch 2\n\n"
            "DR Accesorios RD · 12 de octubre de 2026 · República Dominicana\n\n"
            "En plena expansión de las descargas digitales, Nintendo envía una señal "
            "a quienes todavía prefieren comprar videojuegos en formato físico. "
            "Los cartuchos siguen siendo importantes para el mercado latinoamericano "
            "y algunos juegos requieren descargas.\n\n"
            "Lee la noticia completa desde el enlace de nuestra biografía.\n\n"
            "#Nintendo #Switch2 #TecnologiaRD #NoticiasRD #DRNoticiasTech")
        caption = threads.threads_caption(self.post["title"], instagram, self.url)
        self.assertLessEqual(len(caption), 480)
        self.assertIn(self.url, caption)
        self.assertIn("#DRNoticiasTech", caption)
        self.assertNotIn("enlace de nuestra biografía", caption)
        self.assertNotIn("DR Accesorios RD ·", caption)
        self.assertIn("En plena expansión", caption)

    def test_outside_url_and_very_long_title_rejected(self):
        content = "📰 titular\n\n" + "Editorial con datos. " * 15
        with self.assertRaises(ValueError):
            threads.threads_caption("Titular", content, "https://example.com/externa")
        with self.assertRaises(ValueError):
            threads.threads_caption("A" * 200, content, self.url)

    def test_ledger_is_separate_for_each_platform(self):
        from dr_noticias_instagram_pilot import ledger_path
        from dr_noticias_facebook_pilot import facebook_ledger_path
        value = threads.threads_ledger_path(self.url)
        self.assertTrue(value.startswith("dr-noticias-threads-ledger/"))
        self.assertNotEqual(value, ledger_path(self.url))
        self.assertNotEqual(value, facebook_ledger_path(self.url))

    def test_publishing_creates_media_then_publishes_only_once(self):
        ledgers = {}
        calls = []
        def gh(path, *, method="GET", data=None):
            return {"sha": "saved-sha"} if path.split("?")[0] in ledgers else None
        def ledger(path, record, sha=None):
            ledgers["/contents/" + path] = dict(record)
            return "saved-sha"
        def req(path, token, form=None, fields=None):
            calls.append((path, form, fields))
            if path.endswith("/threads"):
                self.assertEqual(form["media_type"], "IMAGE")
                self.assertEqual(form["image_url"], "https://example.org/cover.jpg")
                self.assertTrue(len(form["text"]) < 500)
                return {"id": "12345678900"}
            if path == "/12345678900":
                return {"id": "12345678900", "status": "FINISHED"}
            if path.endswith("/threads_publish"):
                self.assertEqual(form, {"creation_id": "12345678900"})
                return {"id": "999999999999"}
            raise AssertionError("Ruta Threads inesperada")
        with patch.object(threads, "github_api", side_effect=gh), patch.object(
                threads, "ledger_write", side_effect=ledger):
            threads.publish(self.post, self.sha, "https://example.org/cover.jpg",
                            "Texto Threads ejemplo " + self.url, "test-token", request=req,
                            pause=lambda _: None)
            self.assertEqual(len(calls), 3)
            self.assertEqual(ledgers["/contents/" + threads.threads_ledger_path(self.url)]["status"], "published")
            with self.assertRaisesRegex(RuntimeError, "reserva"):
                threads.publish(self.post, self.sha, "https://example.org/cover.jpg",
                                "Texto Threads ejemplo " + self.url, "test-token", request=req,
                                pause=lambda _: None)
            self.assertEqual(len(calls), 3)

    def test_uncertain_network_failure_requires_review_and_never_retries(self):
        saved = {}
        def gh(path, *, method="GET", data=None):
            return {"sha": "blob"} if path.split("?")[0] in saved else None
        def write(path, record, sha=None):
            saved["/contents/" + path] = dict(record)
            return "sha"
        def req(path, token, form=None, fields=None):
            if path.endswith("/threads"):
                return {"id":"456789123"}
            if path == "/456789123":
                return {"status":"FINISHED"}
            if path.endswith("/threads_publish"):
                raise RuntimeError("unknown Meta result")
        with patch.object(threads, "github_api", side_effect=gh), patch.object(
                threads, "ledger_write", side_effect=write):
            with self.assertRaisesRegex(RuntimeError, "unknown"):
                threads.publish(self.post, self.sha, "https://example.org/cover.jpg",
                                "Texto", "test", request=req, pause=lambda _: None)
            self.assertEqual(saved["/contents/" + threads.threads_ledger_path(self.url)]["status"],
                             "requires_review")
            with self.assertRaisesRegex(RuntimeError, "reserva"):
                threads.publish(self.post, self.sha, "https://example.org/cover.jpg",
                                "Texto", "test", request=req, pause=lambda _: None)


if __name__ == "__main__":
    unittest.main()
