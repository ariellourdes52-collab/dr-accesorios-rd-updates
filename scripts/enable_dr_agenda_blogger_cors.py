#!/usr/bin/env python3
"""DR Agenda only: safely allow cross-origin public catalog fetch from Blogger.

Only the Firebase Hosting config for the secondary Agenda site changes.
Files, images and event catalog keep exactly the same hashes.
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
    CATALOG_PATH, require, verify_agenda_inventory,
    wait_for_agenda_inventory, FIREBASE_RESERVED_PATHS,
)

EXPECTED_SITE = 'sites/dr-accesorios-rd-agenda'
CONFIRM = 'HABILITAR-CORS-BLOGGER-AGENDA'
BASE_FILES = frozenset({
    '/index.html', '/robots.txt', '/sitemap.xml',
    '/agenda/index.html', '/agenda/app.js', '/agenda/styles.css',
    '/agenda/logo-oficial.webp', CATALOG_PATH,
})
IMAGE_PATH = re.compile(r'/agenda/images/evt-[a-z0-9][a-z0-9-]{1,42}\.webp\Z')


def updated_config(config: dict) -> dict:
    require(isinstance(config, dict), 'Configuración Hosting ausente')
    updated = copy.deepcopy(config)
    items = updated.setdefault('headers', [])
    require(isinstance(items, list), 'Reglas de cabeceras inválidas')
    for item in items:
        require(isinstance(item, dict) and isinstance(item.get('glob'), str)
                and isinstance(item.get('headers'), dict), 'Regla de cabeceras no reconocida')
    exact = [item for item in items if item['glob'] == CATALOG_PATH]
    if exact:
        exact[-1]['headers']['Access-Control-Allow-Origin'] = '*'
    else:
        items.append({'glob': CATALOG_PATH, 'headers': {'Access-Control-Allow-Origin': '*'}})
    return updated


def prepare_plan(hosting):
    require(SITE == EXPECTED_SITE and SITE_ID != PRIMARY_SITE_ID
            and PUBLIC == 'https://dr-accesorios-rd-agenda.web.app',
            'Prohibido modificar Hosting principal')
    release, version = hosting.active()
    require(bool(release and version and version.startswith(SITE + '/versions/')),
            'No hay versión LIVE de DR Agenda')
    files = hosting.files(version)
    unexpected = (set(files) - BASE_FILES -
                  {p for p in files if IMAGE_PATH.fullmatch(p)} -
                  FIREBASE_RESERVED_PATHS)
    require(not unexpected, 'Archivos inesperados en Hosting secundario: ' + ', '.join(sorted(unexpected)))
    require(BASE_FILES <= set(files), 'Faltan archivos esenciales de Agenda')
    require(all(isinstance(d, str) and bool(d) for d in files.values()), 'Hashes inválidos')
    meta = hosting.api('GET', version)
    if 'fileCount' in meta:
        require(len(files) == int(meta['fileCount']), 'Inventario incompleto')
    original = meta.get('config')
    proposed = updated_config(original)
    manifest = {p: d for p, d in files.items() if p not in FIREBASE_RESERVED_PATHS}
    require(CATALOG_PATH in manifest, 'Catálogo no encontrado')
    return {'release': release, 'version': version, 'files': files,
            'manifest': manifest, 'original': original, 'proposed': proposed,
            'changed': proposed != original}


def save_backup(plan):
    directory = os.environ.get('DR_AGENDA_BACKUP_DIR', '')
    require(bool(directory), 'Falta ruta de respaldo')
    path = Path(directory) / ('agenda-cors-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    path.mkdir(parents=True, exist_ok=False)
    (path / 'restore-manifest.json').write_text(json.dumps({
        'site': SITE, 'oldRelease': plan['release'], 'oldVersion': plan['version'],
        'files': plan['files'], 'config': plan['original'],
    }, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Respaldo externo creado', flush=True)


def run(mode='auditar', confirm='', hosting=None):
    require(mode in ('auditar', 'aplicar'), 'Modo no permitido')
    hosting = hosting or Hosting()
    plan = prepare_plan(hosting)
    print('DR Agenda Hosting secundario:', SITE, '| eventos e imágenes conservados', flush=True)
    print('Solo cabecera Access-Control-Allow-Origin: * para', CATALOG_PATH, flush=True)
    if not plan['changed']:
        print('✅ CORS ya está habilitado, no se publica otra versión.', flush=True)
        return False
    if mode == 'auditar':
        print('✅ AUDITORÍA: ningún archivo, configuración o evento modificado.', flush=True)
        return True
    require(confirm == CONFIRM, 'Confirmación incorrecta')
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'Solo desde GitHub Actions')
    save_backup(plan)
    require(hosting.active() == (plan['release'], plan['version']), 'Hosting cambió')
    require(hosting.files(plan['version']) == plan['files'], 'Inventario cambió')
    version_obj = hosting.api('POST', SITE + '/versions', json={'config': plan['proposed']})
    new_version = version_obj.get('name', '')
    require(isinstance(new_version, str) and new_version.startswith(SITE + '/versions/'),
            'Firebase respondió con un sitio distinto')
    result = hosting.api('POST', new_version + ':populateFiles', json={'files': plan['manifest']})
    require(not result.get('uploadRequiredHashes'),
            'Firebase pidió cargas nuevas: abortar (el catálogo debe conservarse)')
    wait_for_agenda_inventory(hosting, new_version, plan['manifest'])
    require(hosting.active() == (plan['release'], plan['version']), 'Hosting cambió antes de finalizar')
    require(hosting.files(plan['version']) == plan['files'], 'Inventario cambió antes de finalizar')
    hosting.api('PATCH', new_version, params={'updateMask': 'status'}, json={'status': 'FINALIZED'})
    require(hosting.active() == (plan['release'], plan['version']), 'Hosting cambió antes de activar')
    hosting.api('POST', SITE + '/releases', params={'versionName': new_version}, json={
        'message': 'DR Agenda: permitir lectura pública de events.json desde Blogger; solo CORS'
    })
    require(hosting.active()[1] == new_version, 'Versión LIVE no coincide')
    verify_agenda_inventory(hosting.files(new_version), plan['manifest'])
    active_meta = hosting.api('GET', new_version)
    require(updated_config(active_meta.get('config')) == active_meta.get('config'),
            'CORS no aparece en la configuración activa')
    print('✅ CORS activado; todos los archivos tienen los mismos hashes.', flush=True)
    print('✅ Sin cambios a DR Radar, DR Audio, Telegram, FCM o Android.', flush=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('auditar', 'aplicar'), default='auditar')
    parser.add_argument('--confirm', default='')
    options = parser.parse_args()
    run(mode=options.mode, confirm=options.confirm)


if __name__ == '__main__':
    try:
        main()
    except (AgendaError, OSError, ValueError, KeyError, TypeError) as exc:
        print('❌ DR Agenda CORS: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
