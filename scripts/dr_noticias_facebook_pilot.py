#!/usr/bin/env python3
"""DR Noticias Tech: Facebook Page piloto manual con preview y reserva anti-duplicados.

El modo preview NO publica ni modifica archivos. El modo publish necesita fecha reciente,
artículo específico, huella JPEG exacta y frase de autorización en GitHub Actions.
Nunca actúa sobre la página comercial DR Accesorios RD.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from dr_noticias_media import (MEDIA_BRANCH, FEED, github_api, read_url, trusted_post)
from dr_noticias_instagram_pilot import (make_caption, ready_post, ledger_write)

PAGE_ID = "875159429020764"
PAGE_MARKER = "dr noticias tech"
GRAPH = "https://graph.facebook.com/v26.0"
TOKEN_NAME = "DR_NOTICIAS_FB_PAGE_TOKEN"
CONFIRM = "PUBLICAR-UNA-NOTICIA-DRNOTICIASTECH-FACEBOOK"


def fb_request(path, token, form=None):
    """No loguear respuestas completas de Meta: podrían contener credenciales."""
    if not token or len(token) < 20:
        raise ValueError("Secreto de fanpage ausente")
    if not path.startswith("/") or ".." in path or "?" in path:
        raise ValueError("Ruta de Facebook no permitida")
    method = "POST" if form is not None else "GET"
    body = urlencode(form).encode("utf-8") if form is not None else None
    headers = {"Authorization": "Bearer " + token,
               "Accept": "application/json",
               "User-Agent": "DRNoticiasTechFacebookPublisher/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(GRAPH + path + (("?fields=id,name") if method == "GET" else ""),
                  headers=headers, data=body, method=method)
    try:
        with urlopen(req, timeout=40) as response:
            result = json.load(response)
    except HTTPError as error:
        code = str(error.code)
        try:
            obj = json.loads(error.read(8192)).get("error", {})
            api_code = str(obj.get("code", "unknown"))
            subcode = str(obj.get("error_subcode", "unknown"))
        except (UnicodeError, ValueError):
            api_code, subcode = "unknown", "unknown"
        raise RuntimeError("Facebook Graph HTTP " + code +
                           ", code=" + api_code + ", subcode=" + subcode) from None
    except (URLError, TimeoutError):
        raise RuntimeError("Facebook Graph no respondió; reintentos bloqueados si había reserva") from None
    if not isinstance(result, dict):
        raise RuntimeError("Respuesta inesperada de Facebook")
    return result


def verify_page(token, request=fb_request):
    obj = request("/me", token)
    if (str(obj.get("id", "")) != PAGE_ID or
            PAGE_MARKER not in str(obj.get("name", "")).lower()):
        raise RuntimeError("El token no corresponde a la fanpage DR Noticias Tech")
    return {"id": PAGE_ID, "name": str(obj.get("name", ""))}


def facebook_caption(ig_caption, article_url):
    if not trusted_post(article_url):
        raise ValueError("El enlace debe pertenecer al Blogger oficial")
    marker = "Lee la noticia completa desde el enlace de nuestra biografía."
    if marker not in ig_caption:
        raise ValueError("Texto editorial inesperado: falta llamada a la lectura")
    caption = ig_caption.replace(marker, "🔗 Lee la noticia completa aquí:\n" + article_url)
    if article_url not in caption or len(caption) > 2600:
        raise ValueError("Texto no apto para Facebook")
    return caption


def facebook_ledger_path(article_url):
    key = (PAGE_ID + ":" + article_url).encode("utf-8")
    return "dr-noticias-facebook-ledger/" + hashlib.sha256(key).hexdigest() + ".json"


def publish(post, digest, image_url, caption, token, request=fb_request):
    path = facebook_ledger_path(post["url"])
    if github_api("/contents/" + quote(path) + "?ref=" + MEDIA_BRANCH):
        raise RuntimeError("Publicación Facebook ya registrada o incierta. No volver a ejecutar")
    record = {
        "status": "reserved", "platform": "facebook", "page_id": PAGE_ID,
        "article_url": post["url"], "title": post["title"],
        "jpeg_sha256": digest, "caption_sha256": hashlib.sha256(caption.encode()).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    current_sha = ledger_write(path, record)
    print("Reserva exclusiva Facebook creada. Prohibidos los reintentos automáticos.")
    try:
        # Un solo POST crea UNA foto publicada con su texto y enlace Blogger.
        result = request("/" + PAGE_ID + "/photos", token, {
            "url": image_url, "caption": caption, "published": "true"
        })
        media_id = str(result.get("post_id") or result.get("id") or "")
        if not media_id or not re.fullmatch(r"[0-9_]+", media_id):
            raise RuntimeError("Facebook no confirmó ID de publicación; revisión manual obligatoria")
        record.update(status="published", facebook_media_id=media_id,
                      completed_at=datetime.now(timezone.utc).isoformat())
        ledger_write(path, record, sha=current_sha)
        print("PUBLICADO en fanpage DR Noticias Tech; ID: " + media_id)
        print("Enlace Blogger: " + post["url"])
    except Exception:
        if record["status"] != "published":
            record["status"] = "requires_review"
            try:
                ledger_write(path, record, sha=current_sha)
            except Exception:
                print("ADVERTENCIA: la reserva permanece bloqueada; no reintentar sin revisión")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["preview", "publish"], default="preview")
    parser.add_argument("--article-url", default="")
    parser.add_argument("--approved-sha256", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.mode == "publish":
        if (os.environ.get("GITHUB_ACTIONS") != "true" or
                os.environ.get("GITHUB_REF") != "refs/heads/main" or
                args.confirm != CONFIRM or
                not args.article_url or
                not re.fullmatch(r"[0-9a-f]{64}", args.approved_sha256) or
                not os.environ.get("GITHUB_TOKEN")):
            raise ValueError("Faltan condiciones obligatorias de autorización manual")

    token = os.environ.get(TOKEN_NAME, "")
    verify_page(token)
    data, _ = read_url(FEED, max_bytes=1600000)
    post, digest, image_url, ig_caption = ready_post(json.loads(data), args.article_url)
    caption = facebook_caption(ig_caption, post["url"])
    if args.mode == "publish" and digest != args.approved_sha256:
        raise RuntimeError("La imagen cambió desde la vista previa; publicación bloqueada")

    print("DR NOTICIAS TECH — FACEBOOK " + args.mode.upper())
    print("Página verificada: " + PAGE_ID)
    print("Noticia: " + post["title"])
    print("Enlace Blogger: " + post["url"])
    print("JPEG verificado: " + image_url)
    print("SHA256: " + digest)
    print("Caracteres: " + str(len(caption)))
    print("--- INICIO TEXTO FACEBOOK ---")
    print(caption)
    print("--- FIN TEXTO FACEBOOK ---")
    if args.mode == "preview":
        print("SIMULACIÓN: SIN PUBLICAR EN FACEBOOK NI MODIFICAR LEDGER.")
        return
    publish(post, digest, image_url, caption, token)


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, OSError, ValueError, RuntimeError, KeyError,
            TypeError, TimeoutError) as error:
        print("ERROR: " + str(error)[:220], file=sys.stderr)
        sys.exit(1)
