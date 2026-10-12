import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_noticias_instagram_auto as auto


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 13, 14, 30, tzinfo=timezone.utc)
        self.start = datetime(2026, 10, 13, 0, tzinfo=timezone.utc)

    def post(self, suffix, published):
        return {"title": "Noticia " + suffix,
                "url": "https://draccesoriosrd.blogspot.com/2026/10/" + suffix + ".html",
                "cover": "https://blogger.googleusercontent.com/a.jpg",
                "published": published.isoformat()}

    def test_fecha_requiere_zona_y_no_acepta_vacia(self):
        self.assertEqual(auto.activation_instant("2026-10-13T00:00:00-04:00"),
                         datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc))
        for bad in ("", "not-a-date", "2026-10-13T00:00:00"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                auto.activation_instant(bad)

    def test_no_publica_noticias_anteriores_a_activacion(self):
        posts = [
            self.post("before", self.start - timedelta(minutes=1)),
            self.post("old", self.now - timedelta(hours=9)),
            self.post("new", self.now - timedelta(minutes=20)),
            self.post("future", self.now + timedelta(minutes=10))
        ]
        result = auto.eligible_entries(posts, self.now, self.start)
        self.assertEqual([p["title"] for p in result], ["Noticia new"])

    def test_orden_de_publicacion_de_la_mas_antigua_a_la_mas_reciente(self):
        posts = [self.post("latest", self.now - timedelta(minutes=10)),
                 self.post("earliest", self.now - timedelta(minutes=50))]
        self.assertEqual([p["title"] for p in auto.eligible_entries(posts,self.now,self.start)],
                         ["Noticia earliest", "Noticia latest"])

    def test_no_publica_si_la_fecha_inicio_aun_no_llega(self):
        future_start = self.now + timedelta(hours=1)
        self.assertEqual(auto.eligible_entries([self.post("x", self.now)],self.now,future_start),[])

    def test_estricto_limite_tres_cupos_diarios(self):
        hits = set()
        def gh(path, *, method="GET", data=None):
            key = path.split("?")[0]
            return {"sha": "x"} if key in hits else None
        date = self.now.date()
        with patch.object(auto, "github_api", side_effect=gh):
            self.assertEqual(auto.available_daily_slot(date), 1)
            hits.add("/contents/dr-noticias-daily/2026-10-13/slot-1.json")
            self.assertEqual(auto.available_daily_slot(date), 2)
            hits.add("/contents/dr-noticias-daily/2026-10-13/slot-2.json")
            self.assertEqual(auto.available_daily_slot(date), 3)
            hits.add("/contents/dr-noticias-daily/2026-10-13/slot-3.json")
            self.assertIsNone(auto.available_daily_slot(date))


if __name__ == "__main__":
    unittest.main()
