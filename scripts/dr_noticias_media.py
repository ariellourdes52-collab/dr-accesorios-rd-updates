#!/usr/bin/env python3
"""DR Noticias Tech: comprueba y convierte portadas de Blogger a JPEG.

Dos modos manuales: check (sin escrituras) y stage (sube SOLO el JPEG a
la rama dr-noticias-media). Nunca llama a Instagram ni publica entradas.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError

REPO = "ariellourdes52-collab/dr-accesorios-rd-updates"
MAIN = "main"
MEDIA_BRANCH = "dr-noticias-media"
FEED = "https://draccesoriosrd.blogspot.com/feeds/posts/default?alt=json&max-results=20"
BLOG = "draccesoriosrd.blogspot.com"
IMAGE_HOSTS = {"blogger.googleusercontent.com", "lh3.googleusercontent.com"}
CONFIRM = "PREPARAR-JPEG-DR-NOTICIAS"
FRESHNESS_SECONDS = 72 * 3600
MAX_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 28_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class CoverParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cover = None

    def handle_starttag(self, tag, attrs):
        if tag != "img" or self.cover:
            return
        d = dict(attrs)
        url = d.get("src", "")
        try:
            u = urlparse(unescape(url))
            if (u.scheme == "https" and u.hostname in IMAGE_HOSTS
                    and not u.username and not u.password and not u.port):
                self.cover = u.geturl()
        except ValueError:
            pass


def high_resolution_cover_urls(url: str) -> list[str]:
    """Preferir tamaño original, sin modificar archivos ni recortar en Blogger."""
    u = urlparse(url)
    if (u.scheme != "https" or u.hostname not in IMAGE_HOSTS or
            u.username or u.password or u.port):
        raise ValueError("Portada fuera de Googleusercontent")
    path = re.sub(
        r"/(?:s\d+(?:-[A-Za-z0-9]+)*|w\d+(?:-h\d+)?(?:-[A-Za-z0-9]+)*)/",
        "/s1600/", u.path, count=1
    )
    path = re.sub(
        r"=(?:s\d+|w\d+(?:-h\d+)?)(?:-[A-Za-z0-9]+)*$",
        "=s1600", path
    )
    candidate = u._replace(path=path).geturl()
    return [candidate, url] if candidate != url else [url]


def adequate_cover_quality(raw: bytes) -> bool:
    with Image.open(io.BytesIO(raw)) as image:
        w, h = image.size
        return w >= 700 and h >= 500 and w * h >= 700000


def trusted_post(url: str) -> bool:
    try:
        u = urlparse(url)
        return (u.scheme == "https" and u.hostname == BLOG
                and not u.username and not u.password and not u.port
                and bool(re.match(r"^/20\d{2}/\d{2}/[-a-zA-Z0-9_]+\.html$", u.path))
                and not u.query and not u.fragment)
    except ValueError:
        return False


def read_url(url: str, *, max_bytes: int = MAX_BYTES, headers: dict | None = None) -> tuple[bytes, str]:
    h = {"User-Agent": "DRNoticiasTechMediaPrep/1.0", "Accept": "*/*"}
    h.update(headers or {})
    request = Request(url, headers=h)
    with urlopen(request, timeout=25) as response:
        final = urlparse(response.geturl())
        # Bloquear respuestas redirigidas a servidores no previstos
        if url.startswith("https://blogger.googleusercontent.com/"):
            if final.scheme != "https" or final.hostname not in IMAGE_HOSTS:
                raise ValueError("La portada redirige fuera del proveedor Blogger")
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("Respuesta excesiva")
        return raw, response.headers.get_content_type()


def entries_from_feed(feed: dict, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    posts = []
    for e in feed.get("feed", {}).get("entry", []):
        try:
            published = datetime.fromisoformat(e["published"]["$t"].replace("Z", "+00:00"))
            seconds = (now - published).total_seconds()
            if seconds < -600 or seconds > FRESHNESS_SECONDS:
                continue
            title = e["title"]["$t"].strip()
            urls = [x.get("href") for x in e.get("link", [])
                    if x.get("rel") == "alternate" and "html" in x.get("type", "")]
            if not urls or not trusted_post(urls[0]):
                continue
            parser = CoverParser()
            parser.feed(e.get("content", {}).get("$t", ""))
            posts.append({"url": urls[0], "title": title,
                          "published": published.isoformat(), "cover": parser.cover})
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(posts, key=lambda x: x["published"], reverse=True)


def jpeg_from_png(raw: bytes) -> bytes:
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        if img.width * img.height > MAX_PIXELS:
            raise ValueError("Imagen demasiado grande")
        # Mantener todos los píxeles, incluso si hay transparencia.
        img = ImageOps.exif_transpose(img).convert("RGBA")
        background = Image.new("RGBA", img.size, "#fff0f6")
        background.alpha_composite(img)
        rgb = background.convert("RGB")
        rgb.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        w, h = rgb.size
        # Instagram feed: proporción final de 4:5 a 1.91:1, sin recortar contenido.
        if w / h > 1.91:
            hh = int(w / 1.91 + 0.999)
            frame = Image.new("RGB", (w, hh), "#fff0f6")
            frame.paste(rgb, (0, (hh - h) // 2))
            rgb = frame
        elif w / h < 0.8:
            ww = int(h * 0.8 + 0.999)
            frame = Image.new("RGB", (ww, h), "#fff0f6")
            frame.paste(rgb, ((ww - w) // 2, 0))
            rgb = frame
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=88, optimize=True, progressive=False)
        data = out.getvalue()
        if not data.startswith(b"\xff\xd8\xff") or len(data) >= 8 * 1024 * 1024:
            raise ValueError("JPEG incompatible con Instagram")
        return data


def github_api(path: str, *, method: str = "GET", data: dict | None = None):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ValueError("Falta GITHUB_TOKEN; no es posible preparar la imagen")
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Authorization": "Bearer " + token,
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request("https://api.github.com/repos/" + REPO + path,
                  data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError("GitHub API HTTP " + str(error.code)) from None


def ensure_media_branch():
    existing = github_api("/git/ref/heads/" + MEDIA_BRANCH)
    if existing:
        return
    head = github_api("/git/ref/heads/" + MAIN)
    if not head or not head.get("object", {}).get("sha"):
        raise RuntimeError("No se puede verificar main")
    github_api("/git/refs", method="POST", data={
        "ref": "refs/heads/" + MEDIA_BRANCH, "sha": head["object"]["sha"]})


def stage_jpeg(post: dict, image: bytes) -> str:
    ensure_media_branch()
    digest = hashlib.sha256(image).hexdigest()
    path = "dr-noticias-media/" + digest + ".jpg"
    api_path = "/contents/" + quote(path)
    present = github_api(api_path + "?ref=" + MEDIA_BRANCH)
    if present and present.get("type") != "file":
        raise RuntimeError("Ruta existente no es un archivo")
    if not present:
        github_api(api_path, method="PUT", data={
            "message": "media(dr-noticias): portada JPEG de noticia verificada",
            "content": base64.b64encode(image).decode("ascii"),
            "branch": MEDIA_BRANCH
        })
    url = ("https://raw.githubusercontent.com/" + REPO + "/" +
           MEDIA_BRANCH + "/" + path)
    # Comprobar que el recurso público realmente sirve imagen JPEG.
    for _ in range(6):
        try:
            check, kind = read_url(url, max_bytes=8*1024*1024)
            if kind == "image/jpeg" and check.startswith(b"\xff\xd8\xff"):
                return url
        except (HTTPError, TimeoutError, ValueError):
            pass
        time.sleep(4)
    raise RuntimeError("El JPEG aún no está disponible públicamente; revisar el enlace")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("check", "stage"), default="check")
    parser.add_argument("--url", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.mode == "stage":
        if args.confirm != CONFIRM or os.environ.get("GITHUB_ACTIONS") != "true":
            raise ValueError("Confirmación manual de preparar imagen ausente")
        if not args.url:
            raise ValueError("Para preparar JPEG debes indicar el enlace exacto de Blogger")
    source, mime = read_url(FEED, max_bytes=1600000)
    entries = entries_from_feed(json.loads(source))
    print("DR NOTICIAS TECH | modo " + args.mode)
    print("Noticias recientes válidas: " + str(len(entries)))
    selected = next((p for p in entries if p["url"] == args.url), None) if args.url else next((p for p in entries if p["cover"]), None)
    if not selected:
        raise ValueError("No se encontró la noticia elegida en las últimas 72 horas")
    if not selected["cover"]:
        raise ValueError("La noticia no tiene portada Blogger")
    # El feed de Blogger suele devolver /w400-h225/ o /s400/.
    # Nunca enviar a Instagram miniaturas borrosas de 400x225.
    raw = None
    mimetype = None
    for candidate in high_resolution_cover_urls(selected["cover"]):
        try:
            attempt, kind = read_url(candidate)
            if adequate_cover_quality(attempt):
                raw, mimetype = attempt, kind
                break
        except (HTTPError, OSError, ValueError, UnidentifiedImageError):
            continue
    if raw is None:
        raise ValueError("Portada sin resolución editorial suficiente; requiere original de al menos ~1200x675")
    image = jpeg_from_png(raw)
    with Image.open(io.BytesIO(image)) as check:
        print("Noticia: " + selected["title"][:90])
        print("Portada fuente: " + mimetype)
        print("JPEG preparado: " + str(check.width) + "x" + str(check.height) +
              ", " + str(len(image)) + " bytes")
    if args.mode == "stage":
        url = stage_jpeg(selected, image)
        print("JPEG público verificado: " + url)
        print("Instagram: NO PUBLICADO. Blog intacto.")
    else:
        print("Solo comprobación. Ningún archivo subido. Instagram: NO PUBLICADO.")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, OSError, ValueError, RuntimeError, KeyError, TypeError,
            UnidentifiedImageError, json.JSONDecodeError) as e:
        print("ERROR de preparación: " + str(e)[:200], file=sys.stderr)
        sys.exit(1)
