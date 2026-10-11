#!/usr/bin/env python3
"""Reschedule exactly one Agenda event on the SECONDARY Firebase Hosting.

No Android build, no FCM/Telegram/WebPush. Preserves event ID, image, link,
all other events, and all deployed secondary-site files. Optimistic revision.
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
    wait_for_agenda_inventory, verify_agenda_inventory, require, parse_iso
)

EVENT_ID = re.compile(r'evt-[a-z0-9][a-z0-9-]{1,42}\Z', re.IGNORECASE)
IMAGE_PATH = re.compile(r'/agenda/images/evt-[a-z0-9][a-z0-9-]{1,42}\.webp\Z', re.IGNORECASE)
BASE_FILES = frozenset({
    '/index.html', '/robots.txt', '/sitemap.xml',
    '/agenda/index.html', '/agenda/app.js', '/agenda/styles.css',
    '/agenda/logo-oficial.webp', CATALOG_PATH,
})
CONFIRM = 'REPROGRAMAR-EVENTO-AGENDA'
NOTE_RE = re.compile(r'\n\n📅 (?:Fecha actualizada|Evento pospuesto): [^\n]*\Z')


def prepare_reschedule(api, *, event_id, revision, title, expected_start, expected_end,
                       new_start, new_end, kind, reason):
    require(SITE == 'sites/dr-accesorios-rd-agenda' and SITE_ID != PRIMARY_SITE_ID and
            PUBLIC == 'https://dr-accesorios-rd-agenda.web.app', 'Hosting principal prohibido')
    require(bool(EVENT_ID.fullmatch(event_id)), 'ID Agenda inválido')
    require(isinstance(revision, int) and revision >= 1, 'Revisión inválida')
    require(isinstance(title, str) and 3 <= len(title) <= 180, 'Título inválido')
    require(kind in ('change', 'postpone'), 'Tipo de cambio inválido')
    require(isinstance(reason, str) and len(reason) <= 160 and not any(x in reason for x in '\r\n<>'),
            'Motivo fuera de rango')
    original_start, original_end = parse_iso(expected_start), parse_iso(expected_end)
    begin, finish = parse_iso(new_start), parse_iso(new_end)
    require(finish > begin and begin > datetime.now(timezone.utc), 'Fechas nuevas inválidas o pasadas')
    require((new_start, new_end) != (expected_start, expected_end), 'No hay cambios de fecha')
    require((begin - datetime.now(timezone.utc)).days <= 730, 'Nueva fecha demasiado lejana')
    if kind == 'postpone':
        require(begin > original_start, 'Posponer requiere una fecha posterior al inicio anterior')
    prior_release, prior_version = api.active()
    require(bool(prior_release and prior_version and prior_version.startswith(SITE + '/versions/')),
            'No existe publicación LIVE de DR Agenda')
    files = api.files(prior_version)
    metadata = api.api('GET', prior_version)
    require(isinstance(metadata.get('config'), dict), 'Configuración de Hosting ausente')
    if 'fileCount' in metadata:
        require(len(files) == int(metadata['fileCount']), 'Inventario incompleto')
    require(BASE_FILES <= set(files), 'Faltan archivos base del segundo sitio')
    require(not (set(files) - BASE_FILES -
                 {p for p in files if IMAGE_PATH.fullmatch(p)} - FIREBASE_RESERVED_PATHS),
            'Archivos inesperados en Hosting; abortar')
    require(all(bool(digest) for digest in files.values()), 'Hashes inválidos')
    raw = read_remote()
    require(raw is not None and len(raw.encode('utf-8')) < 650000, 'Catálogo remoto ausente')
    verify_remote_against_live(raw, files, {'baseRevision': revision,
                                           'remoteFingerprint': fingerprint(raw)})
    catalog = json.loads(raw)
    require(catalog.get('schemaVersion') == 1 and catalog.get('revision') == revision and
            isinstance(catalog.get('events'), list) and len(catalog['events']) <= 250,
            'Catálogo o revisión incompatible')
    target = [e for e in catalog['events'] if isinstance(e, dict) and e.get('id') == event_id]
    require(len(target) == 1, 'Evento inexistente o duplicado')
    original = target[0]
    require(original.get('title') == title and original.get('startAt') == expected_start and
            original.get('endAt') == expected_end, 'El evento cambió; vuelve a seleccionarlo')
    require(original.get('status') in ('confirmed', 'postponed'), 'Estado no reprogramable')
    updated = copy.deepcopy(catalog)
    for item in updated['events']:
        if item.get('id') != event_id:
            continue
        item['startAt'] = new_start
        item['endAt'] = new_end
        # 'postponed' se oculta en Telegram; con fecha nueva debe verse confirmado.
        item['status'] = 'confirmed'
        desc = NOTE_RE.sub('', str(item.get('description') or ''))
        if reason:
            prefix = 'Evento pospuesto' if kind == 'postpone' else 'Fecha actualizada'
            note = f'\n\n📅 {prefix}: {reason}'
            require(len(desc) + len(note) <= 1200, 'Motivo excede la descripción de Android')
            item['description'] = desc + note
        elif desc != item.get('description'):
            # Una reprogramación posterior sin motivo no debe conservar
            # una justificación obsoleta de la reprogramación anterior.
            item['description'] = desc
        break
    updated['revision'] = revision + 1
    updated['updatedAt'] = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    data = json_bytes(updated)
    require(len(data) <= 600000, 'Catálogo resultante supera el límite de Android')
    compressed, digest = compress_digest(data)
    expected_files = {p: d for p, d in files.items() if p not in FIREBASE_RESERVED_PATHS}
    expected_files[CATALOG_PATH] = digest
    require(set(expected_files) == set(files) - FIREBASE_RESERVED_PATHS, 'Archivos alterados')
    require(all(files[p] == h for p, h in expected_files.items() if p != CATALOG_PATH),
            'Se intentó modificar algo distinto del catálogo')
    return {
        'prior_release': prior_release, 'prior_version': prior_version,
        'files': files, 'config': metadata['config'], 'old_raw': raw,
        'new_data': data, 'compressed': compressed, 'digest': digest,
        'expected_files': expected_files, 'old_event': original,
        'event_id': event_id, 'new_start': new_start, 'new_end': new_end,
    }


def save_backup(plan):
    backup_dir = os.environ.get('DR_AGENDA_BACKUP_DIR')
    require(bool(backup_dir), 'Falta ruta de respaldo; abortar')
    folder = Path(backup_dir) / ('reschedule-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'previous-events.json').write_text(plan['old_raw'], encoding='utf-8')
    (folder / 'restore-manifest.json').write_bytes(json_bytes({
        'site': SITE, 'release': plan['prior_release'], 'version': plan['prior_version'],
        'files': plan['files'], 'config': plan['config'],
    }))
    print('Respaldo del catálogo creado', flush=True)


def reschedule(*, event_id, revision, title, expected_start, expected_end,
               new_start, new_end, kind, reason, mode, confirm, api=None):
    require(mode in ('auditar', 'publicar'), 'Modo inválido')
    if mode == 'publicar':
        require(confirm == CONFIRM and os.environ.get('DR_AGENDA_RESCHEDULE_ENABLED') == 'true' and
                os.environ.get('GITHUB_ACTIONS') == 'true', 'Reprogramación protegida: falta confirmación o autorización')
    api = api or Hosting()
    p = prepare_reschedule(api, event_id=event_id, revision=revision, title=title,
                           expected_start=expected_start, expected_end=expected_end,
                           new_start=new_start, new_end=new_end, kind=kind, reason=reason)
    print('DR Agenda:', event_id, 'cambio', kind, 'revisión', revision, '->', revision + 1)
    if mode == 'auditar':
        print('AUDITORÍA SOLAMENTE: Firebase y notificaciones intactos')
        return
    save_backup(p)
    require(api.active() == (p['prior_release'], p['prior_version']), 'Cambió versión LIVE')
    require(api.files(p['prior_version']) == p['files'], 'Cambió inventario LIVE')
    require(fingerprint(read_remote()) == fingerprint(p['old_raw']), 'Cambió catálogo remoto')
    created = api.api('POST', SITE + '/versions', json={'config': copy.deepcopy(p['config'])})
    new_version = created.get('name', '')
    require(new_version.startswith(SITE + '/versions/'), 'Firebase devolvió otro sitio')
    result = api.api('POST', new_version + ':populateFiles', json={'files': p['expected_files']})
    needed = set(result.get('uploadRequiredHashes', []))
    require(needed <= {p['digest']}, 'Firebase solicitó archivos fuera del catálogo')
    if needed:
        upload_url = result.get('uploadUrl', '')
        require(upload_url.startswith('https://upload-firebasehosting.googleapis.com/') and
                '/sites/' + SITE_ID + '/' in upload_url, 'Destino de carga inesperado')
        response = api.session.post(upload_url + '/' + p['digest'], data=p['compressed'],
                                    headers={'Content-Type': 'application/octet-stream'}, timeout=90)
        require(response.ok, 'No se pudo subir el catálogo actualizado')
    wait_for_agenda_inventory(api, new_version, p['expected_files'])
    require(api.active() == (p['prior_release'], p['prior_version']) and
            fingerprint(read_remote()) == fingerprint(p['old_raw']), 'Cambió LIVE antes de finalizar')
    api.api('PATCH', new_version, params={'updateMask': 'status'}, json={'status': 'FINALIZED'})
    require(api.active() == (p['prior_release'], p['prior_version']), 'Cambió LIVE antes de publicar')
    api.api('POST', SITE + '/releases', params={'versionName': new_version}, json={
        'message': 'DR Agenda: reprogramación de un evento desde admin Telegram (sin avisos)'
    })
    require(api.active()[1] == new_version, 'Nueva versión no está LIVE')
    verify_agenda_inventory(api.files(new_version), p['expected_files'])
    latest = json.loads(read_remote())
    target = next((e for e in latest.get('events', []) if e.get('id') == event_id), None)
    require(latest.get('revision') == revision + 1 and target and
            target.get('startAt') == new_start and target.get('endAt') == new_end,
            'El cambio no se verificó en el catálogo publicado')
    print('REPROGRAMADO Y VERIFICADO: sólo un evento, mismo ID, sin notificaciones')


def main():
    p = argparse.ArgumentParser(description='Reprogramar un evento de DR Agenda, sólo Hosting secundario')
    for name in ('event-id', 'expected-title', 'expected-start', 'expected-end',
                 'new-start', 'new-end'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--expected-revision', type=int, required=True)
    p.add_argument('--change-kind', choices=('change', 'postpone'), required=True)
    p.add_argument('--reason', default='')
    p.add_argument('--mode', choices=('auditar', 'publicar'), default='auditar')
    p.add_argument('--confirm', default='')
    args = p.parse_args()
    reschedule(event_id=args.event_id, revision=args.expected_revision,
               title=args.expected_title, expected_start=args.expected_start,
               expected_end=args.expected_end, new_start=args.new_start, new_end=args.new_end,
               kind=args.change_kind, reason=args.reason, mode=args.mode, confirm=args.confirm)


if __name__ == '__main__':
    try:
        main()
    except (AgendaError, ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print('ERROR: no se reprogramó el evento. ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
