#!/usr/bin/env python3
"""DR Agenda: administrador local y publicador aislado para Firebase Hosting.

Contrato Android: schemaVersion=1, revision creciente, events[].startAt ISO8601 con
segundos y zona horaria, imageUrl bajo /agenda/images/, location objeto.

No modifica Android, no envía FCM y no despliega otros servicios.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
PUBLIC = "https://dr-accesorios-rd.web.app"
CATALOG_PATH = "/agenda/events.json"
CATALOG_URL = PUBLIC + CATALOG_PATH
CATEGORIES = ("tech", "cine", "gaming", "local", "promocion", "lanzamiento", "conferencia", "streaming", "otros")
STATES = ("draft", "confirmed", "postponed", "cancelled")
VALID_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,95}\Z")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})\Z")
RD_ZONE = timezone(timedelta(hours=-4))
# Completa el inventario existente: jamás crear una versión de Hosting con solo /agenda/.
CRITICAL = (
    "/version.json", "/radar.json", "/dr-audio/index.json",
    "/descargar/index.html", "/radar-widget/index.html",
    "/radar-alertas/index.html", "/radar-alertas/manifest.webmanifest",
)
MAX_PUBLIC = 600_000  # Misma cota que AgendaRepository de Android.
MAX_EVENTS = 250
MAX_IMAGE = 1_500_000
MAX_ORIGINAL_IMAGE = 20_000_000


class AgendaError(Exception):
    pass


def require(ok: bool, msg: str):
    if not ok:
        raise AgendaError(msg)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def json_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def fingerprint(raw: bytes | None) -> str | None:
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def confirm_https(url: str, field: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    require(parsed.scheme.lower() == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password,
            f"{field}: requiere enlace HTTPS público sin credenciales.")
    require(len(value) <= 1000, f"{field}: URL demasiado larga.")
    return value


def parse_iso(text: str) -> datetime:
    require(bool(DATE_PATTERN.fullmatch(text)), "Fecha debe contener segundos y zona, ej. 2026-11-15T10:00:00-04:00")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        require(dt.utcoffset() is not None, "Fecha sin zona horaria")
        return dt
    except ValueError as exc:
        raise AgendaError(f"Fecha inválida: {text}") from exc


def input_rd(prompt: str, default_iso: str = "", optional: bool = False) -> str:
    default = ""
    if default_iso:
        default = parse_iso(default_iso).astimezone(RD_ZONE).strftime("%Y-%m-%d %H:%M")
    while True:
        txt = input(f"{prompt} (AAAA-MM-DD HH:MM RD){' ['+default+']' if default else ''}: ").strip()
        if txt.upper() == "NINGUNO" and optional:
            return ""
        if not txt and default:
            return default_iso
        if not txt and optional:
            return ""
        try:
            dt = datetime.strptime(txt, "%Y-%m-%d %H:%M").replace(tzinfo=RD_ZONE)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            print("Fecha inválida. Ejemplo: 2026-11-15 10:00")


def ask(label: str, default: str = "", mandatory: bool = False) -> str:
    while True:
        v = input(f"{label}{' ['+default+']' if default else ''}: ").strip()
        if v.upper() == "NINGUNO" and not mandatory:
            return ""
        if not v:
            v = default
        if v or not mandatory:
            return v
        print("Este campo es obligatorio.")


def choices(label: str, options, default: str) -> str:
    options = tuple(options)
    while True:
        v = ask(f"{label} ({'/'.join(options)})", default).lower()
        if v in options:
            return v
        print("Opción no válida.")


def new_id(title: str) -> str:
    ascii_name = title.lower().encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:36] or "evento"
    return f"evt-{datetime.now(RD_ZONE):%Y%m%d}-{slug}-{uuid.uuid4().hex[:6]}"


def workspace_root(args) -> Path:
    return Path(args.workspace).expanduser().resolve() if args.workspace else Path(__file__).resolve().parent.parent / "agenda_data"


def state_file(root: Path) -> Path:
    return root / "events.local.json"


def empty_state() -> dict:
    return {"schemaVersion": 1, "baseRevision": 0, "remoteFingerprint": None, "events": []}


def read_state(root: Path) -> dict:
    file = state_file(root)
    if not file.exists():
        raise AgendaError("No hay catálogo local. Primero ejecuta: python scripts/dr_agenda_admin.py iniciar")
    data = json.loads(file.read_text(encoding="utf-8"))
    require(data.get("schemaVersion") == 1 and isinstance(data.get("events"), list), "Estado local inválido")
    return data


def save_state(root: Path, state: dict):
    root.mkdir(parents=True, exist_ok=True)
    file = state_file(root)
    temp = file.with_suffix(".tmp")
    temp.write_bytes(json_bytes(state))
    temp.replace(file)


def read_remote():
    import requests
    response = requests.get(CATALOG_URL, params={"_verify": time.time_ns()},
                            headers={"Cache-Control": "no-cache", "Accept": "application/json"}, timeout=25,
                            allow_redirects=False)
    if response.status_code == 404:
        return None
    require(response.status_code == 200, f"Catálogo remoto: HTTP {response.status_code}; no continuar.")
    require(len(response.content) <= MAX_PUBLIC, "Catálogo remoto supera límite Android")
    try:
        obj = response.json()
    except ValueError as exc:
        raise AgendaError("/agenda/events.json devuelve contenido no JSON (¿fallback HTML?). Se abortó.") from exc
    require(isinstance(obj, dict) and obj.get("schemaVersion") == 1 and isinstance(obj.get("events"), list),
            "El catálogo remoto no cumple schemaVersion 1")
    return response.content


def start_workspace(root: Path):
    require(not state_file(root).exists(), "Ya existe catálogo local. No se sobrescribió nada.")
    remote_raw = read_remote()
    if remote_raw is None:
        state = empty_state()
        print("No hay /agenda/events.json remoto: se crea catálogo local vacío.")
    else:
        data = json.loads(remote_raw)
        require(isinstance(data.get("revision"), int) and data["revision"] >= 1, "Revisión remota inválida")
        state = {"schemaVersion": 1, "baseRevision": data["revision"], "remoteFingerprint": fingerprint(remote_raw),
                 "events": data["events"]}
        print(f"Importados {len(data['events'])} eventos. Revisión remota {data['revision']}.")
    save_state(root, state)
    print("Catálogo local:", state_file(root))


def image_for(root: Path, event_id: str, local_path: str) -> str:
    path = Path(local_path.strip('"')).expanduser().resolve()
    require(path.is_file(), f"No existe la imagen: {path}")
    require(path.stat().st_size <= MAX_ORIGINAL_IMAGE, "Imagen origen > 20 MB")
    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = 24_000_000
        with Image.open(path) as source:
            require(source.format in ("JPEG", "PNG", "WEBP"), "Solo JPG, PNG y WebP")
            picture = ImageOps.exif_transpose(source)
            picture.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
            picture = picture.convert("RGBA" if picture.mode == "RGBA" else "RGB")
            out = root / "images" / f"{event_id}.webp"
            out.parent.mkdir(parents=True, exist_ok=True)
            quality = 84
            while quality >= 50:
                picture.save(out, "WEBP", quality=quality, method=5)
                if out.stat().st_size <= MAX_IMAGE:
                    break
                quality -= 8
            require(out.stat().st_size <= MAX_IMAGE, "Imagen optimizada supera 1.5 MB")
    except AgendaError:
        raise
    except Exception as exc:
        raise AgendaError(f"No fue posible procesar la imagen: {exc}") from exc
    print(f"Imagen preparada: {out.name} ({out.stat().st_size // 1024} KB)")
    return PUBLIC + "/agenda/images/" + out.name


def fill_event(root: Path, previous: dict | None = None) -> dict:
    prev = copy.deepcopy(previous or {})
    e = copy.deepcopy(prev)
    e["title"] = ask("Título", prev.get("title", ""), mandatory=True)
    e["id"] = prev.get("id") or new_id(e["title"])
    e["description"] = ask("Descripción", prev.get("description", ""), mandatory=True)
    e["category"] = choices("Categoría", CATEGORIES, prev.get("category", "otros"))
    e["status"] = choices("Estado", STATES, prev.get("status", "draft"))
    e["startAt"] = input_rd("Fecha/hora de inicio", prev.get("startAt", ""))
    e["endAt"] = input_rd("Fecha/hora de fin (opcional)", prev.get("endAt", "") or "", optional=True)
    location = prev.get("location") if isinstance(prev.get("location"), dict) else {}
    current_type = "online" if location.get("type") == "online" else "presencial"
    kind = choices("Tipo de evento", ("presencial", "online"), current_type)
    if kind == "online":
        e["location"] = {"type": "online", "online": True, "name": ask("Plataforma (opcional)", location.get("name", "")),
                          "address": "", "city": "", "province": "", "lat": None, "lng": None}
    else:
        loc = {"type": "presencial", "online": False,
               "name": ask("Nombre del lugar", location.get("name", "")),
               "address": ask("Dirección", location.get("address", "")),
               "city": ask("Ciudad", location.get("city", ""), mandatory=True),
               "province": ask("Provincia", location.get("province", ""), mandatory=True)}
        loc["lat"] = optional_float("Latitud (opcional)", location.get("lat"))
        loc["lng"] = optional_float("Longitud (opcional)", location.get("lng"))
        e["location"] = loc
    e["officialUrl"] = confirm_https(ask("Web oficial (opcional)", prev.get("officialUrl", "")), "officialUrl")
    e["articleUrl"] = confirm_https(ask("Noticia Blogger (opcional)", prev.get("articleUrl", "")), "articleUrl")
    current_image = prev.get("imageUrl", "")
    selected_image = ask("Ruta de imagen en tu PC (vacío=conservar/ninguna)")
    e["imageUrl"] = image_for(root, e["id"], selected_image) if selected_image else current_image
    return e


def optional_float(label, default):
    while True:
        s = ask(label, str(default) if default is not None else "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            print("Debe ser un número decimal o vacío.")


def find_event(state: dict, event_id: str) -> dict:
    return next((e for e in state["events"] if e.get("id") == event_id), None) or (_ for _ in ()).throw(AgendaError("Evento no encontrado: " + event_id))


def validate(state: dict, root: Path) -> list[dict]:
    require(isinstance(state.get("baseRevision"), int) and state["baseRevision"] >= 0, "baseRevision inválida")
    require(isinstance(state.get("events"), list) and len(state["events"]) <= MAX_EVENTS, "Más de 250 eventos")
    ids = set()
    result = []
    for event in state["events"]:
        require(isinstance(event, dict), "Evento no es objeto JSON")
        eid = event.get("id", "")
        require(isinstance(eid, str) and bool(VALID_ID.fullmatch(eid)) and eid not in ids, f"ID inválido/duplicado: {eid}")
        ids.add(eid)
        title = event.get("title", "")
        desc = event.get("description", "")
        require(isinstance(title, str) and 4 <= len(title) <= 180, f"{eid}: título fuera de rango")
        require(isinstance(desc, str) and 4 <= len(desc) <= 4000, f"{eid}: descripción fuera de rango")
        require(event.get("category") in CATEGORIES, f"{eid}: categoría inválida")
        require(event.get("status") in STATES, f"{eid}: estado inválido")
        start = parse_iso(event.get("startAt", ""))
        end = event.get("endAt")
        if end:
            require(parse_iso(end) >= start, f"{eid}: fecha fin anterior al inicio")
        loc = event.get("location")
        require(isinstance(loc, dict), f"{eid}: falta location")
        is_online = loc.get("type") == "online"
        require(loc.get("type") in ("online", "presencial"), f"{eid}: tipo location inválido")
        if not is_online:
            require(bool(str(loc.get("city") or "").strip()), f"{eid}: falta ciudad")
            require(bool(str(loc.get("province") or "").strip()), f"{eid}: falta provincia")
            require(bool(str(loc.get("name") or "").strip()) or bool(str(loc.get("address") or "").strip()),
                    f"{eid}: falta nombre o dirección del lugar")
        for coord, limit in (("lat", 90), ("lng", 180)):
            v = loc.get(coord)
            require(v is None or (isinstance(v, (float, int)) and not isinstance(v, bool) and -limit <= v <= limit),
                    f"{eid}: {coord} inválida")
        require((loc.get("lat") is None) == (loc.get("lng") is None), f"{eid}: lat y lng deben venir juntas")
        for key in ("officialUrl", "articleUrl"):
            confirm_https(event.get(key, ""), f"{eid}/{key}")
        url = event.get("imageUrl", "")
        if url:
            confirm_https(url, f"{eid}/imageUrl")
            parsed = urlparse(url)
            require(parsed.hostname in ("dr-accesorios-rd.web.app", "dr-accesorios-rd.firebaseapp.com") and
                    parsed.path == f"/agenda/images/{eid}.webp" and not parsed.query and not parsed.fragment,
                    f"{eid}: imageUrl debe apuntar a imagen propia, sin parámetros")
        if event["status"] != "draft":
            public = copy.deepcopy(event)
            # No exponer metadatos locales, secretos o estado interno en el catálogo.
            public = {k: public.get(k) for k in ("id", "title", "description", "category", "startAt", "endAt", "status",
                                                 "imageUrl", "officialUrl", "articleUrl", "location")}
            result.append(public)
    result.sort(key=lambda e: parse_iso(e["startAt"]).timestamp())
    require(len(result) <= MAX_EVENTS, "Demasiados eventos públicos")
    return result


def public_data(state: dict, root: Path):
    event_list = validate(state, root)
    payload = {"schemaVersion": 1, "revision": state["baseRevision"] + 1,
               "updatedAt": stamp(), "events": event_list}
    raw = json_bytes(payload)
    require(len(raw) <= MAX_PUBLIC, "Catálogo resultante demasiado grande para Android (600KB)")
    images: dict[str, bytes] = {}
    for event in event_list:
        image_url = event.get("imageUrl", "")
        if not image_url:
            continue
        image_path = root / "images" / (event["id"] + ".webp")
        if image_path.is_file():
            require(image_path.stat().st_size <= MAX_IMAGE, "Imagen supera 1.5 MB: " + image_path.name)
            images["/agenda/images/" + image_path.name] = image_path.read_bytes()
    return payload, raw, images


def compress_digest(raw: bytes):
    body = gzip.compress(raw, mtime=0)
    return body, hashlib.sha256(body).hexdigest()


def make_auth_session():
    import requests
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        import google.auth
        if os.environ.get("FIREBASE_SERVICE_ACCOUNT"):
            creds = service_account.Credentials.from_service_account_info(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]),
                     scopes=["https://www.googleapis.com/auth/firebase.hosting"])
        else:
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/firebase.hosting"])
        return AuthorizedSession(creds)
    except Exception:
        try:
            p = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True, timeout=20)
            token = p.stdout.strip()
            require(bool(token), "gcloud no devolvió token")
            session = requests.Session()
            session.headers.update({"Authorization": "Bearer " + token})
            return session
        except Exception as exc:
            raise AgendaError("No hay credenciales para Firebase Hosting. Usa `gcloud auth login` o "
                              "`gcloud auth application-default login`. Nunca pegues tokens aquí.") from exc


class Hosting:
    def __init__(self):
        self.session = make_auth_session()

    def api(self, method, path, **kw):
        response = self.session.request(method, BASE + path, timeout=90, **kw)
        if not response.ok:
            raise AgendaError(f"Firebase Hosting API HTTP {response.status_code} en {path}: " + response.text[:220])
        return response.json() if response.content else {}

    def active(self):
        release = self.api("GET", SITE + "/channels/live")["release"]
        version = SITE + "/versions/" + release["version"]["name"].rsplit("/", 1)[1]
        return release["name"], version

    def files(self, version):
        files = {}
        token = None
        while True:
            params = {"pageSize": 1000, "status": "ACTIVE"}
            if token:
                params["pageToken"] = token
            page = self.api("GET", version + "/files", params=params)
            for item in page.get("files", []):
                path = item["path"]
                require(path not in files, "Ruta duplicada en Hosting: " + path)
                files[path] = item["hash"]
            token = page.get("nextPageToken")
            if not token:
                return files


def verify_remote_against_live(remote_raw, live_files, state):
    remote_path_exists = CATALOG_PATH in live_files
    require((remote_raw is not None) == remote_path_exists,
            "El inventario activo y el catálogo público no coinciden: se aborta (posible caché o despliegue concurrente)")
    require(fingerprint(remote_raw) == state.get("remoteFingerprint"),
            "Otro publicador modificó Agenda desde que inicializaste el catálogo. Se aborta para evitar sobreescritura.")
    if remote_raw is None:
        require(state["baseRevision"] == 0, "No existe catálogo remoto pero baseRevision > 0")
    else:
        d = json.loads(remote_raw)
        require(d.get("revision") == state["baseRevision"], "Cambió la revisión de Agenda")
        body, digest = compress_digest(remote_raw)
        hashes = {digest}
        if len(body) > 9 and body[9] in (3, 255):
            variant = bytearray(body)
            variant[9] = 255 if body[9] == 3 else 3
            hashes.add(hashlib.sha256(variant).hexdigest())
        require(live_files[CATALOG_PATH] in hashes,
                "La versión activa de Hosting contiene otro contenido de Agenda (caché/desincronización)")


def snapshot(root, state, live_version, existing, remote_raw):
    # En CI, guardarlo fuera del TemporaryDirectory del evento para que Actions
    # pueda subirlo como artefacto aun si la publicacion falla despues del snapshot.
    base = Path(os.environ["DR_AGENDA_BACKUP_DIR"]) if os.environ.get("DR_AGENDA_BACKUP_DIR") else root / "backups"
    base.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    directory = base / suffix
    require(not directory.exists(), "Nombre de respaldo duplicado: espera un segundo y reintenta")
    directory.mkdir()
    (directory / "local.json").write_bytes(json_bytes(state))
    (directory / "firebase-manifest.json").write_bytes(json_bytes({"liveVersion": live_version, "files": existing}))
    if remote_raw is not None:
        (directory / "remote-events.json").write_bytes(remote_raw)
    print("Respaldo guardado en:", directory)


def publish(root: Path, state: dict, *, preflight: bool, confirm: str = "", non_interactive: bool = False):
    # Validate before requesting any Firebase credential.
    payload, raw, image_raw = public_data(state, root)
    remote_raw = read_remote()
    api = Hosting()
    previous_release, previous_version = api.active()
    old_version = api.api("GET", previous_version)
    old_files = api.files(previous_version)
    require(bool(old_files), "Hosting no tiene inventario de archivos")
    if "fileCount" in old_version:
        require(len(old_files) == int(old_version["fileCount"]), "Inventario activo incompleto")
    missing = [p for p in CRITICAL if p not in old_files]
    require(not missing, "Faltan rutas críticas; NO publicar: " + ", ".join(missing))
    verify_remote_against_live(remote_raw, old_files, state)
    for critical in ("/version.json", "/radar.json", "/dr-audio/index.json", "/descargar/", "/radar-alertas/index.html"):
        import requests
        r = requests.get(PUBLIC + critical, timeout=20, headers={"Cache-Control": "no-cache"},
                         params={"precheck": time.time_ns()}, allow_redirects=False)
        require(r.status_code == 200, f"Ruta crítica no responde con 200: {critical}: {r.status_code}")

    for event in payload["events"]:
        if event.get("imageUrl"):
            path = "/agenda/images/" + event["id"] + ".webp"
            require(path in image_raw or path in old_files,
                    "La imagen referenciada no existe ni en Hosting ni localmente: " + path)
    intended = {CATALOG_PATH: raw, **image_raw}
    blobs = {path: compress_digest(data) for path, data in intended.items()}
    expected = dict(old_files)
    for path, (_body, digest) in blobs.items():
        require(path.startswith("/agenda/"), "Intento de editar fuera de /agenda/")
        expected[path] = digest
    require(set(old_files) - set(expected) == set(), "Se perderían archivos de Hosting")
    require(all(old_files[p] == expected[p] for p in old_files if not p.startswith("/agenda/")),
            "Cambios no autorizados fuera de /agenda/")
    changed = [p for p, (_b, d) in blobs.items() if old_files.get(p) != d]
    print("\n--- PREFLIGHT DR AGENDA ---")
    print("Hosting original:", previous_version)
    print("Revisión remota:", state["baseRevision"], "→", payload["revision"])
    print("Eventos públicos:", len(payload["events"]), "| Borradores privados:", len(state["events"]) - len(payload["events"]))
    print("Rutas actuales:", len(old_files), "| Rutas después:", len(expected))
    print("Se crearían/actualizarían SOLO:", ", ".join(changed) or "ninguna")
    print("Sin notificaciones FCM. Sin editar version.json, Radar, Audio ni PWA.")
    if preflight:
        print("✅ PREFLIGHT APROBADO. No se publicó nada.")
        return
    require(confirm == "PUBLICAR-AGENDA", "Falta confirmación. Ejecuta `publicar --confirm PUBLICAR-AGENDA`")
    require(bool(payload["events"]), "No se publica un catálogo vacío por accidente.")
    if not changed:
        print("No hay diferencias, se cancela el despliegue.")
        return
    print("ADVERTENCIA: se modificará la versión LIVE de Firebase Hosting, SOLO bajo /agenda/.")
    if non_interactive:
        # Only the isolated GitHub workflow, after explicit Telegram admin approval,
        # may bypass the manual CLI prompt. This check is NOT user-facing auth;
        # GitHub workflow permissions and the Telegram admin check are the gate.
        require(os.environ.get("DR_AGENDA_CI_APPROVED") == "TELEGRAM_ADMIN_CONFIRMED",
                "Publicación CI no autorizada")
    else:
        typed = input("Escribe exactamente SI-PUBLICAR-DR-AGENDA para confirmar: ").strip()
        require(typed == "SI-PUBLICAR-DR-AGENDA", "Operación cancelada por el usuario")
    snapshot(root, state, previous_version, old_files, remote_raw)
    # Tomar nueva lectura para detectar cambios concurrentes antes de iniciar la escritura.
    require(api.active() == (previous_release, previous_version), "Cambió Hosting antes de publicar")
    require(fingerprint(read_remote()) == fingerprint(remote_raw), "Cambió el catálogo antes de publicar")

    config = copy.deepcopy(old_version.get("config", {}))
    headers = config.setdefault("headers", [])
    for path in (CATALOG_PATH,):
        entry = next((e for e in reversed(headers) if e.get("glob") == path), None)
        if entry is None:
            entry = {"glob": path, "headers": {}}
            headers.append(entry)
        entry.setdefault("headers", {})["Cache-Control"] = "no-cache, no-store, must-revalidate"
    created = api.api("POST", SITE + "/versions", json={"config": config})
    new_version = SITE + "/versions/" + created["name"].rsplit("/", 1)[1]
    pairs = list(expected.items())
    upload_by_hash = {digest: body for body, digest in blobs.values()}
    uploaded = set()
    for offset in range(0, len(pairs), 1000):
        batch = api.api("POST", new_version + ":populateFiles", json={"files": dict(pairs[offset:offset + 1000])})
        missing_hashes = set(batch.get("uploadRequiredHashes", []))
        require(missing_hashes <= set(upload_by_hash), "Hosting pidió subir un archivo preexistente ajeno a Agenda: abortado")
        for digest in missing_hashes - uploaded:
            url = batch["uploadUrl"] + "/" + digest
            response = api.session.post(url, data=upload_by_hash[digest],
                                        headers={"Content-Type": "application/octet-stream"}, timeout=90)
            require(response.ok, f"Falló carga de archivo Agenda: HTTP {response.status_code}")
            uploaded.add(digest)
    require(api.files(new_version) == expected, "Inventario de versión nueva no coincide: abortado")
    api.api("PATCH", new_version, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    require(api.active() == (previous_release, previous_version), "Hosting cambió antes de activar la nueva versión")
    require(fingerprint(read_remote()) == fingerprint(remote_raw), "Cambió catálogo antes de activar versión")
    api.api("POST", SITE + "/releases", params={"versionName": new_version},
            json={"message": "DR Agenda: actualizar solo eventos/imagenes preservando Hosting"})
    require(api.active()[1] == new_version, "La versión activa no coincide con la nueva")
    require(api.files(new_version) == expected, "Archivos activos no coinciden con manifiesto esperado")
    for attempt in range(12):
        check_raw = read_remote()
        if check_raw == raw:
            state["baseRevision"] = payload["revision"]
            state["remoteFingerprint"] = fingerprint(raw)
            save_state(root, state)
            print("✅ DR Agenda publicada y verificada.")
            print("✅ Todo archivo fuera de /agenda/ conservó exactamente su hash anterior.")
            print("✅ Sin notificaciones enviadas.")
            return
        time.sleep(5)
    raise AgendaError("La versión se activó, pero la verificación pública no se completó. No reintentes a ciegas.")


def main(argv=None):
    p = argparse.ArgumentParser(description="DR Agenda: eventos, imágenes y publicación segura (sin FCM)")
    p.add_argument("--workspace", help="Ruta de la carpeta privada de trabajo, por defecto ./agenda_data")
    sub = p.add_subparsers(dest="command", required=True)
    for action in ("iniciar", "nuevo", "listar", "validar"):
        sub.add_parser(action)
    for action in ("editar", "cancelar", "posponer", "avisar"):
        s = sub.add_parser(action)
        s.add_argument("id", help="ID único del evento")
    pub = sub.add_parser("publicar")
    mode = pub.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="Audita Hosting; no publica")
    mode.add_argument("--confirm", help="Debe ser PUBLICAR-AGENDA; además pedirá confirmación interactiva")
    a = p.parse_args(argv)
    root = workspace_root(a)
    if a.command == "iniciar":
        start_workspace(root)
        return
    if a.command == "avisar":
        raise AgendaError("Avisos FCM BLOQUEADOS: la integración de la APK y el canal Agenda aún no están habilitados.")
    state = read_state(root)
    if a.command == "nuevo":
        item = fill_event(root)
        state["events"].append(item)
        validate(state, root)
        save_state(root, state)
        print("✅ Guardado localmente, sin publicar ni notificar:", item["id"])
    elif a.command == "listar":
        if not state["events"]:
            print("Aún no hay eventos.")
        for e in state["events"]:
            print(f"{e['id']:<46} {e.get('status','?'):<10} {e.get('startAt','?')[:16]}  {e.get('title','')[:50]}")
    elif a.command == "editar":
        old = find_event(state, a.id)
        new = fill_event(root, old)
        validate({**state, "events": [new if e.get("id") == a.id else e for e in state["events"]]}, root)
        state["events"] = [new if e.get("id") == a.id else e for e in state["events"]]
        save_state(root, state)
        print("✅ Cambios locales guardados; sin publicar ni notificar.")
    elif a.command == "cancelar":
        e = find_event(state, a.id)
        e["status"] = "cancelled"
        validate(state, root)
        save_state(root, state)
        print("✅ Marcado como cancelado en el catálogo local, SIN enviar notificación.")
    elif a.command == "posponer":
        e = find_event(state, a.id)
        e["status"] = "postponed"
        print("Si aún no hay fecha nueva, conserva la anterior y la app mostrará el estado POSPUESTO.")
        answer = ask("¿Registrar nueva fecha? (si/no)", "no").lower()
        if answer == "si":
            e["startAt"] = input_rd("Nuevo inicio")
            e["endAt"] = input_rd("Nuevo fin opcional", optional=True)
        validate(state, root)
        save_state(root, state)
        print("✅ Evento pospuesto localmente, sin notificación.")
    elif a.command == "validar":
        pub, raw, images = public_data(state, root)
        print(f"✅ Catálogo Android válido. {len(pub['events'])} eventos públicos; {len(state['events']) - len(pub['events'])} borradores; {len(raw)} bytes; {len(images)} imágenes locales.")
        print("Ningún archivo remoto se modificó.")
    elif a.command == "publicar":
        publish(root, state, preflight=a.preflight, confirm=a.confirm or "")


if __name__ == "__main__":
    try:
        main()
    except (AgendaError, KeyboardInterrupt, json.JSONDecodeError, EOFError) as exc:
        print("❌ DR Agenda:", str(exc) or "Operación cancelada", file=sys.stderr)
        sys.exit(1)
