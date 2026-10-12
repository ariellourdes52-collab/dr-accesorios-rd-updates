#!/usr/bin/env python3
"""DR Noticias Tech. Vista previa y piloto manual. No tiene cron."""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from dr_noticias_media import (REPO, MEDIA_BRANCH, adequate_cover_quality,
    entries_from_feed, github_api, high_resolution_cover_urls, jpeg_from_png, read_url)

EXPECTED_ID = "28639578679032150"
EXPECTED_NAME = "drnoticiastech"
GRAPH = "https://graph.instagram.com/v26.0"
FEED = "https://draccesoriosrd.blogspot.com/feeds/posts/default?alt=json&max-results=20"
MEDIA_BASE = "https://raw.githubusercontent.com/" + REPO + "/" + MEDIA_BRANCH + "/dr-noticias-media/"
APPROVE = "PUBLICAR-UNA-NOTICIA-DRNOTICIASTECH"


class EditorialParagraphs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_p = False
        self.buf = []
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "iframe"):
            self.skip += 1
        if tag == "p" and not self.skip:
            self.buf = []
            self.in_p = True
        if tag == "br" and self.in_p:
            self.buf.append(" ")

    def handle_data(self, data):
        if self.in_p and not self.skip:
            self.buf.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            p = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            if len(p) >= 55 and not p.lower().startswith(("lee también", "publicidad", "fuente:")):
                self.paragraphs.append(p)
            self.buf = []
            self.in_p = False
        if tag in ("script", "style", "noscript", "svg", "iframe"):
            self.skip = max(0, self.skip - 1)


def make_caption(title, html):
    parser = EditorialParagraphs()
    parser.feed(html)
    pieces = list(dict.fromkeys(parser.paragraphs))
    if not pieces:
        raise ValueError("La entrada Blogger no contiene párrafos editoriales aprovechables")
    body = ""
    for p in pieces:
        if len(body) >= 720:
            break
        body = (body + "\n\n" + p).strip()
    if len(body) > 850:
        s = body[:850]
        cut = max(s.rfind(". "), s.rfind("! "), s.rfind("? "))
        body = s[:cut + 1].rstrip() if cut >= 440 else s.rsplit(" ", 1)[0] + "…"
    if len(body) < 320:
        raise ValueError("Descripción insuficiente; revisar Blogger antes de publicar")
    first = []
    low = title.lower()
    topics = [
        ("nintendo", ["#Nintendo", "#Switch2"]),
        ("switch", ["#Nintendo", "#Gaming"]),
        ("xbox", ["#Xbox", "#Gaming"]),
        ("videojuego", ["#Videojuegos", "#Gaming"]),
        ("whatsapp", ["#WhatsApp", "#Aplicaciones"]),
        ("samsung", ["#Samsung", "#Android"]),
        ("google", ["#Google", "#Tecnologia"]),
        ("iphone", ["#iPhone", "#Apple"]),
        ("apple", ["#Apple", "#Tecnologia"]),
        ("android", ["#Android", "#Moviles"]),
    ]
    for term, tags in topics:
        if term in low:
            first = tags
            break
    tags = list(dict.fromkeys(first + ["#TecnologiaRD", "#NoticiasRD", "#DRNoticiasTech", "#Innovacion", "#Tecnologia"]))[:5]
    result = ("📰 " + title + "\n\n" + body + "\n\n" +
              "Lee la noticia completa desde el enlace de nuestra biografía.\n\n" +
              " ".join(tags))
    if not 450 <= len(result) <= 1700:
        raise ValueError("Extensión de descripción no válida")
    return result


