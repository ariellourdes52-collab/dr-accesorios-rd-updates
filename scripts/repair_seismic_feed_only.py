#!/usr/bin/env python3
"""Audit then optionally restore ONLY a missing seismic feed in PRIMARY Firebase Hosting.

Does not send FCM or Telegram messages, trigger Apps Script, or modify existing
alerts. Restoration is allowed only if both live manifest and public route lack
the feed. All other file hashes remain byte-for-byte identical.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SITE = 'sites/dr-accesorios-rd'
BASE = 'https://firebasehosting.googleapis.com/v1beta1/'
PUBLIC = 'https://dr-accesorios-rd.web.app'
PATH = '/radar-seismic-v231.json'
CONFIRM = 'RESTAURAR-SOLO-RADAR-SEISMIC-V231'
CRITICAL = ('/radar.json', '/dr-audio/index.json', '/descargar/index.html')
EMPTY = {'schemaVersion': 1, 'enabled': True, 'updatedAt': '', 'alerts': []}


def require(ok, why):
    if not ok:
        raise RuntimeError('ABORTADO: ' + why)


def candidate_config(config):
    """Only add/change Cache-Control for the seismic URL."""
    require(isinstance(config, dict), 'configuración Hosting inválida')
    new = copy.deepcopy(config)
    headers = new.setdefault('headers', [])
    require(isinstance(headers, list), 'reglas headers inválidas')
    exact = [x for x in headers if isinstance(x, dict) and x.get('glob') == PATH]
    require(len(exact) <= 1, 'reglas duplicadas del feed sísmico')
    if exact:
        entry = exact[0]
    else:
        entry = {'glob': PATH, 'headers': {}}
        headers.append(entry)
    header_fields = entry.setdefault('headers', {})
    require(isinstance(header_fields, dict), 'cabeceras sísmicas inválidas')
    header_fields['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return new


def expected_manifest(old, digest):
    require(PATH not in old, 'ya existe feed sísmico en manifiesto (no sobrescribir)')
    require(all(x in old for x in CRITICAL), 'faltan rutas críticas; no modificar Hosting')
    return {**old, PATH: digest}


def payload():
    raw = (json.dumps(EMPTY, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    zipped = gzip.compress(raw, mtime=0)
    return zipped, hashlib.sha256(zipped).hexdigest()


class Hosting:
    def __init__(self, session):
        self.session = session

    def api(self, method, path, **kwargs):
        response = self.session.request(method, BASE + path, timeout=60, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def active(self):
        release = self.api('GET', SITE + '/channels/live')['release']
        name = release['version']['name']
        require(name.startswith(SITE + '/versions/'), 'LIVE apunta a sitio diferente')
        return release['name'], name

    def files(self, version):
        found, token = {}, None
        while True:
            params = {'pageSize': 1000, 'status': 'ACTIVE'}
            if token:
                params['pageToken'] = token
            page = self.api('GET', version + '/files', params=params)
            for item in page.get('files', []):
                require(item['path'] not in found, 'rutas duplicadas en Hosting')
                found[item['path']] = item['hash']
            token = page.get('nextPageToken')
            if not token:
                return found

    def public(self, url):
        return requests.get(PUBLIC + url, params={'seismic_restore_check': time.time_ns()},
                            headers={'Cache-Control': 'no-cache'}, timeout=30)


def audit(hosting):
    live_release, live_version = hosting.active()
    meta = hosting.api('GET', live_version)
    originals = hosting.files(live_version)
    require(bool(originals), 'inventario vacío')
    require(int(meta.get('fileCount', -1)) == len(originals), 'inventario LIVE incompleto')
    config = meta.get('config', {})
    require(isinstance(config, dict), 'configuración LIVE inválida')
    for path in CRITICAL:
        require(path in originals, 'falta la ruta protegida ' + path)
    code = hosting.public(PATH).status_code
    require(code in (200, 404), 'URL sísmica devolvió HTTP ' + str(code))
    exists = PATH in originals
    require((exists and code == 200) or (not exists and code == 404),
            'mismatch entre manifiesto y HTTP; requiere investigación manual')
    for path in ('/radar.json', '/dr-audio/index.json', '/descargar/'):
        result = hosting.public(path)
        require(result.ok, 'ruta protegida ' + path + ' respondió HTTP ' + str(result.status_code))
    return {'site': SITE, 'release': live_release, 'version': live_version,
            'files': originals, 'config': config, 'publicStatus': code, 'missing': not exists}


def backup(snapshot):
    folder = os.environ.get('DR_SEISMIC_BACKUP_DIR', '').strip()
    if not folder:
        return
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = target / ('dr-seismic-hosting-pre-' + stamp + '.json')
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Inventario previo guardado en artifact del workflow: ' + output.name, flush=True)


def restore(hosting, snapshot):
    require(snapshot['missing'], 'el feed sísmico ya existe; restauración no necesaria')
    zipped, digest = payload()
    wanted = expected_manifest(snapshot['files'], digest)
    config = candidate_config(snapshot['config'])
    current = audit(hosting)
    require(current == snapshot, 'Hosting cambió desde la auditoría; no continuar')
    created = hosting.api('POST', SITE + '/versions', json={'config': config})
    draft = created.get('name', '')
    require(draft.startswith(SITE + '/versions/'), 'versión nueva de sitio inesperado')
    uploaded = False
    items = list(wanted.items())
    for offset in range(0, len(items), 1000):
        result = hosting.api('POST', draft + ':populateFiles',
                             json={'files': dict(items[offset:offset + 1000])})
        needed = set(result.get('uploadRequiredHashes', []))
        require(needed <= {digest}, 'Firebase pidió subir otro archivo: se aborta')
        if digest in needed and not uploaded:
            url = result.get('uploadUrl', '')
            require(url.startswith('https://'), 'URL de carga inválida')
            response = hosting.session.post(url + '/' + digest, data=zipped,
                                            headers={'Content-Type': 'application/octet-stream'},
                                            timeout=60)
            response.raise_for_status()
            uploaded = True
    require(hosting.files(draft) == wanted, 'archivo(s) cambiaron en la versión preparada')
    hosting.api('PATCH', draft, params={'updateMask': 'status'}, json={'status': 'FINALIZED'})
    require(hosting.api('GET', draft).get('config', {}) == config,
            'configuración de Hosting cambió inesperadamente')
    require(hosting.active() == (snapshot['release'], snapshot['version']),
            'otro despliegue actualizó LIVE; no activar')
    require(hosting.files(snapshot['version']) == snapshot['files'],
            'inventario original cambió; no activar')
    require(hosting.public(PATH).status_code == 404,
            'el feed sísmico apareció durante el despliegue; no sobrescribir')
    hosting.api('POST', SITE + '/releases', params={'versionName': draft},
                json={'message': 'Restaurar SOLO JSON sísmico ausente; preservar Hosting existente'})
    require(hosting.active()[1] == draft, 'la versión restaurada no quedó activa')
    require(hosting.files(draft) == wanted, 'el manifiesto LIVE no preservó los demás archivos')
    for i in range(8):
        reply = hosting.public(PATH)
        if reply.ok:
            require(reply.json() == EMPTY, 'contenido sísmico público inesperado')
            break
        require(reply.status_code == 404, 'HTTP inesperado al verificar feed')
        time.sleep(3)
    else:
        raise RuntimeError('Hosting restaurado, pero CDN sigue devolviendo 404: revisar propagación')
    for path in ('/radar.json', '/dr-audio/index.json', '/descargar/'):
        require(hosting.public(path).ok, 'ruta crítica falló después de restauración: ' + path)
    print('VERIFICADO: JSON sísmico vacío publicado (sin generar alertas).', flush=True)
    print('VERIFICADO: hashes de todos los demás archivos preservados.', flush=True)
    print('VERIFICADO: Radar, DR Audio y descarga accesibles.', flush=True)


def run(mode, confirm='', hosting=None):
    if mode == 'restaurar':
        require(confirm == CONFIRM, 'falta confirmación exacta de restauración')
    if hosting is None:
        secret = os.environ.get('FIREBASE_SERVICE_ACCOUNT', '')
        require(secret, 'falta service account existente de Firebase')
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(secret), scopes=['https://www.googleapis.com/auth/cloud-platform'])
        hosting = Hosting(AuthorizedSession(credentials))
    snapshot = audit(hosting)
    backup(snapshot)
    print('Sitio LIVE:', snapshot['site'], flush=True)
    print('Archivos preservados:', len(snapshot['files']), flush=True)
    print('Feed sísmico HTTP:', snapshot['publicStatus'], flush=True)
    print('Feed sísmico en manifiesto:', not snapshot['missing'], flush=True)
    if mode == 'auditar':
        print('SOLO AUDITORÍA: no se escribió ni desplegó ningún archivo.', flush=True)
        return
    restore(hosting, snapshot)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('auditar', 'restaurar'), default='auditar')
    parser.add_argument('--confirm', default='')
    args = parser.parse_args()
    run(args.mode, args.confirm)


if __name__ == '__main__':
    main()
