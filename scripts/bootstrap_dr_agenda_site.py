#!/usr/bin/env python3
"""Primer deploy de DR Agenda v1.0 SOLO en segundo sitio Firebase Hosting.

Modo predeterminado AUDITAR (solo GET). Primer deploy exige autorización
expresa. Jamás se conecta al sitio principal ni reemplaza un sitio ya publicado.

Ejecutar vía GitHub Actions con FIREBASE_SERVICE_ACCOUNT como secreto.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dr_agenda_publisher_core import (
    BASE, SITE, SITE_ID, PRIMARY_SITE_ID, PUBLIC, Hosting, AgendaError,
    compress_digest, require,
)

EXPECTED_SITE = "sites/dr-accesorios-rd-agenda"
EXPECTED_DOMAIN = "https://dr-accesorios-rd-agenda.web.app"
FILES = (
    "/index.html", "/robots.txt", "/sitemap.xml",
    "/agenda/index.html", "/agenda/styles.css", "/agenda/app.js",
    "/agenda/logo-oficial.webp", "/agenda/events.json",
)


def assert_isolated():
    require(SITE == EXPECTED_SITE and SITE_ID != PRIMARY_SITE_ID and PUBLIC == EXPECTED_DOMAIN,
            "PROHIBIDO: destino fuera del segundo Hosting de DR Agenda")
    require("dr-accesorios-rd.web.app" not in PUBLIC, "Dominio principal detectado")


def asset_root():
    return Path(__file__).resolve().parent.parent / "agenda_site"


def payloads(root: Path):
    assert_isolated()
    out: dict[str, bytes] = {}
    for path in FILES:
        filename = root / path.lstrip("/")
        require(filename.is_file(), "Falta archivo de la primera versión: " + path)
        data = filename.read_bytes()
        require(0 < len(data) <= 1_000_000, "Archivo faltante o excesivo: " + path)
        out[path] = data
    seed = json.loads(out["/agenda/events.json"])
    require(seed.get("schemaVersion") == 1 and seed.get("revision") == 1
            and isinstance(seed.get("events"), list) and len(seed["events"]) == 0,
            "Primer despliegue solo permite catálogo vacío de schemaVersion 1")
    # La hora real es la del despliegue, no la de preparación del ZIP.
    seed["updatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    out["/agenda/events.json"] = (json.dumps(seed, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return out


def audit_environment(hosting: Hosting, *, require_empty: bool = True):
    assert_isolated()
    release, version = hosting.active()
    if version or release:
        # No intentar desplegar encima de una versión activa, ni del propio sitio Agenda.
        raise AgendaError("El sitio DR Agenda ya tiene publicación LIVE: no se reemplaza ni se reinicializa")
    # Leer la identidad del sitio desde la API no implica crear versiones.
    # Esta comprobación se hace dentro de Hosting.__init__ antes de solicitar LIVE.
    return {"site": SITE, "release": None, "version": None}



def read_version_files(hosting: Hosting, version: str, status: str) -> dict[str, str]:
    """Listar solo archivos EXPECTED/ACTIVE de una versión secundaria."""
    require(status in ("EXPECTED", "ACTIVE"), "Estado de archivo desconocido")
    require(version.startswith(SITE + "/versions/"), "Versión ajena a DR Agenda")
    files = {}
    token = None
    while True:
        params = {"status": status, "pageSize": 1000}
        if token:
            params["pageToken"] = token
        page = hosting.api("GET", version + "/files", params=params)
        for row in page.get("files", []):
            path, digest = row.get("path"), row.get("hash")
            require(isinstance(path, str) and isinstance(digest, str)
                    and path not in files and bool(digest),
                    "Respuesta inválida o duplicada de archivos Hosting")
            files[path] = digest
        token = page.get("nextPageToken")
        if not token:
            return files


def inspect_created_versions(hosting: Hosting):
    """Diagnóstico sin escrituras para versiones no publicadas por intentos previos."""
    response = hosting.api("GET", SITE + "/versions", params={"pageSize": 20})
    count = 0
    for entry in response.get("versions", []):
        if entry.get("status") != "CREATED":
            continue
        name = entry.get("name", "")
        # Firebase a veces devuelve el nombre con projects/<numero>/... .
        if not isinstance(name, str) or "/sites/" + SITE_ID + "/versions/" not in "/" + name:
            if not name.startswith(SITE + "/versions/"):
                continue
        version = SITE + "/versions/" + name.rsplit("/", 1)[-1]
        active = read_version_files(hosting, version, "ACTIVE")
        pending = read_version_files(hosting, version, "EXPECTED")
        print("Diagnóstico SOLO LECTURA: borrador", version.rsplit("/", 1)[-1],
              "| archivos activos:", len(active), "| pendientes:", len(pending), flush=True)
        if pending:
            print("Pendientes:", ", ".join(sorted(pending)[:20]), flush=True)
        count += 1
        if count >= 5:
            break
    if not count:
        print("Diagnóstico SOLO LECTURA: no se encontraron versiones CREATED recientes.", flush=True)


def wait_for_uploaded_files(hosting: Hosting, version: str, expected: dict[str, str],
                            *, max_checks: int = 10, pause_seconds: float = 3):
    """No finalizar ni publicar hasta ver los ocho hashes ACTIVE y ninguno EXPECTED."""
    require(max_checks >= 1 and max_checks <= 20, "Número de reintentos inseguro")
    for attempt in range(max_checks):
        active = read_version_files(hosting, version, "ACTIVE")
        pending = read_version_files(hosting, version, "EXPECTED")
        for label, values in (("ACTIVE", active), ("EXPECTED", pending)):
            require(set(values) <= set(expected),
                    label + ": Firebase devolvió una ruta fuera del paquete DR Agenda")
            require(all(expected[path] == digest for path, digest in values.items()),
                    label + ": hash inesperado, publicación abortada")
        if active == expected and not pending:
            print("✅ Inventario verificado: todos los", len(expected),
                  "archivos están ACTIVE y ninguno EXPECTED.", flush=True)
            return
        print("Esperando archivos de Firebase: ACTIVE", len(active), "/", len(expected),
              "| EXPECTED", len(pending), "| comprobación", attempt + 1,
              "/", max_checks, flush=True)
        if pending:
            print("Pendientes:", ", ".join(sorted(pending)), flush=True)
        if attempt + 1 < max_checks:
            time.sleep(pause_seconds)
    raise AgendaError("Firebase no confirmó el inventario tras los reintentos. "
                      "La versión queda sin publicar; ejecutar AUDITAR y revisar los pendientes.")



def initialize(*, mode: str, confirm: str = "", root: Path | None = None):
    require(mode in ("auditar", "instalar"), "Modo de inicio no permitido")
    assert_isolated()
    root = root or asset_root()
    assets = payloads(root)
    hosting = Hosting()  # GET al segundo sitio, valida identity.
    audit_environment(hosting)
    print(f"DR Agenda: sitio aislado confirmado ({SITE})", flush=True)
    print("Los servicios del sitio principal NO se consultarán ni modificarán.", flush=True)
    print("Archivos preparados:", ", ".join(FILES), flush=True)
    if mode == "auditar":
        inspect_created_versions(hosting)
        print("✅ AUDITORÍA SOLO LECTURA: no se crearon versiones ni releases.", flush=True)
        return {"mode": "auditar", "site": SITE, "files": len(assets)}
    require(confirm == "INSTALAR-DR-AGENDA-V1", "Falta confirmación exacta del administrador")
    require(os.environ.get("GITHUB_ACTIONS") == "true", "Solo se permite el despliegue desde GitHub Actions")
    # Guardia final antes del primer POST de Hosting. Existe una sola ruta de destino.
    require(SITE == EXPECTED_SITE and "sites/dr-accesorios-rd/" not in BASE + SITE,
            "Hosting principal bloqueado")
    cfg = {"headers": [
        {"glob": "/agenda/events.json", "headers": {"Cache-Control": "no-store, must-revalidate", "X-Content-Type-Options": "nosniff"}},
        {"glob": "/agenda/**", "headers": {"Cache-Control": "public,max-age=180", "X-Content-Type-Options": "nosniff"}},
        {"glob": "/index.html", "headers": {"Cache-Control": "public,max-age=180"}},
    ]}
    blobs = {path: compress_digest(data) for path, data in assets.items()}
    expected = {path: digest for path, (_, digest) in blobs.items()}
    # Volver a comprobar que NO se creó versión LIVE desde la auditoría.
    audit_environment(hosting)
    created = hosting.api("POST", SITE + "/versions", json={"config": cfg})
    name = created.get("name", "")
    require(name.startswith(SITE + "/versions/"), "Firebase devolvió versión de otro sitio")
    upload_by_hash = {digest: body for body, digest in blobs.values()}
    result = hosting.api("POST", name + ":populateFiles", json={"files": expected})
    needed = set(result.get("uploadRequiredHashes", []))
    require(needed <= set(upload_by_hash), "Firebase solicitó archivo ajeno a DR Agenda")
    uri = result.get("uploadUrl", "")
    if needed:
        require(uri.startswith("https://upload-firebasehosting.googleapis.com/")
                and "/sites/" + SITE_ID + "/" in uri,
                "URL de carga de Firebase inesperada: no se transfiere nada")
    for digest in needed:
        response = hosting.session.post(uri + "/" + digest, data=upload_by_hash[digest],
                                        headers={"Content-Type": "application/octet-stream"}, timeout=90)
        require(response.ok, "Fallo de carga del paquete Agenda (HTTP " + str(response.status_code) + ")")
    wait_for_uploaded_files(hosting, name, expected)
    hosting.api("PATCH", name, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    audit_environment(hosting)
    hosting.api("POST", SITE + "/releases", params={"versionName": name},
                json={"message": "DR Agenda v1.0: sitio secundario independiente"})
    active_release, active_version = hosting.active()
    require(active_version == name, "La versión LIVE no coincide con la publicada")
    require(hosting.files(name) == expected, "La publicación no conserva todos los archivos")
    print("✅ DR Agenda v1.0 publicada únicamente en", PUBLIC + "/agenda/", flush=True)
    print("✅ Catálogo público inicial vacío; no se enviaron notificaciones.", flush=True)
    return {"mode": "instalar", "site": SITE, "version": name}


def main():
    parser = argparse.ArgumentParser(description="Instalar primera versión DR Agenda aislada")
    parser.add_argument("--mode", choices=("auditar", "instalar"), default="auditar")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    initialize(mode=args.mode, confirm=args.confirm)

if __name__ == "__main__":
    try:
        main()
    except (AgendaError, OSError, ValueError, KeyError) as exc:
        print("❌ DR Agenda: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
