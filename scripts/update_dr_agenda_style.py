#!/usr/bin/env python3
"""Actualización quirúrgica de estilos de DR Agenda EN HOSTING SECUNDARIO.

Solo modifica /agenda/styles.css dentro del segundo sitio de Firebase Hosting.
Preserva bytes/hashes de catálogo, fotografías, logo, HTML, JS y resto de archivos.
Por defecto AUDITAR (GET, sin escrituras); actualización con confirmación manual.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dr_agenda_publisher_core import (
    AgendaError, Hosting, SITE, SITE_ID, PRIMARY_SITE_ID, PUBLIC,
    compress_digest, require, verify_agenda_inventory, wait_for_agenda_inventory,
    FIREBASE_RESERVED_PATHS,
)

CSS_PATH = "/agenda/styles.css"
SITE_EXPECTED = "sites/dr-accesorios-rd-agenda"
CSS_FILE = Path(__file__).resolve().parent.parent / "agenda_site/agenda/styles.css"
BASE_FILES = frozenset({
    "/index.html", "/robots.txt", "/sitemap.xml",
    "/agenda/index.html", "/agenda/app.js", "/agenda/styles.css",
    "/agenda/logo-oficial.webp", "/agenda/events.json",
})
IMAGE_PATH = re.compile(r"/agenda/images/evt-[a-z0-9][a-z0-9-]{1,42}\\.webp\\Z")
CONFIRM = "ACTUALIZAR-SOLO-CSS-AGENDA"


def validate_css(css_bytes: bytes) -> None:
    require(0 < len(css_bytes) <= 1_000_000, "CSS ausente o excesivo")
    css = css_bytes.decode("utf-8")
    required = (
        ".event-cover img{display:block;",
        "object-fit:contain;object-position:center}",
        ".dialog-photo{background-color:#faf7f7;background-size:contain;",
        "@media(max-width:710px){.event-cover{aspect-ratio:4/3}}",
    )
    require(all(part in css for part in required), "Falta regla CSS de imagen completa")
    require(css.count("/* DR Agenda — imágenes de eventos completas") == 1,
            "Modificación de estilos inesperada")


def expected_manifest(old_files: dict[str, str], new_digest: str) -> dict[str, str]:
    require(SITE == SITE_EXPECTED and SITE_ID != PRIMARY_SITE_ID
            and PUBLIC == "https://dr-accesorios-rd-agenda.web.app",
            "Destino de Hosting principal prohibido")
    unexpected = sorted(set(old_files) - BASE_FILES
                        - {p for p in old_files if IMAGE_PATH.fullmatch(p)}
                        - FIREBASE_RESERVED_PATHS)
    require(not unexpected, "Rutas desconocidas en Hosting: " + ", ".join(unexpected))
    require(BASE_FILES <= set(old_files), "Faltan archivos base del sitio Agenda")
    own = {path: digest for path, digest in old_files.items()
           if path not in FIREBASE_RESERVED_PATHS}
    require(all(isinstance(digest, str) and bool(digest) for digest in own.values()),
            "Hashes inválidos en inventario")
    own[CSS_PATH] = new_digest
    return own


def prepare_plan(hosting: Hosting, *, css_file: Path = CSS_FILE):
    css_bytes = css_file.read_bytes()
    validate_css(css_bytes)
    compressed, digest = compress_digest(css_bytes)
    old_release, old_version = hosting.active()
    require(bool(old_version and old_release), "No hay versión activa de Agenda: no actualizar")
    require(old_version.startswith(SITE + "/versions/"), "Versión de otro sitio")
    current = hosting.files(old_version)
    previous_version_info = hosting.api("GET", old_version)
    if "fileCount" in previous_version_info:
        require(len(current) == int(previous_version_info["fileCount"]),
                "Inventario activo incompleto")
    require(isinstance(previous_version_info.get("config"), dict),
            "Configuración de Hosting ausente: no continuar")
    expected = expected_manifest(current, digest)
    changed = current[CSS_PATH] != digest
    require(all(current[p] == expected[p] for p in expected if p != CSS_PATH),
            "Se detectó cambio fuera de estilos")
    return {
        "css_compressed": compressed, "css_digest": digest,
        "release": old_release, "version": old_version, "old_files": current,
        "config": previous_version_info["config"], "expected": expected,
        "changed": changed,
    }


def save_backup(plan) -> Path:
    root = os.environ.get("DR_AGENDA_BACKUP_DIR")
    require(bool(root), "Falta directorio externo para respaldo; abortado")
    folder = Path(root) / ("agenda-css-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    folder.mkdir(parents=True, exist_ok=False)
    info = {
        "site": SITE,
        "previousRelease": plan["release"],
        "previousVersion": plan["version"],
        "previousCssHash": plan["old_files"][CSS_PATH],
        "proposedCssHash": plan["css_digest"],
        "files": plan["old_files"],
        "config": plan["config"],
    }
    (folder / "restore-manifest.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Respaldo previo de versión/manifest guardado:", folder, flush=True)
    return folder


def update(*, mode: str = "auditar", confirm: str = "", css_file: Path = CSS_FILE,
           hosting: Hosting | None = None):
    require(mode in ("auditar", "aplicar"), "Modo no permitido")
    require(SITE == SITE_EXPECTED, "Destino fuera del segundo Hosting")
    hosting = hosting or Hosting()
    plan = prepare_plan(hosting, css_file=css_file)
    print("Sitio secundario verificado:", SITE, flush=True)
    print("Versión LIVE actual:", plan["version"], flush=True)
    print("Archivos actuales:", len(plan["old_files"]),
          "| conservados con el mismo hash:", len(plan["expected"]) - 1, flush=True)
    print("ÚNICO archivo propuesto:", CSS_PATH, flush=True)
    if not plan["changed"]:
        print("✅ Estilos ya aplicados. No se modifica Hosting.", flush=True)
        return {"mode": mode, "changed": False}
    if mode == "auditar":
        print("✅ AUDITORÍA SOLO LECTURA. Ningún evento, fotografía o archivo modificado.", flush=True)
        return {"mode": "auditar", "changed": True}
    require(confirm == CONFIRM, "Falta confirmación exacta del cambio de estilos")
    require(os.environ.get("GITHUB_ACTIONS") == "true",
            "La actualización solo se autoriza desde GitHub Actions")
    save_backup(plan)
    require(hosting.active() == (plan["release"], plan["version"]),
            "LIVE cambió durante la auditoría, abortado")
    require(hosting.files(plan["version"]) == plan["old_files"],
            "El inventario cambió antes de publicar, abortado")
    new = hosting.api("POST", SITE + "/versions", json={"config": copy.deepcopy(plan["config"])})
    name = new.get("name", "")
    require(isinstance(name, str) and name.startswith(SITE + "/versions/"),
            "Firebase devolvió versión fuera del Hosting secundario")
    result = hosting.api("POST", name + ":populateFiles", json={"files": plan["expected"]})
    required = set(result.get("uploadRequiredHashes", []))
    require(required <= {plan["css_digest"]},
            "Firebase pidió cargar un archivo distinto de CSS: abortado")
    if required:
        uri = result.get("uploadUrl", "")
        require(uri.startswith("https://upload-firebasehosting.googleapis.com/")
                and "/sites/" + SITE_ID + "/" in uri,
                "URL de carga inesperada: abortado")
        response = hosting.session.post(
            uri + "/" + plan["css_digest"], data=plan["css_compressed"],
            headers={"Content-Type": "application/octet-stream"}, timeout=90
        )
        require(response.ok, "No se pudo subir CSS: HTTP " + str(response.status_code))
    wait_for_agenda_inventory(hosting, name, plan["expected"])
    require(hosting.active() == (plan["release"], plan["version"]),
            "LIVE cambió antes de finalizar: abortado")
    hosting.api("PATCH", name, params={"updateMask": "status"},
                json={"status": "FINALIZED"})
    require(hosting.active() == (plan["release"], plan["version"]),
            "LIVE cambió antes de activar: abortado")
    hosting.api("POST", SITE + "/releases", params={"versionName": name},
                json={"message": "DR Agenda: imágenes responsive CSS, preservar eventos/imagenes"})
    require(hosting.active()[1] == name, "La versión activa no coincide con la nueva")
    verify_agenda_inventory(hosting.files(name), plan["expected"])
    print("✅ Solo /agenda/styles.css fue actualizado en el Hosting secundario.", flush=True)
    print("✅ Catálogo, fotografías, logo, HTML y JS conservaron sus hashes.", flush=True)
    print("✅ Sin notificaciones a Telegram, FCM o usuarios.", flush=True)
    return {"mode": "aplicar", "changed": True, "version": name}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auditar", "aplicar"), default="auditar")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    update(mode=args.mode, confirm=args.confirm)


if __name__ == "__main__":
    try:
        main()
    except (AgendaError, OSError, ValueError, KeyError, UnicodeError) as error:
        print("❌ DR Agenda CSS: " + str(error), file=sys.stderr)
        raise SystemExit(1)