def meta(path, token, data=None):
    if not token:
        raise ValueError("Falta DR_NOTICIAS_IG_ACCESS_TOKEN")
    body = urlencode(data).encode("utf-8") if data is not None else None
    headers = {"Authorization": "Bearer " + token,
               "User-Agent": "DRNoticiasTechPublisher/1.0", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(GRAPH + path, headers=headers, data=body,
                  method="POST" if body is not None else "GET")
    try:
        with urlopen(req, timeout=40) as response:
            result = json.load(response)
    except HTTPError as e:
        code = "unknown"
        subcode = "unknown"
        try:
            error = json.loads(e.read(8192)).get("error", {})
            code = str(error.get("code", code))
            subcode = str(error.get("error_subcode", subcode))
        except (ValueError, UnicodeDecodeError):
            pass
        raise RuntimeError("Meta API HTTP " + str(e.code) +
                           ", code=" + code + ", subcode=" + subcode) from None
    if not isinstance(result, dict):
        raise RuntimeError("Respuesta inesperada de Meta")
    return result


def check_account(token):
    p = meta("/me?fields=id,username,account_type", token)
    if (str(p.get("id")) != EXPECTED_ID or
            str(p.get("username", "")).lower() != EXPECTED_NAME or
            str(p.get("account_type", "")).upper() != "BUSINESS"):
        raise RuntimeError("Cuenta Instagram no autorizada, se cancela el proceso")


def ready_post(feed, article_url):
    posts = entries_from_feed(feed)
    if article_url:
        posts = [x for x in posts if x["url"] == article_url]
    entries = feed.get("feed", {}).get("entry", [])
    for p in posts:
        if not p.get("cover"):
            continue
        raw = None
        for url in high_resolution_cover_urls(p["cover"]):
            try:
                temp, _ = read_url(url)
                if adequate_cover_quality(temp):
                    raw = temp
                    break
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                continue
        if raw is None:
            continue
        image = jpeg_from_png(raw)
        digest = hashlib.sha256(image).hexdigest()
        media_url = MEDIA_BASE + digest + ".jpg"
        try:
            public_bytes, mime = read_url(media_url, max_bytes=8 * 1024 * 1024)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            continue
        if mime != "image/jpeg" or hashlib.sha256(public_bytes).hexdigest() != digest:
            continue
        entry = next((x for x in entries if any(l.get("rel") == "alternate"
            and l.get("href") == p["url"] for l in x.get("link", []))), None)
        if entry is None:
            continue
        caption = make_caption(p["title"], entry.get("content", {}).get("$t", ""))
        return p, digest, media_url, caption
    raise ValueError("No hay portada pública preparada para la noticia seleccionada")


def ledger_path(article_url):
    seed = (EXPECTED_ID + ":" + article_url).encode("utf-8")
    return "dr-noticias-ledger/" + hashlib.sha256(seed).hexdigest() + ".json"


def ledger_write(path, record, sha=None):
    body = {"branch": MEDIA_BRANCH,
            "message": "audit(dr-noticias): " + record["status"],
            "content": base64.b64encode(json.dumps(
                record, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    result = github_api("/contents/" + quote(path), method="PUT", data=body)
    if not result or not result.get("content", {}).get("sha"):
        raise RuntimeError("Registro anti-duplicados no guardado")
    return result["content"]["sha"]


def publish(post, digest, media_url, caption, token):
    path = ledger_path(post["url"])
    if github_api("/contents/" + quote(path) + "?ref=" + MEDIA_BRANCH):
        raise RuntimeError("Ya existe una reserva para esta noticia. No reintentar sin revisión")
    record = {"status": "reserved", "account": EXPECTED_NAME,
              "article_url": post["url"], "title": post["title"],
              "image_sha256": digest,
              "caption_sha256": hashlib.sha256(caption.encode()).hexdigest(),
              "created_at": datetime.now(timezone.utc).isoformat()}
    sha = ledger_write(path, record)
    print("Reserva anti-duplicados registrada; reintentos automáticos desactivados.")
    try:
        container = meta("/" + EXPECTED_ID + "/media", token, {
            "image_url": media_url, "caption": caption,
            "alt_text": "Portada de DR Noticias Tech: " + post["title"][:150]})
        cid = str(container.get("id") or "")
        if not cid.isdigit():
            raise RuntimeError("Meta no devolvió un contenedor válido")
        record.update(status="container_created", container_id=cid)
        sha = ledger_write(path, record, sha=sha)
        for _ in range(10):
            state = meta("/" + cid + "?fields=status_code", token)
            code = str(state.get("status_code", ""))
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise RuntimeError("El contenedor no está listo: " + code)
            time.sleep(6)
        else:
            raise RuntimeError("Tiempo de espera del contenedor agotado")
        result = meta("/" + EXPECTED_ID + "/media_publish", token, {"creation_id": cid})
        media_id = str(result.get("id") or "")
        if not media_id.isdigit():
            raise RuntimeError("Meta no confirmó la publicación; requiere revisión")
        record.update(status="published", instagram_media_id=media_id,
                      completed_at=datetime.now(timezone.utc).isoformat())
        ledger_write(path, record, sha=sha)
        print("PUBLICADO en @drnoticiastech; ID Meta: " + media_id)
        print("Noticia Blogger: " + post["url"])
    except Exception:
        if record["status"] != "published":
            record["status"] = "requires_review"
            try:
                ledger_write(path, record, sha=sha)
            except Exception:
                print("ATENCION: revisar reserva antes de cualquier nuevo intento")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preview", "publish"), default="preview")
    parser.add_argument("--article-url", default="")
    parser.add_argument("--approved-sha256", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.mode == "publish":
        if (os.environ.get("GITHUB_ACTIONS") != "true" or
            os.environ.get("GITHUB_REF") != "refs/heads/main" or
            args.confirm != APPROVE or not args.article_url or
            not re.fullmatch("[0-9a-f]{64}", args.approved_sha256) or
            not os.environ.get("GITHUB_TOKEN")):
            raise ValueError("Publicación requiere autorización manual completa")
    token = os.environ.get("DR_NOTICIAS_IG_ACCESS_TOKEN", "")
    check_account(token)
    raw, _ = read_url(FEED, max_bytes=1600000)
    post, digest, media_url, caption = ready_post(json.loads(raw), args.article_url)
    if args.mode == "publish" and digest != args.approved_sha256:
        raise RuntimeError("La portada ya no coincide con la aprobada")
    print("DR NOTICIAS TECH | " + args.mode.upper())
    print("Cuenta: @" + EXPECTED_NAME)
    print("Título: " + post["title"])
    print("URL del artículo: " + post["url"])
    print("JPEG: " + media_url)
    print("SHA256 para aprobación: " + digest)
    print("Longitud: " + str(len(caption)) + " caracteres")
    print("--- INICIO DEL TEXTO DE INSTAGRAM ---")
    print(caption)
    print("--- FIN DEL TEXTO ---")
    if args.mode == "preview":
        print("SOLO VISTA PREVIA: ninguna publicación ni modificación.")
    else:
        publish(post, digest, media_url, caption, token)


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, OSError, ValueError, RuntimeError, KeyError,
            TimeoutError) as e:
        print("ERROR: " + str(e)[:220], file=sys.stderr)
        sys.exit(1)
