#!/usr/bin/env python3
"""Comprobación manual de credenciales X (OAuth 1.0a).

Hace UNA petición GET /2/users/me, no realiza publicaciones ni subida
de imágenes ni escrituras de GitHub. No revela secretos ni respuestas crudas.
Puede consumir créditos según las tarifas vigentes de X.
"""
import os
import re
import sys

from dr_noticias_x_auto import ACCOUNT, credentials, safe_error_code, x_request


def diagnose(env=None, request_fn=x_request):
    env = os.environ if env is None else env
    try:
        credentials(env)
    except ValueError:
        print("X_AUTH: missing_credentials")
        return False
    try:
        response = request_fn("GET", "/2/users/me", env)
    except Exception as error:
        print("X_AUTH: failed code=" + safe_error_code(error))
        return False

    user = response.get("data", {}) if isinstance(response, dict) else {}
    username = user.get("username", "") if isinstance(user, dict) else ""
    username = username.strip().lower().lstrip("@") if isinstance(username, str) else ""
    if not re.fullmatch(r"[a-z0-9_]{1,15}", username):
        print("X_AUTH: failed code=unexpected_account_response")
        return False
    if username != ACCOUNT:
        # Handle público, no datos privados ni tokens.
        print("X_AUTH: failed code=account_mismatch expected=@" + ACCOUNT +
              " actual=@" + username)
        return False
    print("X_AUTH: verified account=@" + ACCOUNT)
    print("X_AUTH: no media upload; no post; no GitHub ledger writes")
    return True


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or (
            os.environ.get("GITHUB_REF") != "refs/heads/main"):
        print("X_AUTH: blocked outside GitHub Actions/main")
        return 1
    return 0 if diagnose() else 1


if __name__ == "__main__":
    sys.exit(main())
