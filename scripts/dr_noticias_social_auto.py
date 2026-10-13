#!/usr/bin/env python3
"""DR Noticias Tech: Facebook/Threads automáticos sin tocar flujo Instagram.

Cada canal posee variable de activación, reservas diarias, ledger y cron propios.
Publica solo noticias Blogger posteriores al inicio configurado, edad <= 8h,
máximo tres/día/canal y una noticia por ejecución. Cualquier error falla cerrado.
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
    FEED, MEDIA_BRANCH, adequate_cover_quality, entries_from_feed, github_api,
    high_resolution_cover_urls, jpeg_from_png, read_url, stage_jpeg
)
from dr_noticias_instagram_auto import (
    MAX_AGE_SECONDS, activation_instant, eligible_entries, editorial_ready
)
from dr_noticias_instagram_pilot import MEDIA_BASE, make_caption, ledger_write
from dr_noticias_facebook_pilot import (
    PAGE_ID, facebook_caption, facebook_ledger_path, publish as facebook_publish,
    verify_page
)
from dr_noticias_threads_pilot import (
    ACCOUNT_ID, threads_caption, threads_ledger_path,
    publish as threads_publish, verify_account
)

RD_TZ = ZoneInfo("America/Santo_Domingo")
DAILY_MAX = 3
PLATFORMS = {
    "facebook": {
        "enabled": "DR_NOTICIAS_FB_AUTO_ENABLED",
        "token": "DR_NOTICIAS_FB_PAGE_TOKEN",
        "account": PAGE_ID,
        "ledger_path": facebook_ledger_path,
        "verify": verify_page,
        "caption": facebook_caption,
        "publish": facebook_publish
    },
    "threads": {
        "enabled": "DR_NOTICIAS_THREADS_AUTO_ENABLED",
        "token": "DR_NOTICIAS_THREADS_ACCESS_TOKEN",
        "account": ACCOUNT_ID,
        "ledger_path": threads_ledger_path,
        "verify": verify_account,
        "caption": threads_caption,
        "publish": threads_publish
    }
}


def daily_path(platform, local_date, slot):
    if platform not in PLATFORMS or slot not in range(1, DAILY_MAX + 1):
        raise ValueError("Plataforma o cupo diario no autorizado")
    return "dr-noticias-social-daily/" + platform + "/" + str(local_date) + "/slot-" + str(slot) + ".json"


def available_slot(platform, local_date, request=github_api):
    for slot in range(1, DAILY_MAX + 1):
        path = daily_path(platform, local_date, slot)
        if request("/contents/" + quote(path) + "?ref=" + MEDIA_BRANCH) is None:
            return slot
    return None


def reserve_slot(platform, local_date, slot, article_url, request=github_api,
                 write=ledger_write):
    path = daily_path(platform, local_date, slot)
    if request("/contents/" + quote(path) + "?ref=" + MEDIA_BRANCH) is not None:
        raise RuntimeError("Reserva diaria existente: no reintentar")
    return write(path, {
        "platform": platform, "status": "reserved", "article_url": article_url,
        "local_date": str(local_date), "slot": slot,
        "created_at": datetime.now(timezone.utc).isoformat()
    })


def prepare_post(feed, post):
    raw = None
    for image_url in high_resolution_cover_urls(post["cover"]):
        try:
            content, _ = read_url(image_url)
            if adequate_cover_quality(content):
                raw = content
                break
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            continue
    if raw is None:
        raise ValueError("Portada sin tamaño original suficiente")
    jpg = jpeg_from_png(raw)
    digest = hashlib.sha256(jpg).hexdigest()
    image_url = stage_jpeg(post, jpg)  # Solo rama dr-noticias-media, sin tocar Blogger
    # La portada ya fue recuperada, convertida y guardada: no volver a descargar
    # el original de Blogger ni recomprimirlo (podría cambiar entre solicitudes).
    expected_url = MEDIA_BASE + digest + ".jpg"
    if image_url != expected_url:
        raise RuntimeError("URL de portada preparada inesperada")
    staged, mime = read_url(image_url, max_bytes=8 * 1024 * 1024)
    if mime != "image/jpeg" or hashlib.sha256(staged).hexdigest() != digest:
        raise RuntimeError("Portada JPEG pública no coincide con el archivo preparado")
    entry = next((e for e in feed.get("feed", {}).get("entry", [])
                  if any(link.get("rel") == "alternate" and
                         link.get("href") == post["url"]
                         for link in e.get("link", []))), None)
    if entry is None:
        raise RuntimeError("La noticia cambió durante la preparación")
    instagram_caption = make_caption(post["title"], entry.get("content", {}).get("$t", ""))
    return post, digest, image_url, instagram_caption


def run(platform, mode, environ=None, now=None):
    if platform not in PLATFORMS or mode not in ("scan", "run"):
        raise ValueError("Modo o plataforma no permitido")
    cfg = PLATFORMS[platform]
    env = os.environ if environ is None else environ
    enabled = str(env.get(cfg["enabled"], "")).lower().strip() == "true"
    if mode == "run" and (
        not enabled or env.get("GITHUB_ACTIONS") != "true" or
        env.get("GITHUB_REF") != "refs/heads/main" or
        not env.get("GITHUB_TOKEN")
    ):
        raise ValueError("Canal " + platform + " no habilitado para publicar automáticamente")
    start = activation_instant(env.get("DR_NOTICIAS_AUTO_START_UTC", ""))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Hora sin zona")
    token = env.get(cfg["token"], "")
    cfg["verify"](token)
    feed_bytes, _ = read_url(FEED, max_bytes=1600000)
    feed = json.loads(feed_bytes)
    posts = eligible_entries(entries_from_feed(feed, now=now), now, start)
    print("DR Noticias Tech | " + platform.upper() + " | " + mode.upper())
    print("Cuenta validada: " + cfg["account"])
    print("Inicio UTC: " + start.isoformat())
    print("Noticias recientes posteriores al inicio: " + str(len(posts)))
    if not posts:
        print("Sin noticias nuevas, nada que publicar.")
        return
    localday = now.astimezone(RD_TZ).date()
    if mode == "run" and available_slot(platform, localday) is None:
        print("Límite alcanzado: tres por día en " + platform)
        return
    for post in posts:
        if mode == "run":
            path = cfg["ledger_path"](post["url"])
            if github_api("/contents/" + quote(path) + "?ref=" + MEDIA_BRANCH) is not None:
                print("Saltada, ya registrada en " + platform + ": " + post["title"][:90])
                continue
        try:
            editorial_ready(feed, post)
        except ValueError as error:
            print("Control editorial no superado: " + post["title"][:75] + " (" + str(error)[:90] + ")")
            continue
        if mode == "scan":
            print("CANDIDATA SOLO LECTURA: " + post["title"][:110])
            continue
        try:
            selected, digest, image_url, ig_caption = prepare_post(feed, post)
        except ValueError as error:
            print("Portada no preparada: " + post["title"][:70] + " (" + str(error)[:90] + ")")
            continue
        if platform == "facebook":
            caption = cfg["caption"](ig_caption, selected["url"])
        else:
            caption = cfg["caption"](selected["title"], ig_caption, selected["url"])
        slot = available_slot(platform, localday)
        if slot is None:
            print("Límite diario completado.")
            return
        reserve_slot(platform, localday, slot, selected["url"])
        cfg["publish"](selected, digest, image_url, caption, token)
        print("COMPLETADA: una publicación automática en " + platform + ".")
        return
    if mode == "scan":
        print("Fin de simulación. Cero publicaciones o imágenes guardadas.")
    else:
        print("Sin noticias restantes publicables en " + platform + ".")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=tuple(PLATFORMS), required=True)
    parser.add_argument("--mode", choices=("scan", "run"), default="scan")
    args = parser.parse_args()
    run(args.platform, args.mode)


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, RuntimeError, ValueError, KeyError, TypeError,
            OSError, TimeoutError) as error:
        print("ERROR de " + str(error)[:205], file=sys.stderr)
        sys.exit(1)
