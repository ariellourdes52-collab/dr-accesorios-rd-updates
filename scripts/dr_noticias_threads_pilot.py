#!/usr/bin/env python3
"""DR Noticias Tech: Threads piloto. Preview no publica; publish solo manual.
Usa Blogger y la portada JPEG ya verificada. Ledger exclusivo Threads.
No modifica los publicadores de Instagram ni Facebook.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from dr_noticias_media import FEED, MEDIA_BRANCH, github_api, read_url, trusted_post
from dr_noticias_instagram_pilot import ready_post, ledger_write

ACCOUNT_ID = "28329657306656174"
ACCOUNT_NAME = "drnoticiastech"
GRAPH = "https://graph.threads.net/v1.0"
TOKEN_ENV = "DR_NOTICIAS_THREADS_ACCESS_TOKEN"
CONFIRM = "PUBLICAR-UNA-NOTICIA-DRNOTICIASTECH-THREADS"


def threads_api(path, token, form=None, fields=None):
    """API Threads con Authorization header, errores resumidos y sin datos sensibles."""
    if not token or len(token.strip()) < 20:
        raise ValueError("Falta DR_NOTICIAS_THREADS_ACCESS_TOKEN")
    if not re.fullmatch(r"/(?:me|[0-9]{5,30})(?:/(?:threads|threads_publish))?", path):
        raise ValueError("Ruta Threads no autorizada")
    if form is not None and fields is not None:
        raise ValueError("No mezclar POST y GET")
    params = ("?" + urlencode({"fields": fields})) if fields is not None else ""
    body = urlencode(form).encode("utf-8") if form is not None else None
    headers = {
        "Authorization": "Bearer " + token.strip(),
        "Accept": "application/json",
        "User-Agent": "DRNoticiasTechThreadsPilot/1.0"
    }
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(GRAPH + path + params, headers=headers, data=body,
                  method="POST" if body is not None else "GET")
    try:
        with urlopen(req, timeout=40) as response:
            result = json.load(response)
    except HTTPError as error:
        try:
            detail = json.loads(error.read(8192)).get("error", {})
            api_code = re.sub("[^0-9]", "", str(detail.get("code", "")))[:6] or "?"
            api_sub = re.sub("[^0-9]", "", str(detail.get("error_subcode", "")))[:6] or "?"
        except (ValueError, UnicodeError):
            api_code, api_sub = "?", "?"
        raise RuntimeError("Threads API HTTP " + str(error.code) +
                           ", code=" + api_code + ", subcode=" + api_sub) from None
    except (URLError, TimeoutError):
        raise RuntimeError("Threads API no responde. Revisar reserva antes de reintentar") from None
    if not isinstance(result, dict):
        raise RuntimeError("Threads API devolvió contenido inesperado")
    return result


def verify_account(token, request=threads_api):
    data = request("/me", token, fields="id,username")
    if (str(data.get("id")) != ACCOUNT_ID or
            str(data.get("username", "")).lower().lstrip("@") != ACCOUNT_NAME):
        raise RuntimeError("Token de otra cuenta Threads. Publicación bloqueada")
    return {"id": ACCOUNT_ID, "username": ACCOUNT_NAME}


def threads_caption(title, instagram_caption, article_url):
    """Construye un post Threads con resumen original y enlace Blogger, <= 500."""
    if not trusted_post(article_url):
        raise ValueError("Enlace externo a Blogger oficial")
    if not title.strip() or len(title) > 180:
        raise ValueError("Titular no válido")
    paragraphs = [p.strip() for p in instagram_caption.split("\n\n") if p.strip()]
    lead = next((p for p in paragraphs
                 if len(p) >= 90 and not p.startswith("📰") and
                 not p.lower().startswith("dr accesorios rd")), "")
    if len(lead) < 90:
        raise ValueError("La noticia no contiene resumen editorial")
    # Mantenemos solo 3 etiquetas sencillas para evitar una pared de hashtags.
    hashtags = re.findall(r"(?<!\w)#[\w]+", instagram_caption)
    tags = list(dict.fromkeys(hashtags[:2] + ["#DRNoticiasTech"]))[:3]
    top = "📰 " + title.strip() + "\n\n"
    tail = "\n\n🔗 " + article_url + "\n\n" + " ".join(tags)
    capacity = 480 - len(top) - len(tail)
    if capacity < 110:
        raise ValueError("Titular o URL demasiado largos para Threads")
    if len(lead) > capacity:
        # Priorizar una oración completa: evitar finales abruptos como "una…".
        allowed = lead[:capacity]
        sentence_ends = [m.end() for m in re.finditer(r"[.!?](?=\\s|$)", allowed)]
        if sentence_ends and sentence_ends[-1] >= min(120, capacity // 2):
            lead = allowed[:sentence_ends[-1]].rstrip()
        else:
            cut = lead[:capacity - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
            lead = cut + "…"
    message = top + lead + tail
    if not (len(message) <= 480 and article_url in message and lead):
        raise ValueError("El contenido supera el tamaño permitido para Threads")
    return message


def threads_ledger_path(article_url):
    digest = hashlib.sha256((ACCOUNT_ID + ":" + article_url).encode("utf-8")).hexdigest()
    return "dr-noticias-threads-ledger/" + digest + ".json"


def publish(post, digest, image_url, caption, token, request=threads_api,
            pause=time.sleep):
    path = threads_ledger_path(post["url"])
    if github_api("/contents/" + quote(path) + "?ref=" + MEDIA_BRANCH):
        raise RuntimeError("Esta noticia tiene reserva Threads previa. No repetir sin revisar")
    record = {
        "platform": "threads", "status": "reserved",
        "account_id": ACCOUNT_ID, "username": ACCOUNT_NAME,
        "article_url": post["url"], "title": post["title"],
        "jpeg_sha256": digest,
        "caption_sha256": hashlib.sha256(caption.encode("utf-8")).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    sha = ledger_write(path, record)
    print("Reserva exclusiva Threads creada. No ejecutar dos veces.")
    try:
        # La API de Threads requiere crear primero un contenedor de imagen.
        container = request("/" + ACCOUNT_ID + "/threads", token, {
            "media_type": "IMAGE", "image_url": image_url,
            "text": caption,
            "alt_text": ("Portada informativa de DR Noticias Tech: " + post["title"])[:300]
        })
        cid = str(container.get("id") or "")
        if not cid.isdigit():
            raise RuntimeError("Threads no confirmó ID de contenedor")
        record.update(status="container_created", container_id=cid)
        sha = ledger_write(path, record, sha=sha)

        for attempt in range(10):
            state = request("/" + cid, token, fields="id,status")
            status = str(state.get("status") or "").upper()
            if status == "FINISHED":
                break
            if status in {"EXPIRED", "ERROR", "PUBLISHED"}:
                raise RuntimeError("Contenedor Threads en estado " + status + "; revisar")
            if attempt < 9:
                pause(6)
        else:
            raise RuntimeError("Contenedor Threads no terminó de procesarse. No reintentar")

        # Solo un publish por artículo. La reserva persiste ante errores de red.
        result = request("/" + ACCOUNT_ID + "/threads_publish", token,
                         {"creation_id": cid})
        post_id = str(result.get("id") or "")
        if not post_id.isdigit():
            raise RuntimeError("Threads no confirmó ID de publicación. Revisar cuenta")
        record.update(status="published", threads_media_id=post_id,
                      completed_at=datetime.now(timezone.utc).isoformat())
        ledger_write(path, record, sha=sha)
        print("PUBLICADO en Threads @drnoticiastech; ID: " + post_id)
        print("Enlace Blogger: " + post["url"])
    except Exception:
        if record["status"] != "published":
            record["status"] = "requires_review"
            try:
                ledger_write(path, record, sha=sha)
            except Exception:
                print("ADVERTENCIA: reserva Threads conservada. Revisión manual obligatoria")
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
            raise ValueError("Faltan condiciones obligatorias para publicación supervisada")
    token = os.environ.get(TOKEN_ENV, "")
    verify_account(token)
    feed_bytes, _ = read_url(FEED, max_bytes=1600000)
    post, digest, image_url, instagram_caption = ready_post(
        json.loads(feed_bytes), args.article_url)
    caption = threads_caption(post["title"], instagram_caption, post["url"])
    if args.mode == "publish" and digest != args.approved_sha256:
        raise RuntimeError("La portada cambió desde la vista previa; cancelar")

    print("DR NOTICIAS TECH — THREADS " + args.mode.upper())
    print("Cuenta verificada: @" + ACCOUNT_NAME + " / " + ACCOUNT_ID)
    print("Noticia: " + post["title"])
    print("Enlace Blogger: " + post["url"])
    print("JPEG público verificado: " + image_url)
    print("SHA256: " + digest)
    print("Caracteres: " + str(len(caption)))
    print("--- TEXTO THREADS ---")
    print(caption)
    print("--- FIN TEXTO THREADS ---")
    if args.mode == "preview":
        print("SIMULACIÓN: 0 publicaciones, 0 cambios en Threads ni ledger.")
        return
    publish(post, digest, image_url, caption, token)


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, OSError, ValueError, RuntimeError,
            KeyError, TypeError, TimeoutError) as error:
        print("ERROR: " + str(error)[:220], file=sys.stderr)
        sys.exit(1)
