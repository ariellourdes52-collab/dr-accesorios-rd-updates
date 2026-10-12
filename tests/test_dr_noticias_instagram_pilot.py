import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_noticias_instagram_pilot as pilot


class PilotTests(unittest.TestCase):
    def test_caption_y_cinco_hashtags(self):
        html = (
            "<style>SECRETO CSS</style><p>Nintendo mantendrá los juegos físicos de "
            "Switch 2 en un escenario donde las tiendas digitales ganan terreno. "
            "La compañía afirma que las preferencias de sus jugadores siguen siendo "
            "fundamentales para decidir qué formatos conservar.</p>"
            "<p>Para República Dominicana, los cartuchos tradicionales permiten "
            "coleccionar y prestar videojuegos en determinadas condiciones. "
            "Las tarjetas llave requieren descargar los datos, una diferencia "
            "importante que el consumidor debe conocer antes de comprar.</p>"
            "<p>Este movimiento refleja que la transición digital no afecta "
            "a todas las comunidades de la misma manera y que Nintendo seguirá "
            "ofreciendo alternativas mientras exista demanda.</p>"
            "<script>FILTRAR ESTO</script>"
        )
        result = pilot.make_caption("Nintendo no abandonará los juegos físicos en Switch 2", html)
        self.assertNotIn("SECRETO CSS", result)
        self.assertNotIn("FILTRAR ESTO", result)
        self.assertEqual(len([x for x in result.split() if x.startswith("#")]), 5)
        self.assertIn("enlace de nuestra biografía", result)
        self.assertIn("#DRNoticiasTech", result)
        self.assertTrue(450 <= len(result) <= 1700)

    def test_no_inventar_contenido_si_no_hay_parrafos(self):
        with self.assertRaises(ValueError):
            pilot.make_caption("Titulo", "<p>Hola</p><img src='foo'>")

    def test_fijar_unicamente_cuenta_esperada(self):
        with patch.object(pilot, "meta", return_value={
            "id": pilot.EXPECTED_ID,
            "username": pilot.EXPECTED_NAME,
            "account_type": "BUSINESS"
        }):
            pilot.check_account("dummy")
        with patch.object(pilot, "meta", return_value={
            "id": "999", "username": "draccesoriosrd", "account_type": "BUSINESS"
        }):
            with self.assertRaises(RuntimeError):
                pilot.check_account("dummy")

    def test_bloquea_segundo_intento_de_misma_noticia(self):
        storage = {}
        creates = []
        def gh(path, *, method="GET", data=None):
            if method == "GET":
                return storage.get(path.split("?")[0])
            if method == "PUT":
                storage[path] = {"sha": "blob1"}
                return {"content": {"sha": "blob1"}}
        def fake_meta(path, token, data=None):
            creates.append(path)
            if path.endswith("/media"):
                return {"id": "123456789"}
            if "status_code" in path:
                return {"status_code": "FINISHED"}
            if path.endswith("/media_publish"):
                return {"id": "987654321"}
            raise AssertionError(path)
        post = {"url": "https://draccesoriosrd.blogspot.com/2026/10/test-post.html",
                "title": "Noticia de prueba"}
        with patch.object(pilot, "github_api", side_effect=gh), patch.object(
                pilot, "meta", side_effect=fake_meta):
            pilot.publish(post, "a" * 64, "https://example.com/test.jpg", "texto", "dummy")
            self.assertEqual(len(creates), 3)
            with self.assertRaisesRegex(RuntimeError, "Ya existe una reserva"):
                pilot.publish(post, "a" * 64, "https://example.com/test.jpg", "texto", "dummy")
            self.assertEqual(len(creates), 3)

    def test_fallo_persistente_no_reintenta_publicar(self):
        storage = {}
        def gh(path, *, method="GET", data=None):
            if method == "GET":
                return storage.get(path.split("?")[0])
            storage[path] = {"sha": "blob1"}
            return {"content": {"sha": "blob1"}}
        post = {"url": "https://draccesoriosrd.blogspot.com/2026/10/test-error.html",
                "title": "Noticia de prueba"}
        with patch.object(pilot, "github_api", side_effect=gh), patch.object(
                pilot, "meta", side_effect=RuntimeError("fallo Meta")):
            with self.assertRaisesRegex(RuntimeError, "fallo Meta"):
                pilot.publish(post, "a" * 64, "https://example.com/test.jpg", "texto", "dummy")
            with self.assertRaisesRegex(RuntimeError, "Ya existe una reserva"):
                pilot.publish(post, "a" * 64, "https://example.com/test.jpg", "texto", "dummy")


if __name__ == "__main__":
    unittest.main()
