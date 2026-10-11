#!/usr/bin/env python3
"""Delete exactly one published DR Agenda event on the SECONDARY Hosting only.

No FCM, Telegram, Railway, Radar or Blogger sends. First-class audit and backup.
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
    CATALOG_PATH, FIREBASE_RESERVED_PATHS, json_bytes, read_remote,
    compress_digest, fingerprint, verify_remote_against_live,
    wait_for_agenda_inventory, verify_agenda_inventory, require
)

EVENT_ID = re.compile(r"evt-[a-z0-9][a-z0-9-]{1,42}\Z", re.IGNORECASE)
IMAGE_PATH = re.compile(r"/agenda/images/evt-[a-z0-9][a-z0-9-]{1,42}\.webp\Z", re.IGNORECASE)
BASE_FILES = frozenset({
    "/index.html", "/robots.txt", "/sitemap.xml",
    "/agenda/index.html", "/agenda/app.js", "/agenda/styles.css",
    "/agenda/logo-oficial.webp", CATALOG_PATH,
})
CONFIRM = "BORRAR-EVENTO-AGENDA"


def deletion_plan(api: Hosting, event_id: str, revision: int, title: str):
    require(SITE == "sites/dr-accesorios-rd-agenda" and
            SITE_ID != PRIMARY_SITE_ID and
            PUBLIC == "https://dr-accesorios-rd-agenda.web.app",
            "NO se permite modificar el Hosting principal")
    require(bool(EVENT_ID.fullmatch(event_id)), "ID inválido")
    require(isinstance(revision, int) and revision >= 1, "Revisión inválida")
    require(isinstance(title, str) and 3 <= len(title) <= 180, "Título inválido")
    old_release, old_version = api.active()
    require(bool(old_release and old_version and old_version.startswith(SITE + "/versions/")),
            "No hay versión activa del Hosting secundario")
    existing = api.files(old_version)
    meta = api.api("GET", old_version)
    require(isinstance(meta.get("config"), dict), "Configuración ausente")
    if "fileCount" in meta:
        require(len(existing) == int(meta["fileCount"]), "Inventario incompleto")
    require(BASE_FILES <= set(existing), "Faltan archivos base; abortar")
    require(not (set(existing) - BASE_FILES -
                 {p for p in existing if IMAGE_PATH.fullmatch(p)} -
                 FIREBASE_RESERVED_PATHS),
            "Rutas inesperadas en el Hosting; abortar")
    require(all(bool(d) for d in existing.values()), "Hashes inválidos")
    old_raw = read_remote()
    require(old_raw is not None and len(old_raw) < 650000, "Catálogo remoto ausente")
    verify_remote_against_live(old_raw, existing, {"baseRevision": revision,
                                                    "remoteFingerprint": fingerprint(old_raw)})
    catalog = json.loads(old_raw)
    require(catalog.get("schemaVersion") == 1 and
            catalog.get("revision") == revision and
            isinstance(catalog.get("events"), list) and len(catalog["events"]) <= 250,
            "La revisión remota cambió. Vuelve a seleccionar desde Telegram.")
    found = [ev for ev in catalog["events"] if isinstance(ev, dict) and ev.get("id") == event_id]
    require(len(found) == 1 and found[0].get("title") == title,
            "Evento/título no coincide; operación cancelada")
    # No editar ningún otro evento.
    updated = copy.deepcopy(catalog)
    updated["events"] = [ev for ev in catalog["events"] if ev.get("id") != event_id]
    updated["revision"] = revision + 1
    updated["updatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    data = json_bytes(updated)
    compressed, digest = compress_digest(data)
    expected = {p: h for p, h in existing.items() if p not in FIREBASE_RESERVED_PATHS}
    expected[CATALOG_PATH] = digest
    image_path = "/agenda/images/" + event_id + ".webp"
    # Remove the specific event image from the NEW active manifest, preserving other photos.
    photo_removed = image_path in expected
    expected.pop(image_path, None)
    changed = sorted(set(existing) - set(expected) - FIREBASE_RESERVED_PATHS)
    require(changed == ([image_path] if photo_removed else []),
            "Se intentó borrar otro archivo")
    require(all(existing[p] == h for p, h in expected.items() if p != CATALOG_PATH),
            "Cambio fuera del catálogo")
    return {
        "old_release": old_release, "old_version": old_version,
        "old_files": existing, "config": meta["config"],
        "raw": old_raw, "new_raw": data, "compressed": compressed,
        "digest": digest, "expected": expected, "event": found[0],
        "photo_removed": photo_removed,
    }


def backup(plan):
    root = os.environ.get("DR_AGENDA_BACKUP_DIR")
    require(bool(root), "Sin carpeta de respaldo; abortado")
    folder = Path(root) / ("delete-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "previous-events.json").write_bytes(plan["raw"])
    (folder / "restore-manifest.json").write_bytes(json_bytes({
        "site": SITE, "version": plan["old_version"], "release": plan["old_release"],
        "files": plan["old_files"], "config": plan["config"]
    }))
    print("✅ Respaldo creado:", folder)


def delete_event(event_id: str, revision: int, title: str, mode: str, confirm: str, api=None):
    require(mode in ("auditar", "eliminar"), "Modo inválido")
    require(mode != "eliminar" or
            (confirm == CONFIRM and
             os.environ.get("DR_AGENDA_DELETE_ENABLED") == "true" and
             os.environ.get("GITHUB_ACTIONS") == "true"),
            "Borrado bloqueado por confirmación, variable o contexto")
    api = api or Hosting()
    plan = deletion_plan(api, event_id, revision, title)
    print("Sitio verificado:", SITE)
    print("Evento:", event_id, "| nombre:", title, "| revision:", revision)
    print("Solo se modificará /agenda/events.json; imagen del mismo evento:",
          "se retirará" if plan["photo_removed"] else "no existe")
    if mode == "auditar":
        print("✅ AUDITORÍA: ningún archivo modificado, ninguna notificación enviada")
        return
    backup(plan)
    require(api.active() == (plan["old_release"], plan["old_version"]), "Cambió el Hosting")
    require(api.files(plan["old_version"]) == plan["old_files"], "Cambió el inventario")
    require(fingerprint(read_remote()) == fingerprint(plan["raw"]), "Cambió el catálogo")
    created = api.api("POST", SITE + "/versions", json={"config": copy.deepcopy(plan["config"])})
    name = created.get("name", "")
    require(name.startswith(SITE + "/versions/"), "Firebase devolvió sitio incorrecto")
    result = api.api("POST", name + ":populateFiles", json={"files": plan["expected"]})
    required = set(result.get("uploadRequiredHashes", []))
    require(required <= {plan["digest"]}, "Firebase solicitó archivos ajenos a Agenda")
    if required:
        uri = result.get("uploadUrl", "")
        require(uri.startswith("https://upload-firebasehosting.googleapis.com/") and
                "/sites/" + SITE_ID + "/" in uri,
                "URL de carga inesperada")
        response = api.session.post(
            uri + "/" + plan["digest"], data=plan["compressed"],
            headers={"Content-Type": "application/octet-stream"}, timeout=90)
        require(response.ok, "No se pudo subir events.json")
    wait_for_agenda_inventory(api, name, plan["expected"])
    require(api.active() == (plan["old_release"], plan["old_version"]), "Hosting cambió antes del release")
    require(fingerprint(read_remote()) == fingerprint(plan["raw"]), "Catálogo cambió antes del release")
    api.api("PATCH", name, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    require(api.active() == (plan["old_release"], plan["old_version"]), "Hosting cambió antes de activar")
    api.api("POST", SITE + "/releases", params={"versionName": name}, json={
        "message": "DR Agenda: retirar solo un evento confirmado desde admin Telegram"
    })
    require(api.active()[1] == name, "Release nuevo no está activo")
    verify_agenda_inventory(api.files(name), plan["expected"])
    print("✅ Catálogo actualizado en el Hosting secundario. Sin notificaciones.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--revision", required=True, type=int)
    parser.add_argument("--title", required=True)
    parser.add_argument("--mode", choices=("auditar", "eliminar"), default="auditar")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    delete_event(args.event_id, args.revision, args.title, args.mode, args.confirm)


if __name__ == "__main__":
    try:
        main()
    except (AgendaError, OSError, ValueError, KeyError, TypeError):
        print("❌ No se eliminó el evento. Revisar verificación/estado de GitHub.", file=sys.stderr)
        raise SystemExit(1)
