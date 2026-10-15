"""Regresión offline: errores transitorios de GitHub sin duplicar posts."""
import base64
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_noticias_media as media
import dr_noticias_instagram_pilot as pilot


def transient(code):
    return HTTPError("https://api.github.com/test", code, "temporary", {}, None)


class GithubSocialResilienceTests(unittest.TestCase):
    def test_get_reintenta_409_y_errores_5xx_sin_escritura(self):
        for status in (409, 500, 502, 503, 504):
            with self.subTest(status=status):
                with (patch.dict(media.os.environ, {"GITHUB_TOKEN": "dummy"}),
                      patch.object(media, "urlopen", side_effect=[
                          transient(status), io.BytesIO(b'{"sha":"exists"}')
                      ]) as open_url,
                      patch.object(media.time, "sleep") as pause):
                    record = media.github_api("/contents/test.json?ref=dr-noticias-media")
                self.assertEqual(record["sha"], "exists")
                self.assertEqual(open_url.call_count, 2)
                pause.assert_called_once()

    def test_no_reintenta_put_a_ciegas_ni_error_de_permiso(self):
        for method, status in (("PUT", 500), ("GET", 403)):
            with self.subTest(method=method, status=status):
                with (patch.dict(media.os.environ, {"GITHUB_TOKEN": "dummy"}),
                      patch.object(media, "urlopen", side_effect=transient(status)) as open_url,
                      patch.object(media.time, "sleep") as pause):
                    with self.assertRaisesRegex(RuntimeError, str(status)):
                        media.github_api("/contents/test", method=method,
                                         data={"x": 1} if method == "PUT" else None)
                open_url.assert_called_once()
                pause.assert_not_called()

    def test_404_no_existe_sin_reintento(self):
        with (patch.dict(media.os.environ, {"GITHUB_TOKEN": "dummy"}),
              patch.object(media, "urlopen", side_effect=transient(404)) as open_url):
            self.assertIsNone(media.github_api("/contents/absent"))
        open_url.assert_called_once()

    def test_ledger_500_y_409_reintenta_solo_si_la_ruta_sigue_libre(self):
        for code in (409, 500):
            with self.subTest(code=code):
                methods = []
                def gh(path, *, method="GET", data=None):
                    methods.append(method)
                    if len(methods) == 1:
                        raise RuntimeError("GitHub API HTTP " + str(code))
                    return None if method == "GET" else {"content": {"sha": "ours"}}
                with (patch.object(pilot, "github_api", side_effect=gh),
                      patch.object(pilot.time, "sleep") as pause):
                    self.assertEqual(pilot.ledger_write("dr-noticias-ledger/a.json",
                                                       {"status": "reserved"}), "ours")
                self.assertEqual(methods, ["PUT", "GET", "PUT"])
                pause.assert_called_once()

    def test_500_que_si_grabo_reserva_no_hace_segundo_put(self):
        record = {"status": "reserved", "article_url": "https://example.test/a"}
        remote = {"content": base64.b64encode(json.dumps(record).encode()).decode(),
                  "sha": "remote-confirmed"}
        methods = []
        def gh(path, *, method="GET", data=None):
            methods.append(method)
            if method == "PUT":
                raise RuntimeError("GitHub API HTTP 500")
            return remote
        with patch.object(pilot, "github_api", side_effect=gh):
            self.assertEqual(pilot.ledger_write("dr-noticias-ledger/a.json", record),
                             "remote-confirmed")
        self.assertEqual(methods, ["PUT", "GET"])

    def test_409_no_sobrescribe_reserva_de_otro_proceso(self):
        ours = {"status": "reserved", "article_url": "https://example.test/a"}
        theirs = {"content": base64.b64encode(json.dumps({
            "status": "reserved", "article_url": "https://example.test/b"
        }).encode()).decode(), "sha": "another"}
        methods = []
        def gh(path, *, method="GET", data=None):
            methods.append(method)
            if method == "PUT":
                raise RuntimeError("GitHub API HTTP 409")
            return theirs
        with patch.object(pilot, "github_api", side_effect=gh):
            with self.assertRaisesRegex(RuntimeError, "Registro modificado"):
                pilot.ledger_write("dr-noticias-ledger/a.json", ours)
        self.assertEqual(methods, ["PUT", "GET"])

    def test_finalizacion_requiere_sha_vigente(self):
        def gh(path, *, method="GET", data=None):
            if method == "PUT":
                raise RuntimeError("GitHub API HTTP 500")
            return {"sha": "sha-changed", "content": base64.b64encode(
                b'{"status":"reserved"}').decode()}
        with patch.object(pilot, "github_api", side_effect=gh):
            with self.assertRaisesRegex(RuntimeError, "Registro modificado"):
                pilot.ledger_write("dr-noticias-ledger/a.json",
                                   {"status": "published"}, sha="sha-original")

    def test_stage_jpeg_409_relee_antes_de_subir_de_nuevo(self):
        data = b"\xff\xd8\xffsample-jpeg"
        calls = []
        def gh(path, *, method="GET", data=None):
            calls.append(method)
            if method == "PUT" and calls.count("PUT") == 1:
                raise RuntimeError("GitHub API HTTP 409")
            if method == "GET":
                return None
            return {"content": {"sha": "new"}}
        with (patch.object(media, "ensure_media_branch"),
              patch.object(media, "github_api", side_effect=gh),
              patch.object(media, "read_url", return_value=(data, "image/jpeg")),
              patch.object(media.time, "sleep") as pause):
            url = media.stage_jpeg({"title": "a"}, data)
        self.assertIn(hashlib.sha256(data).hexdigest(), url)
        self.assertEqual(calls, ["GET", "PUT", "GET", "PUT"])
        pause.assert_called_once()

    def test_stage_jpeg_500_confirmado_no_reescribe_portada(self):
        data = b"\xff\xd8\xffsample-jpeg"
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data).hexdigest()
        calls = []
        def gh(path, *, method="GET", data=None):
            calls.append(method)
            if method == "PUT":
                raise RuntimeError("GitHub API HTTP 500")
            return None if len(calls) == 1 else {"type": "file", "sha": blob}
        with (patch.object(media, "ensure_media_branch"),
              patch.object(media, "github_api", side_effect=gh),
              patch.object(media, "read_url", return_value=(data, "image/jpeg"))):
            media.stage_jpeg({"title": "a"}, data)
        self.assertEqual(calls, ["GET", "PUT", "GET"])


if __name__ == "__main__":
    unittest.main()
