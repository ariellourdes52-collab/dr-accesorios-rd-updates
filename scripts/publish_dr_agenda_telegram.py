#!/usr/bin/env python3
"""GitHub Actions only: Telegram-approved event -> safe Firebase Hosting Spark.

No FCM sends, no Telegram broadcasts. Service-account authentication remains in
GitHub Secrets; nothing sensitive is stored in the site or repository.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageOps

from dr_agenda_publisher_core import (
    AgendaError, PUBLIC, CATEGORIES, MAX_EVENTS, MAX_IMAGE,
    fingerprint, read_remote, image_for, publish, stamp,
)

VALID_EVENT_ID = re.compile(r"evt-[a-z0-9][a-z0-9-]{1,42}\Z")
FILE_PATH = re.compile(r"(?:photos|documents)/[A-Za-z0-9_.\-/]{1,220}\Z")


def require(ok, message):
    if not ok:
        raise AgendaError(message)


def validated_request(text):
    require(len(text.encode('utf-8')) <= 18000, "Solicitud demasiado grande")
    obj = json.loads(text)
    require(isinstance(obj, dict), "Solicitud Agenda debe ser un objeto")
    request_id = obj.get('requestId')
    require(isinstance(request_id, str) and re.fullmatch(r'[0-9a-f-]{36}', request_id), 'requestId inválido')
    eid = obj.get('id')
    require(isinstance(eid, str) and VALID_EVENT_ID.fullmatch(eid), 'ID de evento inválido')
    title = obj.get('title')
    description = obj.get('description')
    require(isinstance(title, str) and 3 <= len(title) <= 120, 'Título inválido')
    require(isinstance(description, str) and 10 <= len(description) <= 1200, 'Descripción inválida')
    category = obj.get('category')
    require(category in CATEGORIES, 'Categoría inválida')
    # Explicit timezone is mandatory; the publisher validates actual dates.
    start, end = obj.get('startAt'), obj.get('endAt')
    from dr_agenda_publisher_core import parse_iso
    first, last = parse_iso(start), parse_iso(end)
    require(last > first, 'La fecha final debe ser posterior al inicio')
    # Neither past events nor unbounded future dates may be published.
    now = datetime.now(timezone.utc)
    require(last > now, 'El evento ya venció')
    require((first - now).days <= 730, 'Fecha inicial demasiado lejana')
    kind = obj.get('type')
    require(kind in ('online', 'presencial'), 'Tipo de evento inválido')
    loc = {'type': kind, 'online': kind == 'online',
           'name': str(obj.get('place') or '')[:120],
           'address': str(obj.get('address') or '')[:120],
           'city': str(obj.get('city') or '')[:120],
           'province': str(obj.get('province') or '')[:120],
           'lat': None, 'lng': None}
    if kind == 'presencial':
        require(bool(loc['city'] and loc['province'] and (loc['name'] or loc['address'])),
                'Ubicación presencial incompleta')
    from dr_agenda_publisher_core import confirm_https
    url = confirm_https(obj.get('officialUrl') or '', 'officialUrl')
    file_id = obj.get('telegramPhotoFileId') or ''
    require(isinstance(file_id, str) and len(file_id) <= 512 and
            (not file_id or bool(re.fullmatch(r'[A-Za-z0-9_-]{5,512}', file_id))),
            'ID de fotografía Telegram inválido')
    event = {
        'id': eid, 'title': title, 'description': description,
        'category': category, 'status': 'confirmed',
        'startAt': start, 'endAt': end,
        'imageUrl': '', 'officialUrl': url, 'articleUrl': '', 'location': loc,
    }
    return event, file_id


def download_telegram_image(file_id: str, filename: Path, token: str):
    """Fetch only Telegram-provided file paths. Fail closed for oversized pictures."""
    require(bool(token), 'Falta TELEGRAM_BOT_TOKEN de GitHub Secrets')
    endpoint = 'https://api.telegram.org/bot' + token + '/getFile'
    response = requests.post(endpoint, json={'file_id': file_id}, timeout=20)
    response.raise_for_status()
    body = response.json()
    require(body.get('ok') is True, 'Telegram no encontró la fotografía')
    meta = body.get('result') or {}
    path = meta.get('file_path')
    require(isinstance(path, str) and bool(FILE_PATH.fullmatch(path)) and '..' not in path,
            'Ruta de foto Telegram no permitida')
    size = meta.get('file_size')
    require(size is None or 0 < int(size) <= 20_000_000,
            'Foto Telegram supera 20 MB')
    url = 'https://api.telegram.org/file/bot' + token + '/' + quote(path, safe='/')
    with requests.get(url, timeout=40, stream=True, allow_redirects=False) as image:
        image.raise_for_status()
        limit = 20_000_000
        total = 0
        with filename.open('wb') as out:
            for block in image.iter_content(chunk_size=65536):
                if not block:
                    continue
                total += len(block)
                require(total <= limit, 'Fotografía Telegram demasiado grande')
                out.write(block)
        require(total > 0, 'Fotografía Telegram vacía')
    Image.MAX_IMAGE_PIXELS = 24_000_000
    try:
        with Image.open(filename) as im:
            require(im.format in ('JPEG', 'PNG', 'WEBP'), 'Formato de imagen no permitido')
            im.verify()
    except (OSError, ValueError) as e:
        raise AgendaError('Imagen Telegram dañada o inválida') from e


def build_workspace(payload_json: str, workspace: Path, *, image_fetch=download_telegram_image):
    event, telegram_file_id = validated_request(payload_json)
    remote = read_remote()
    if remote is None:
        base_revision, events = 0, []
    else:
        body = json.loads(remote)
        require(body.get('schemaVersion') == 1 and isinstance(body.get('events'), list),
                'Catálogo remoto incompatible')
        base_revision, events = body['revision'], body['events']
        require(isinstance(base_revision, int) and base_revision >= 1, 'Revisión remota inválida')
    # Idempotency: never re-create the same event on a retry. Reject mutations.
    require(not any(e.get('id') == event['id'] for e in events if isinstance(e, dict)),
            'El evento ya existe; no se vuelve a publicar')
    require(len(events) < MAX_EVENTS, 'Catálogo lleno')
    if telegram_file_id:
        photo = workspace / 'telegram-original.bin'
        image_fetch(telegram_file_id, photo, os.environ.get('TELEGRAM_BOT_TOKEN', ''))
        event['imageUrl'] = image_for(workspace, event['id'], str(photo))
        photo.unlink(missing_ok=True)
    state = {
        'schemaVersion': 1,
        'baseRevision': base_revision,
        'remoteFingerprint': fingerprint(remote),
        'events': events + [event],
    }
    return state, event


def main():
    require(os.environ.get('DR_AGENDA_CI_APPROVED') == 'TELEGRAM_ADMIN_CONFIRMED',
            'La publicación remota requiere autorización desde Telegram')
    payload = os.environ.get('DR_AGENDA_PAYLOAD_JSON', '')
    require(bool(payload), 'Falta solicitud del bot')
    with tempfile.TemporaryDirectory(prefix='dr-agenda-') as dirname:
        root = Path(dirname)
        state, event = build_workspace(payload, root)
        print('DR Agenda: preflight obligatorio para', event['id'], flush=True)
        publish(root, state, preflight=True)
        print('DR Agenda: publicando SOLO rutas /agenda/', flush=True)
        publish(root, state, preflight=False,
                confirm='PUBLICAR-AGENDA', non_interactive=True)
        # Verify after publish by ID/revision, independently of the library call.
        raw = read_remote()
        require(raw is not None, 'Catálogo no aparece tras publicación')
        released = json.loads(raw)
        require(any(e.get('id') == event['id'] and e.get('title') == event['title']
                    for e in released.get('events', []) if isinstance(e, dict)),
                'La ficha del evento no se pudo verificar en Hosting')
        print('✅ Evento publicado y verificado en Hosting: ' + event['id'])
        print('✅ SIN notificaciones Telegram ni FCM: validar primero en Android')


if __name__ == '__main__':
    try:
        main()
    except (AgendaError, ValueError, requests.RequestException) as error:
        # Never print access tokens, URLs containing Telegram credentials or user payloads.
        print('❌ DR Agenda: publicación no completada: ' + type(error).__name__)
        raise SystemExit(1)
