"""Pruebas offline, ninguna llamada a X, ni escrituras reales."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_noticias_x_auto as x


class XPublisherTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)
        self.url = "https://draccesoriosrd.blogspot.com/2026/10/noticia-prueba.html"
        self.post = {
            "url": self.url,
            "title": "Google presenta una nueva funcion para Android en Republica Dominicana",
            "published": (self.now - timedelta(hours=1)).isoformat(),
            "cover": "https://blogger.googleusercontent.com/img/a.jpg",
        }
        self.html = "<p>" + ("La actualización amplía las opciones para usuarios de teléfonos Android. " * 4) + "</p>"
        self.feed = {"feed": {"entry": [{"content": {"$t": self.html}}]}}
        self.env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_TOKEN": "test-github",
            "DR_NOTICIAS_X_AUTO_ENABLED": "true",
            "DR_NOTICIAS_X_START_UTC": "2026-10-14T13:00:00+00:00",
            "X_API_KEY": "fake-api-key",
            "X_API_SECRET": "fake-api-secret",
            "X_ACCESS_TOKEN": "fake-access-token",
            "X_ACCESS_TOKEN_SECRET": "fake-access-token-secret",
        }

    def fake_fetch(self, url, **kwargs):
        if url != x.FEED:
            raise AssertionError("La simulacion no debe descargar imagenes")
        import json
        return json.dumps(self.feed).encode("utf-8"), "application/json"

    def test_caption_has_no_blog_url_and_stays_below_limit(self):
        msg = x.caption(self.post["title"], self.html)
        self.assertLessEqual(len(msg), 235)
        self.assertIn("#DRNoticiasTech", msg)
        self.assertIn("#Android", msg)
        self.assertIn("Lee la noticia completa en nuestro blog. Enlace en el perfil.", msg)
        self.assertNotIn("http", msg)
        self.assertNotIn(self.url, msg)

    def test_links_rejected_even_shortened_domains(self):
        for bad in ("Lee https://blog.example.com/a", "Visita www.example.com",
                    "Entra en example.com", "Nota draccesoriosrd.blogspot.com"):
            with self.assertRaises(ValueError):
                x.no_links(bad)

    def test_caption_will_not_copy_blog_link_from_article(self):
        contaminated = "<p>" + ("Este informe tecnologico presenta novedades para todos los usuarios. " * 2) + "Fuente https://example.com</p>"
        with self.assertRaisesRegex(ValueError, "URL"):
            x.caption("Nueva noticia tecnologica", contaminated)

    def test_slot_range_and_ledger_namespace(self):
        paths = [x.slot_path("2026-10-14", i) for i in (1, 2, 3)]
        self.assertEqual(len(set(paths)), 3)
        self.assertTrue(all("x-daily" in p for p in paths))
        with self.assertRaises(ValueError):
            x.slot_path("2026-10-14", 4)
        self.assertTrue(x.ledger_path(self.url).startswith("dr-noticias-x-ledger/"))

    def test_full_day_has_no_slot(self):
        self.assertIsNone(x.first_free_slot("2026-10-14", gh=lambda _: {"sha": "occupied"}))

    def test_run_rejects_disabled_wrong_branch_and_missing_keys(self):
        for name, value in [
            ("DR_NOTICIAS_X_AUTO_ENABLED", "false"),
            ("GITHUB_REF", "refs/heads/test"),
            ("GITHUB_ACTIONS", "false"),
            ("GITHUB_TOKEN", ""),
            ("X_ACCESS_TOKEN_SECRET", ""),
            ("DR_NOTICIAS_X_START_UTC", ""),
        ]:
            env = dict(self.env)
            env[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                x.run("run", env=env, now=self.now,
                      fetch=lambda *a, **kw: self.fail("No debe leer Blogger"))

    def test_scan_no_x_no_github_no_image_and_no_secrets(self):
        with patch.object(x, "entries_from_feed", return_value=[self.post]), \
             patch.object(x, "editorial_ready", return_value={"content": {"$t": self.html}}), \
             patch.object(x, "x_request", side_effect=AssertionError("X NO")), \
             patch.object(x, "image_for_article", side_effect=AssertionError("IMG NO")):
            x.run("scan", env={}, now=self.now, fetch=self.fake_fetch,
                  gh=lambda _: self.fail("GITHUB NO"),
                  writer=lambda *a, **k: self.fail("ESCRITURA NO"),
                  requester=lambda *a, **k: self.fail("X NO"))

    def test_existing_ledger_never_posts(self):
        with patch.object(x, "entries_from_feed", return_value=[self.post]), \
             patch.object(x, "editorial_ready", return_value={"content": {"$t": self.html}}), \
             patch.object(x, "image_for_article", side_effect=AssertionError("IMG NO")):
            def get(path):
                return {"sha": "existing"} if "x-ledger" in path else None
            x.run("run", env=self.env, now=self.now, fetch=self.fake_fetch, gh=get,
                  requester=lambda *a: self.fail("No hay llamada API"))

    def test_publication_once_reserves_and_attaches_image_without_url(self):
        written = []
        called = []
        def writer(path, record, sha=None):
            written.append((path, dict(record), sha))
            return "new-sha"
        def requester(method, path, env, payload=None):
            called.append((method, path, payload))
            if path == "/2/users/me":
                return {"data": {"id": "123", "username": "drnoticiastech"}}
            if path == "/2/media/upload":
                return {"data": {"id": "123456"}}
            if path == "/2/tweets":
                return {"data": {"id": "987654321"}}
            self.fail("Ruta no permitida")
        with patch.object(x, "entries_from_feed", return_value=[self.post]), \
             patch.object(x, "editorial_ready", return_value={"content": {"$t": self.html}}), \
             patch.object(x, "image_for_article", return_value=b"\xff\xd8\xffJPEGtest"):
            x.run("run", env=self.env, now=self.now, fetch=self.fake_fetch,
                  gh=lambda _: None, writer=writer, requester=requester)
        self.assertEqual([p for _, p, _ in called],
                         ["/2/users/me", "/2/media/upload", "/2/tweets"])
        payload = called[-1][2]
        self.assertEqual(payload["media"]["media_ids"], ["123456"])
        self.assertNotIn("https://", payload["text"])
        self.assertEqual(sum(1 for _, rec, _ in written if rec["status"] == "published"), 1)
        self.assertEqual(sum(1 for _, rec, _ in written if rec["status"] == "reserved"), 2)

    def test_wrong_user_blocks_without_upload_and_no_retry(self):
        written = []
        def requester(method, path, env, payload=None):
            if path == "/2/users/me":
                return {"data": {"username": "someoneelse"}}
            self.fail("No deberia subir imagen ni publicar")
        with patch.object(x, "entries_from_feed", return_value=[self.post]), \
             patch.object(x, "editorial_ready", return_value={"content": {"$t": self.html}}), \
             patch.object(x, "image_for_article", return_value=b"\xff\xd8\xffJPEGtest"):
            with self.assertRaisesRegex(RuntimeError, "revisión manual"):
                x.run("run", env=self.env, now=self.now,
                      fetch=self.fake_fetch, gh=lambda _: None,
                      writer=lambda path, r, sha=None: written.append(dict(r)) or "sha",
                      requester=requester)
        self.assertTrue(any(r["status"] == "requires_review" for r in written))


if __name__ == "__main__":
    unittest.main()
