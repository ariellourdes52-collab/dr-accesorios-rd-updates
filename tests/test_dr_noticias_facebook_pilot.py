import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_noticias_facebook_pilot as facebook


class FacebookPilotTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://draccesoriosrd.blogspot.com/2026/10/nintendo-switch-2-juegos-fisicos-latinoamerica.html"
        self.post = {"url": self.url, "title": "Nintendo no abandonará los juegos físicos en Switch 2"}
        self.sha = "a" * 64

    def test_page_only_validates_exact_id_and_brand(self):
        def valid(path, token):
            self.assertEqual(path, "/me")
            return {"id": facebook.PAGE_ID, "name": "DR Noticias Tech  DR Accesorios RD"}
        self.assertEqual(facebook.verify_page("token", request=valid)["id"], facebook.PAGE_ID)
        for response in [
            {"id": "123456", "name": "DR Noticias Tech  DR Accesorios RD"},
            {"id": facebook.PAGE_ID, "name": "DR Accesorios RD tienda comercial"}
        ]:
            with self.assertRaises(RuntimeError):
                facebook.verify_page("token", request=lambda *args: response)

    def test_facebook_text_keeps_editorial_and_direct_blogger_link(self):
        content = ("📰 Nintendo no abandonará los juegos físicos en Switch 2\n\n"
                   "Nintendo mantendrá los cartuchos y hay varias diferencias prácticas.\n\n"
                   "Lee la noticia completa desde el enlace de nuestra biografía.\n\n"
                   "#Nintendo #Switch2 #TecnologiaRD #NoticiasRD #DRNoticiasTech")
        result = facebook.facebook_caption(content, self.url)
        self.assertIn(self.url, result)
        self.assertNotIn("enlace de nuestra biografía", result)
        self.assertEqual(len([x for x in result.split() if x.startswith("#")]), 5)

    def test_rejects_external_links_and_malformed_template(self):
        base = "Lee la noticia completa desde el enlace de nuestra biografía."
        with self.assertRaises(ValueError):
            facebook.facebook_caption(base, "https://evil.com/x")
        with self.assertRaises(ValueError):
            facebook.facebook_caption("sin marcador", self.url)

    def test_facebook_ledger_is_distinct_from_instagram(self):
        path = facebook.facebook_ledger_path(self.url)
        self.assertTrue(path.startswith("dr-noticias-facebook-ledger/"))
        from dr_noticias_instagram_pilot import ledger_path
        self.assertNotEqual(path, ledger_path(self.url))

    def test_publishes_one_photo_and_blocks_second_attempt(self):
        reserved = {}
        used = []
        def gh(path, *, method="GET", data=None):
            if method == "GET":
                return {"sha": "blob"} if path.split("?")[0] in reserved else None
            raise AssertionError("unexpected GitHub API call")
        def ledger_write(path, record, sha=None):
            reserved["/contents/" + path] = True
            return "test_sha"
        def post_graph(path, token, form=None):
            used.append((path, form))
            return {"id": "56789012345", "post_id": "875159429020764_56789012345"}
        with patch.object(facebook, "github_api", side_effect=gh), patch.object(
                facebook, "ledger_write", side_effect=ledger_write):
            facebook.publish(self.post, self.sha, "https://example.org/test.jpg",
                             "texto Facebook", "test_token", request=post_graph)
            self.assertEqual(len(used), 1)
            self.assertEqual(used[0][0], "/" + facebook.PAGE_ID + "/photos")
            self.assertEqual(used[0][1]["published"], "true")
            self.assertIn("caption", used[0][1])
            with self.assertRaisesRegex(RuntimeError, "ya registrada"):
                facebook.publish(self.post, self.sha, "https://example.org/test.jpg",
                                 "texto Facebook", "test_token", request=post_graph)
            self.assertEqual(len(used), 1)

    def test_failure_keeps_ledger_reserved_and_requires_review(self):
        saved = {}
        def gh(path, *, method="GET", data=None):
            return {"sha": "blob"} if path.split("?")[0] in saved else None
        def ledger_write(path, record, sha=None):
            saved["/contents/" + path] = record["status"]
            return "test_sha"
        with patch.object(facebook, "github_api", side_effect=gh), patch.object(
                facebook, "ledger_write", side_effect=ledger_write):
            with self.assertRaisesRegex(RuntimeError, "unknown"):
                facebook.publish(self.post, self.sha, "https://example.org/test.jpg",
                                 "texto Facebook", "token",
                                 request=lambda *args: (_ for _ in ()).throw(RuntimeError("unknown")))
            self.assertIn("requires_review", saved.values())
            with self.assertRaisesRegex(RuntimeError, "ya registrada"):
                facebook.publish(self.post, self.sha, "https://example.org/test.jpg",
                                 "texto Facebook", "token", request=lambda *args: None)


if __name__ == "__main__":
    unittest.main()
