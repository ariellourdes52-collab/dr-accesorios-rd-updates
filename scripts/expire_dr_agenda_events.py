#!/usr/bin/env python3
"""Purge expired DR Agenda entries ONLY from the secondary Firebase Hosting site.

Existing frontend filtering hides events at their end time; this scheduled job
removes catalog records and their own images (no notifications or other sites).
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
    wait_for_agenda_inventory, verify_agenda_inventory, require, parse_iso,
)

EXPECTED_SITE = "sites/dr-accesorios-rd-agenda"
REPOSITORY = "ariellourdes52-collab/dr-accesorios-rd-updates"
CONFIRM = "EXPIRAR-EVENTOS-AGENDA"
EVENT_ID = re.compile(r"evt-[a-z0-9][a-z0-9-]{1,42}\Z", re.I)
IMAGE_PATH = re.compile(r"/agenda/images/evt-[a-z0-9][a-z0-9-]{1,42}\.webp\Z", re.I)
BASE_FILES = frozenset({
    "/index.html", "/robots.txt", "/sitemap.xml",
    "/agenda/index.html", "/agenda/app.js", "/agenda/styles.css",
    "/agenda/logo-oficial.webp", CATALOG_PATH,
})


def partition_events(catalog: dict, now: datetime):
    """Use endAt, falling back to startAt; fail closed on invalid dates."""
    require(now.tzinfo is not None, "Hora de referencia sin zona")
    require(isinstance(catalog, dict) and catalog.get("schemaVersion") == 1,
            "Versión de catálogo incompatible")
    require(isinstance(catalog.get("revision"), int) and
            not isinstance(catalog["revision"], bool) and catalog["revision"] >= 1,
            "Revisión de catálogo inválida")
    entries = catalog.get("events")
    require(isinstance(entries, list) and len(entries) <= 250, "Eventos de Agenda incompatibles")
    seen = set()
    keep, expired = [], []
    for item in entries:
        require(isinstance(item, dict), "Evento inválido en catálogo")
        event_id = item.get("id")
        require(isinstance(event_id, str) and EVENT_ID.fullmatch(event_id) and event_id not in seen,
                "Identificador inválido o duplicado")
        seen.add(event_id)
        begin = parse_iso(item.get("startAt"))
        ending = item.get("endAt")
        final = parse_iso(ending) if ending else begin
        require(final >= begin, "Fecha final anterior al inicio")
        (expired if final <= now else keep).append(item)
    return keep, expired


def prepare_plan(api: Hosting, now=None):
    require(SITE == EXPECTED_SITE and SITE_ID != PRIMARY_SITE_ID and
            PUBLIC == "https://dr-accesorios-rd-agenda.web.app",
            "Fuera del Hosting secundario de DR Agenda")
    now = now or datetime.now(timezone.utc)
    require(now.tzinfo is not None, "Fecha no válida")
    previous_release, previous_version = api.active()
    require(previous_release and previous_version and
            previous_version.startswith(SITE + "/versions/"),
            "No hay versión LIVE de DR Agenda")
    files = api.files(previous_version)
    require(BASE_FILES <= set(files), "Inventario Agenda incompleto")
    unknown = set(files) - BASE_FILES - {p for p in files if IMAGE_PATH.fullmatch(p)} - FIREBASE_RESERVED_PATHS
    require(not unknown, "Archivo desconocido: " + ", ".join(sorted(unknown)))
    require(all(isinstance(h, str) and bool(h) for h in files.values()), "Hash de archivo inválido")
    meta = api.api("GET", previous_version)
    require(isinstance(meta.get("config"), dict), "Falta configuración Hosting")
    if "fileCount" in meta:
        require(len(files) == int(meta["fileCount"]), "Inventario de Hosting incompleto")
    raw = read_remote()
    require(isinstance(raw, bytes) and 0 < len(raw) <= 600000, "Catálogo remoto no disponible")
    catalog = json.loads(raw)
    verify_remote_against_live(raw, files, {
        "baseRevision": catalog.get("revision"),
        "remoteFingerprint": fingerprint(raw),
    })
    keep, expired = partition_events(catalog, now)
    if not expired:
        return {"changed": False, "expired": [], "previous_version": previous_version}
    updated = copy.deepcopy(catalog)
    updated["events"] = keep
    updated["revision"] = catalog["revision"] + 1
    updated["updatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    new_raw = json_bytes(updated)
    require(len(new_raw) <= 600000, "Catálogo actualizado supera límite")
    compressed, digest = compress_digest(new_raw)
    expected = {p: h for p, h in files.items() if p not in FIREBASE_RESERVED_PATHS}
    expected[CATALOG_PATH] = digest
    expired_images = {"/agenda/images/" + e["id"] + ".webp" for e in expired}
    removed_images = expired_images & set(expected)
    for image in removed_images:
        expected.pop(image)
    require(set(files) - set(expected) - FIREBASE_RESERVED_PATHS == removed_images,
            "Cambio de inventario no autorizado")
    require(all(files[p] == h for p, h in expected.items() if p != CATALOG_PATH),
            "Intento de modificar otro archivo")
    return {
        "changed": True, "expired": expired, "kept": keep,
        "previous_release": previous_release, "previous_version": previous_version,
        "files": files, "config": meta["config"],
        "raw": raw, "new_raw": new_raw, "compressed": compressed, "digest": digest,
        "expected": expected, "removed_images": sorted(removed_images),
        "previous_revision": catalog["revision"],
    }


def save_backup(plan):
    target = os.environ.get("DR_AGENDA_BACKUP_DIR")
    require(bool(target), "Falta carpeta segura de respaldo; se cancela el borrado")
    location = Path(target) / ("expire-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    location.mkdir(parents=True, exist_ok=False)
    (location / "previous-events.json").write_bytes(plan["raw"])
    (location / "restore-manifest.json").write_bytes(json_bytes({
        "site": SITE, "version": plan["previous_version"],
        "release": plan["previous_release"],
        "config": plan["config"], "files": plan["files"],
    }))
    print("✅ Respaldo previo guardado para Actions", flush=True)


def expire(*, mode="auditar", confirm="", api=None, now=None):
    require(mode in ("auditar", "aplicar"), "Modo desconocido")
    if mode == "aplicar":
        require(os.environ.get("GITHUB_ACTIONS") == "true" and
                os.environ.get("GITHUB_REPOSITORY") == REPOSITORY and
                os.environ.get("GITHUB_REF") == "refs/heads/main" and
                os.environ.get("GITHUB_EVENT_NAME") in ("schedule", "workflow_dispatch"),
                "Sólo está autorizado en GitHub Actions, repositorio principal")
        if os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
            require(confirm == CONFIRM, "Falta confirmación manual")
    api = api or Hosting()
    plan = prepare_plan(api, now)
    if not plan["changed"]:
        print("✅ DR Agenda: no hay eventos vencidos. Sin cambios ni publicaciones.", flush=True)
        return 0
    print("DR Agenda: revisión", plan["previous_revision"],
          "| vencidos:", len(plan["expired"]),
          "| imágenes a retirar:", len(plan["removed_images"]), flush=True)
    if mode == "auditar":
        print("✅ AUDITORÍA: sin cambios ni notificaciones.", flush=True)
        return len(plan["expired"])
    save_backup(plan)
    prior = (plan["previous_release"], plan["previous_version"])
    require(api.active() == prior and api.files(plan["previous_version"]) == plan["files"],
            "Firebase cambió antes de ejecutar; no se modifica")
    require(fingerprint(read_remote()) == fingerprint(plan["raw"]),
            "El catálogo fue modificado por otro proceso")
    created = api.api("POST", SITE + "/versions", json={"config": copy.deepcopy(plan["config"])})
    name = created.get("name", "")
    require(isinstance(name, str) and name.startswith(SITE + "/versions/"),
            "Firebase devolvió un Hosting no autorizado")
    result = api.api("POST", name + ":populateFiles", json={"files": plan["expected"]})
    needed = set(result.get("uploadRequiredHashes", []))
    require(needed <= {plan["digest"]},
            "Se requieren archivos ajenos a events.json: cancelado")
    if needed:
        upload_url = result.get("uploadUrl", "")
        require(upload_url.startswith("https://upload-firebasehosting.googleapis.com/") and
                "/sites/" + SITE_ID + "/" in upload_url,
                "URL de carga ajena a Agenda")
        response = api.session.post(
            upload_url + "/" + plan["digest"], data=plan["compressed"],
            headers={"Content-Type": "application/octet-stream"}, timeout=90,
        )
        require(response.ok, "No se pudo cargar events.json")
    wait_for_agenda_inventory(api, name, plan["expected"])
    require(api.active() == prior and
            fingerprint(read_remote()) == fingerprint(plan["raw"]),
            "El catálogo o versión LIVE cambió antes de finalizar")
    api.api("PATCH", name, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    require(api.active() == prior and
            fingerprint(read_remote()) == fingerprint(plan["raw"]),
            "Cambios concurrentes antes de activar")
    api.api("POST", SITE + "/releases", params={"versionName": name}, json={
        "message": "DR Agenda: retirar eventos al vencer, sólo Hosting secundario; sin avisos",
    })
    require(api.active()[1] == name, "La nueva versión no quedó activa")
    verify_agenda_inventory(api.files(name), plan["expected"])
    print("✅ Se retiraron", len(plan["expired"]),
          "eventos vencidos y", len(plan["removed_images"]),
          "imágenes; otros archivos intactos; sin notificaciones.", flush=True)
    return len(plan["expired"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auditar", "aplicar"), default="auditar")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    expire(mode=args.mode, confirm=args.confirm)


if __name__ == "__main__":
    try:
        main()
    except (AgendaError, ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as error:
        print("❌ DR Agenda caducidad: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
