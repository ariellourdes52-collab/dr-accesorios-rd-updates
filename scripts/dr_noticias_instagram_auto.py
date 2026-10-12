#!/usr/bin/env python3
"""DR Noticias Tech: solo publica noticias NUEVAS de Blogger tras activación.

Falla cerrado ante errores. Nunca reintenta una publicación incierta.
No toca DR Radar, DR Audio, DR Agenda, Android, Blogger ni otros Instagram.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dr_noticias_media import (
    FEED, high_resolution_cover_urls, adequate_cover_quality,
    entries_from_feed, github_api, jpeg_from_png, read_url, stage_jpeg
)
from dr_noticias_instagram_pilot import (
    check_account, ledger_path, make_caption, ready_post, publish, EXPECTED_NAME
)

DOMINICAN_TZ = ZoneInfo("America/Santo_Domingo")
MAX_AGE_SECONDS = 8 * 60 * 60
DAILY_MAX = 3


def activation_instant(value):
    if not value:
        raise ValueError("Configurar DR_NOTICIAS_AUTO_START_UTC antes de activar")
    try:
        start = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Fecha de inicio inválida, usar ISO 8601 con zona horaria") from None
    if start.tzinfo is None:
        raise ValueError("DR_NOTICIAS_AUTO_START_UTC debe incluir zona horaria")
    return start.astimezone(timezone.utc)


def eligible_entries(posts, now, start):
    if start > now:
        return []
    fresh = []
    for p in posts:
        try:
            published = datetime.fromisoformat(p["published"]).astimezone(timezone.utc)
            age = (now - published).total_seconds()
            if published >= start and 0 <= age <= MAX_AGE_SECONDS:
                fresh.append(p)
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(fresh, key=lambda p: (p["published"], p["url"]))


def daily_path(date, slot):
    return "dr-noticias-daily/" + date + "/slot-" + str(slot) + ".json"


def available_daily_slot(localdate):
    day = str(localdate)
    for slot in range(1, DAILY_MAX + 1):
        if github_api("/contents/" + quote(daily_path(day, slot)) +
                      "?ref=dr-noticias-media") is None:
            return slot
    return None


def reserve_daily_slot(localdate, slot, post_url):
    path = daily_path(str(localdate), slot)
    if github_api("/contents/" + quote(path) + "?ref=dr-noticias-media") is not None:
        raise RuntimeError("Reserva diaria ya existente; abortar, no reintentar")
    from dr_noticias_instagram_pilot import ledger_write
    return ledger_write(path, {
        "status": "reserved",
        "account": EXPECTED_NAME,
        "article_url": post_url,
        "date": str(localdate),
        "slot": slot,
        "created_at": datetime.now(timezone.utc).isoformat()
    })


def editorial_ready(feed, post):
    entry = next((e for e in feed.get("feed", {}).get("entry", [])
                  if any(link.get("rel") == "alternate" and
                         link.get("href") == post["url"] for link in e.get("link", []))),
                 None)
    if not entry or not post.get("cover"):
        raise ValueError("La noticia no tiene portada y texto verificables")
    make_caption(post["title"], entry.get("content", {}).get("$t", ""))
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["scan", "run"], default="scan")
    args = parser.parse_args()
    active = os.environ.get("DR_NOTICIAS_AUTO_ENABLED", "").strip().lower() == "true"
    if args.mode == "run":
        if (not active or os.environ.get("GITHUB_ACTIONS") != "true" or
                os.environ.get("GITHUB_REF") != "refs/heads/main"):
            raise ValueError("AUTOMATIZACIÓN DESACTIVADA; publicación cancelada")
        if not os.environ.get("GITHUB_TOKEN"):
            raise ValueError("Sin permiso de GitHub para el ledger")
    start = activation_instant(os.environ.get("DR_NOTICIAS_AUTO_START_UTC", ""))
    now = datetime.now(timezone.utc)
    token = os.environ.get("DR_NOTICIAS_IG_ACCESS_TOKEN", "")
    check_account(token)
    raw, _ = read_url(FEED, max_bytes=1600000)
    feed = json.loads(raw)
    posts = eligible_entries(entries_from_feed(feed, now=now), now, start)
    print("DR NOTICIAS TECH | " + args.mode.upper() + " | destino @" + EXPECTED_NAME)
    print("Inicio UTC: " + start.isoformat())
    print("Noticias elegibles: " + str(len(posts)))
    if not posts:
        print("Sin publicaciones nuevas; fin sin cambios.")
        return
    localday = now.astimezone(DOMINICAN_TZ).date()
    if args.mode == "run" and available_daily_slot(localday) is None:
        print("Límite de tres publicaciones automáticas diarias alcanzado.")
        return
    for post in posts:
        if args.mode == "run":
            # Siempre consultar el registro ANTES de preparar archivos o hablar con Meta.
            if github_api("/contents/" + quote(ledger_path(post["url"])) +
                          "?ref=dr-noticias-media") is not None:
                continue
        try:
            editorial_ready(feed, post)
        except ValueError as e:
            print("Omitida por control editorial: " + post["title"][:80] +
                  " (" + str(e)[:80] + ")")
            continue
        if args.mode == "scan":
            print("Candidata (SOLO LECTURA): " + post["title"][:100])
            continue
        if not post.get("cover"):
            continue
        # El preparador usa solo HTTPS de Blogger, verifica originales HD y JPEG.
        raw_image = None
        for url in high_resolution_cover_urls(post["cover"]):
            try:
                pic, _ = read_url(url)
                if adequate_cover_quality(pic):
                    raw_image = pic
                    break
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                continue
        if raw_image is None:
            print("Sin imagen HD; se omite noticia sin publicar.")
            continue
        jpeg = jpeg_from_png(raw_image)
        stage_jpeg(post, jpeg)  # RAMA multimedia, NO publicaciones en Instagram
        found_post, digest, image_url, caption = ready_post(feed, post["url"])
        if found_post["url"] != post["url"]:
            raise RuntimeError("Cambió la noticia durante la preparación")
        if digest != hashlib.sha256(jpeg).hexdigest():
            raise RuntimeError("Cambió la portada durante la preparación, abortado")
        slot = available_daily_slot(localday)
        if slot is None:
            print("Límite diario alcanzado antes de publicar.")
            return
        # Se reserva un cupo de los 3 incluso si Meta falla, para evitar spam.
        reserve_daily_slot(localday, slot, post["url"])
        publish(found_post, digest, image_url, caption, token)
        print("Automática completada. Un artículo por ejecución; próximos en otra hora.")
        return
    if args.mode == "scan":
        print("No se publica, no se convierten ni se suben imágenes.")
    else:
        print("Sin artículos nuevos publicables; 0 publicaciones.")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, RuntimeError, ValueError, KeyError,
            TypeError, OSError, TimeoutError) as e:
        print("ERROR de automatización: " + str(e)[:200], file=sys.stderr)
        sys.exit(1)
