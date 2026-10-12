"""Pruebas locales para preparar imágenes de DR Noticias Tech sin acceso a redes."""
import io
import unittest
from datetime import datetime, timezone
from PIL import Image

from scripts.dr_noticias_media import (CoverParser, entries_from_feed, jpeg_from_png,
                                     trusted_post, high_resolution_cover_urls,
                                     adequate_cover_quality)


class MediaTests(unittest.TestCase):
    def png(self, size, color=(50, 5, 40, 255)):
        image = Image.new("RGBA", size, color)
        memory = io.BytesIO()
        image.save(memory, format="PNG")
        return memory.getvalue()

    def test_jpeg_real_y_fondo_fucsia_claro(self):
        result = jpeg_from_png(self.png((1200, 675)))
        self.assertTrue(result.startswith(b"\xff\xd8\xff"))
        with Image.open(io.BytesIO(result)) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertEqual(im.size, (1200, 675))

    def test_ajusta_imagenes_extremas_sin_cortar(self):
        for shape in [(2000, 400), (300, 1800)]:
            with self.subTest(shape=shape):
                output = jpeg_from_png(self.png(shape))
                with Image.open(io.BytesIO(output)) as im:
                    self.assertGreaterEqual(im.width / im.height, 0.8)
                    self.assertLessEqual(im.width / im.height, 1.91)

    def test_restaura_resolucion_original_blogger(self):
        original = "https://blogger.googleusercontent.com/img/b/test/w400-h225/portada.png"
        candidates = high_resolution_cover_urls(original)
        self.assertEqual(candidates[0], "https://blogger.googleusercontent.com/img/b/test/s1600/portada.png")
        self.assertEqual(candidates[1], original)
        image = "https://blogger.googleusercontent.com/img/b/test/s320/portada.png"
        self.assertEqual(high_resolution_cover_urls(image)[0],
                         "https://blogger.googleusercontent.com/img/b/test/s1600/portada.png")
        self.assertEqual(high_resolution_cover_urls("https://blogger.googleusercontent.com/img/a/test/photo=w400-h225")[0],
                         "https://blogger.googleusercontent.com/img/a/test/photo=s1600")
        self.assertRaises(ValueError, high_resolution_cover_urls, "https://otro-dominio.com/s320/portada.png")

    def test_bloquea_miniaturas_de_baja_resolucion(self):
        self.assertFalse(adequate_cover_quality(self.png((400, 225))))
        self.assertTrue(adequate_cover_quality(self.png((1200, 675))))
        self.assertTrue(adequate_cover_quality(self.png((800, 1000))))

    def test_portadas_solo_proveedor_blogger(self):
        p = CoverParser()
        p.feed('<img src="https://falso.com/x.png"><img src="https://blogger.googleusercontent.com/a.png">')
        self.assertEqual(p.cover, "https://blogger.googleusercontent.com/a.png")
        self.assertTrue(trusted_post("https://draccesoriosrd.blogspot.com/2026/10/mi-noticia.html"))
        self.assertFalse(trusted_post("https://untrusted.example/2026/10/mi-noticia.html"))
        self.assertFalse(trusted_post("http://draccesoriosrd.blogspot.com/2026/10/mi-noticia.html"))

    def test_solo_noticias_recientes(self):
        def entry(id_, published):
            return {
                "title": {"$t": "Noticia " + id_},
                "published": {"$t": published},
                "link": [{"rel": "alternate", "type": "text/html",
                          "href": "https://draccesoriosrd.blogspot.com/2026/10/noticia-" + id_ + ".html"}],
                "content": {"$t": '<img src="https://blogger.googleusercontent.com/post.png">'}
            }
        now = datetime(2026, 10, 12, 20, tzinfo=timezone.utc)
        result = entries_from_feed({"feed": {"entry": [
            entry("nueva", "2026-10-12T15:00:00-04:00"),
            entry("antigua", "2026-10-05T15:00:00-04:00")
        ]}}, now=now)
        self.assertEqual(len(result), 1)
        self.assertIn("nueva", result[0]["url"])


if __name__ == "__main__":
    unittest.main()
