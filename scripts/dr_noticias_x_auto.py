#!/usr/bin/env python3
"""DR Noticias Tech -> X. Canal aislado, desactivado por defecto.

Solo noticias nuevas de Blogger, 3 por dia RD, 1 por ejecucion. Jamás incluye
URLs en el post. Modo scan: 0 llamadas a X, 0 escrituras en GitHub.
No modificar los publicadores existentes ni reintentar una publicacion incierta.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dr_noticias_media import (
    FEED, MEDIA_BRANCH, entries_from_feed, high_resolution_cover_urls,
    adequate_cover_quality, jpeg_from_png, github_api, read_url,
)
from dr_noticias_instagram_auto import (
    activation_instant, eligible_entries, editorial_ready,
)
from dr_noticias_instagram_pilot import EditorialParagraphs, ledger_write

ACCOUNT = "drnoticiastech"
X_URL = "https://api.x.com"
RD = ZoneInfo("America/Santo_Domingo")
DAILY_LIMIT = 3
MAX_TEXT = 235  # Margen bajo 280 caracteres ponderados, sin URLs.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
X_KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
URL_PATTERN = re.compile(
    r"(?i)(?:https?://|www\.|\b[\w-]+\.(?:com|net|org|do|io|app|ai|co)\b)"
)


def no_links(text):
    if URL_PATTERN.search(text):
        raise ValueError("X: URL detectada; está prohibido publicar enlaces")
    return text


def caption(title, html):
    title = re.sub(r"\s+", " ", title).strip()
    if not title or len(title) > 175:
        raise ValueError("X: titular vacío o demasiado largo")
    no_links(title)
    parser = EditorialParagraphs()
    parser.feed(html)
    paragraphs = list(dict.fromkeys(parser.paragraphs))
    lead = next((p for p in paragraphs if len(p) >= 55), "")
    if not lead:
        raise ValueError("X: no hay contenido editorial verificable")
    lead = re.sub(r"\s+", " ", lead).strip()
    no_links(lead)
    low = title.lower()
    topic = ("#Gaming" if any(w in low for w in ("juego", "gaming", "nintendo", "xbox", "playstation"))
             else "#Android" if "android" in low or "samsung" in low
             else "#Apple" if "iphone" in low or "apple" in low
             else "#Tecnologia")
    tags = " #DRNoticiasTech " + topic
    top = title + "\n\n"
    remaining = MAX_TEXT - len(top) - len(tags)
    if remaining < 48:
        raise ValueError("X: titular deja poco espacio para la noticia")
    if len(lead) > remaining:
        cut = lead[:remaining - 1]
        lead = cut.rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    result = top + lead + tags
    if len(result) > MAX_TEXT or len(lead) < 30:
        raise ValueError("X: texto fuera de límites")
    return no_links(result)


def ledger_path(article_url):
    digest = hashlib.sha256((ACCOUNT + ":" + article_url).encode("utf-8")).hexdigest()
    return "dr-noticias-x-ledger/" + digest + ".json"


def slot_path(day, slot):
    if slot not in (1, 2, 3):
        raise ValueError("X: cupo fuera de rango")
    return "dr-noticias-x-daily/" + str(day) + "/slot-" + str(slot) + ".json"


def read_ledger(path, gh=None):
    gh = gh or github_api
    return gh("/contents/" + quote(path) + "?ref=" + MEDIA_BRANCH)


def first_free_slot(day, gh=None):
    for i in range(1, DAILY_LIMIT + 1):
        if read_ledger(slot_path(day, i), gh) is None:
            return i
    return None


def credentials(env):
    missing = [name for name in X_KEYS if not env.get(name)]
    if missing:
        raise ValueError("X: faltan credenciales: " + ", ".join(missing))
    return tuple(env[name].strip() for name in X_KEYS)


def x_request(method, path, env, payload=None):
    """OAuth 1.0a firmado, sin tokens en URLs o logs y sin redirects."""
    if path not in ("/2/users/me", "/2/media/upload", "/2/tweets"):
        raise ValueError("X: endpoint no autorizado")
    if (method, path) not in (
        ("GET", "/2/users/me"),
        ("POST", "/2/media/upload"),
        ("POST", "/2/tweets"),
    ):
        raise ValueError("X: método no autorizado")
    from requests import request
    from requests_oauthlib import OAuth1
    from requests.exceptions import RequestException
    keys = credentials(env)
    try:
        response = request(
            method, X_URL + path,
            auth=OAuth1(keys[0], keys[1], keys[2], keys[3]),
            json=payload if method == "POST" else None,
            timeout=35, allow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "DRNoticiasTechXPublisher/1.0"},
        )
    except RequestException:
        raise RuntimeError("X: error de red, publicación incierta; NO reintentar") from None
    expected = 201 if path == "/2/tweets" else 200
    if response.status_code != expected:
        # No leer/imprimir el body de X, podría contener datos sensibles.
        raise RuntimeError("X API HTTP " + str(response.status_code) +
                           "; no reintentar sin revisión") from None
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError("X: respuesta JSON inválida, no reintentar") from None
    if not isinstance(body, dict):
        raise RuntimeError("X: formato de respuesta inesperado")
    return body


def verify_account(env, request_fn=x_request):
    result = request_fn("GET", "/2/users/me", env)
    username = str(result.get("data", {}).get("username", "")).lower().lstrip("@")
    if username != ACCOUNT:
        raise RuntimeError("X: cuenta OAuth distinta de @drnoticiastech; publicación bloqueada")


def upload_image(image, env, request_fn=x_request):
    if not image.startswith(b"\xff\xd8\xff") or not 0 < len(image) <= MAX_IMAGE_BYTES:
        raise ValueError("X: portada JPEG incompatible (>5 MB o corrupta)")
    result = request_fn("POST", "/2/media/upload", env, {
        "media": base64.b64encode(image).decode("ascii"),
        "media_category": "tweet_image",
    })
    data = result.get("data", {})
    media_id = str(data.get("id") or data.get("media_id") or data.get("media_id_string") or "")
    if not media_id.isdecimal():
        raise RuntimeError("X: carga de portada sin media_id confirmado")
    return media_id


def publish_post(text, media_id, env, request_fn=x_request):
    no_links(text)
    if len(text) > MAX_TEXT or not media_id.isdecimal():
        raise ValueError("X: publicación inválida")
    result = request_fn("POST", "/2/tweets", env, {
        "text": text, "media": {"media_ids": [media_id]}
    })
    post_id = str(result.get("data", {}).get("id") or "")
    if not post_id.isdecimal():
        raise RuntimeError("X: creación sin ID confirmado; revisión manual")
    return post_id


def image_for_article(post, fetch=None):
    fetch = fetch or read_url
    cover = post.get("cover")
    if not cover:
        raise ValueError("X: noticia sin portada")
    for candidate in high_resolution_cover_urls(cover):
        try:
            raw, _ = fetch(candidate)
            if adequate_cover_quality(raw):
                image = jpeg_from_png(raw)
                if len(image) <= MAX_IMAGE_BYTES:
                    return image
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            continue
    raise ValueError("X: portada original insuficiente o supera 5 MB")


def run(mode, env=None, now=None, gh=None, writer=None,
        fetch=None, requester=None):
    if mode not in ("scan", "run"):
        raise ValueError("X: modo inválido")
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("X: fecha sin zona")
    gh = gh or github_api
    writer = writer or ledger_write
    fetch = fetch or read_url
    requester = requester or x_request
    if mode == "run":
        if (env.get("DR_NOTICIAS_X_AUTO_ENABLED") != "true" or
                env.get("GITHUB_ACTIONS") != "true" or
                env.get("GITHUB_REF") != "refs/heads/main" or
                not env.get("GITHUB_TOKEN")):
            raise ValueError("X: publicación desactivada o fuera de main")
        credentials(env)
        # Inicio propio de X: impide publicar noticias anteriores a la activación.
        start = activation_instant(env.get("DR_NOTICIAS_X_START_UTC", ""))
    else:
        start = (activation_instant(env["DR_NOTICIAS_X_START_UTC"])
                 if env.get("DR_NOTICIAS_X_START_UTC")
                 else now - timedelta(hours=8))
    body, _ = fetch(FEED, max_bytes=1600000)
    feed = json.loads(body)
    posts = eligible_entries(entries_from_feed(feed, now=now), now, start)
    print("DR Noticias Tech | X | " + mode.upper() +
          " | noticias candidatas=" + str(len(posts)))
    day = now.astimezone(RD).date()
    if mode == "run" and first_free_slot(day, gh) is None:
        print("X: cupos diarios agotados (3).")
        return
    for post in posts:
        if mode == "run" and read_ledger(ledger_path(post["url"]), gh) is not None:
            continue
        try:
            entry = editorial_ready(feed, post)
            message = caption(post["title"], entry.get("content", {}).get("$t", ""))
        except ValueError:
            continue
        if mode == "scan":
            print("SOLO VISTA PREVIA: " + message.replace("\n", " | "))
            continue
        try:
            image = image_for_article(post, fetch)
        except ValueError:
            print("X: noticia omitida por falta de portada válida.")
            continue
        slot = first_free_slot(day, gh)
        if slot is None:
            return
        # Bloqueo permanente incluso si X devuelve timeout tras aceptar la publicación.
        record = {
            "platform": "x", "status": "reserved", "account": ACCOUNT,
            "article_url": post["url"],
            "caption_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "created_at": now.isoformat(),
        }
        path = ledger_path(post["url"])
        if read_ledger(path, gh) is not None or read_ledger(slot_path(day, slot), gh) is not None:
            raise RuntimeError("X: reserva existente, no reintentar")
        sha = writer(path, record)
        writer(slot_path(day, slot), {
            "status": "reserved", "platform": "x",
            "article_url": post["url"], "created_at": now.isoformat(), "slot": slot
        })
        try:
            verify_account(env, requester)
            media_id = upload_image(image, env, requester)
            tweet_id = publish_post(message, media_id, env, requester)
            record.update(status="published", x_post_id=tweet_id,
                          published_at=datetime.now(timezone.utc).isoformat())
            writer(path, record, sha=sha)
            print("X: UNA publicación confirmada. ID=" + tweet_id)
            return
        except Exception:
            record["status"] = "requires_review"
            try:
                writer(path, record, sha=sha)
            except Exception:
                print("X: reserva bloqueada; fallo registrando revisión")
            raise RuntimeError("X: intento bloqueado para revisión manual; nunca reintentar automáticamente") from None
    print("X: sin otras noticias aptas; no se ha publicado.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("scan", "run"), default="scan")
    args = p.parse_args()
    try:
        run(args.mode)
    except (HTTPError, URLError, RuntimeError, ValueError, KeyError,
            TypeError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        print("ERROR X: " + str(exc)[:200], file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
