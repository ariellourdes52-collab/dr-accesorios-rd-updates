"""Pruebas aisladas del diagnóstico OAuth: cero solicitudes de red."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr_noticias_x_auth_check as auth

FAKE_KEYS = {
    "X_API_KEY": "mock-key",
    "X_API_SECRET": "mock-secret",
    "X_ACCESS_TOKEN": "mock-token",
    "X_ACCESS_TOKEN_SECRET": "mock-token-secret",
}


class OAuthDiagnosticTests(unittest.TestCase):
    def capture(self, env, fn):
        output = io.StringIO()
        with redirect_stdout(output):
            result = auth.diagnose(env, fn)
        return result, output.getvalue()

    def test_verified_account_is_read_only(self):
        calls = []
        def fetch(method, path, env):
            calls.append((method, path))
            return {"data": {"username": "drnoticiastech", "id": "987"}}
        ok, message = self.capture(FAKE_KEYS, fetch)
        self.assertTrue(ok)
        self.assertEqual(calls, [("GET", "/2/users/me")])
        self.assertIn("verified", message)
        self.assertNotIn("mock-secret", message)

    def test_account_mismatch_is_rejected(self):
        ok, out = self.capture(FAKE_KEYS, lambda *a: {"data": {"username": "otroperfil"}})
        self.assertFalse(ok)
        self.assertIn("account_mismatch", out)
        self.assertIn("actual=@otroperfil", out)

    def test_missing_credentials_never_make_a_request(self):
        ok, out = self.capture({}, lambda *a: self.fail("NO REQUEST"))
        self.assertFalse(ok)
        self.assertIn("missing_credentials", out)

    def test_api_status_safe_no_response_body_leaks(self):
        for status in ("401", "403", "429"):
            def fetch(*args, status=status):
                raise RuntimeError("X API HTTP " + status + "; no reintentar sin revisión")
            ok, out = self.capture(FAKE_KEYS, fetch)
            self.assertFalse(ok)
            self.assertIn("code=http_" + status, out)
            self.assertNotIn("mock-token", out)

    def test_untrusted_errors_are_redacted(self):
        def fail(*args):
            raise RuntimeError("unexpected: mock-secret")
        ok, out = self.capture(FAKE_KEYS, fail)
        self.assertFalse(ok)
        self.assertEqual(out.strip(), "X_AUTH: failed code=unexpected")

    def test_main_requires_actions_and_main_without_request(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "GITHUB_REF": "refs/heads/main"}):
            self.assertEqual(auth.main(), 1)
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/dev"}):
            self.assertEqual(auth.main(), 1)


if __name__ == "__main__":
    unittest.main()
