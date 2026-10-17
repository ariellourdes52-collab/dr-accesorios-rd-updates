#!/usr/bin/env python3
"""Update two UI strings on the LIVE Radar Web App; preserve all other Hosting files."""
from __future__ import annotations
import copy
import gzip
import hashlib
import json
import os
import time
import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

SITE = "sites/dr-accesorios-rd"
BASE = "https://firebasehosting.googleapis.com/v1beta1/"
PUBLIC_BASE = "https://dr-accesorios-rd.web.app"
TARGET = "/radar-alertas/index.html"
CRITICAL = ("/radar.json", "/descargar/index.html", "/dr-audio/index.json", "/radar-widget/index.html")
EXPECTED_SHA256 = "b4bdcee54e74aa0fc89ecebb493fcf2f934be5550c6525494167b89f63ff1eb6"
PHRASES = (
    ("Configuración mínima completa.", "Essential setup complete."),
    ("Radar, sismos, publicaciones y los eventos anunciados de DR Agenda pueden llegar aunque cierres la Web App. Las notificaciones sociales siguen siendo opcionales.",
     "DR Radar, earthquake alerts, new posts and DR Events announcements can reach you even when the Web App is closed. Social notifications remain optional."),
)

def require(value, why):
    if not value:
        raise RuntimeError("ABORTADO (sin publicar): " + why)

def patch(source):
    require("DR_SITE_BILINGUAL_UI_QA_V1" in source, "No coincide el módulo bilingüe")
    require(source.count("const tr={") == 1, "Diccionario inesperado")
    require(source.count('<span class="ok">Configuración mínima completa.</span>') == 1,
            "Mensaje de Web Push diferente del revisado")
    for es, en in PHRASES:
        require(source.count(es) == 1 and en not in source, "Frase cambiada o ya traducida")
        require(json.dumps(es, ensure_ascii=False) + ":" not in source, "Entrada duplicada")
    entries = "".join("\n      " + json.dumps(es, ensure_ascii=False) + ":" +
                      json.dumps(en, ensure_ascii=False) + "," for es, en in PHRASES)
    updated = source.replace("const tr={", "const tr={" + entries, 1)
    require(updated.replace(entries, "", 1) == source, "Edición fuera del diccionario")
    return updated

def main():
    secret = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    require(secret, "No está configurada la credencial de Firebase")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(secret), scopes=["https://www.googleapis.com/auth/firebase.hosting"])
    session = AuthorizedSession(creds)

    def api(method, path, **kw):
        response = session.request(method, BASE + path, timeout=60, **kw)
        response.raise_for_status()
        return response.json() if response.content else {}

    def active():
        release = api("GET", SITE + "/channels/live")["release"]
        version_id = release["version"]["name"].rsplit("/", 1)[1]
        return release["name"], SITE + "/versions/" + version_id

    def manifest(version):
        result = {}
        token = None
        while True:
            params = {"pageSize": 1000, "status": "ACTIVE"}
            if token:
                params["pageToken"] = token
            page = api("GET", version + "/files", params=params)
            for item in page.get("files", []):
                path = item["path"]
                require(path not in result, "Ruta duplicada " + path)
                result[path] = item["hash"]
            token = page.get("nextPageToken")
            if not token:
                return result

    old_release, old_version = active()
    old_files = manifest(old_version)
    require(bool(old_files), "Inventario de Hosting vacío")
    require(all(path in old_files for path in (TARGET,) + CRITICAL), "Falta ruta crítica")
    config = copy.deepcopy(api("GET", old_version).get("config", {}))

    response = requests.get(PUBLIC_BASE + TARGET, params={"no_cache": time.time_ns()},
                            headers={"Cache-Control": "no-cache"}, timeout=30)
    response.raise_for_status()
    live_digest = hashlib.sha256(response.content).hexdigest()
    if live_digest != EXPECTED_SHA256:
        print("El HTML público difiere del respaldo original; se conservará la versión activa.", flush=True)
    original = response.content.decode("utf-8")
    # Edición sobre el HTML vigente: no se restaura ni se reemplaza con un respaldo antiguo.
    for signature in ("DR_RADAR_BROWSER_ALERTS_V2", "const tr={",
                      "const FLAG = 'drRadarPushSubscribedV1'",
                      "function setStatus(html)", "function apply()",
                      "new MutationObserver(schedule).observe(document.body"):
        require(original.count(signature) == 1,
                "No coincide la estructura vigente de Web App: " + signature)
    require("DR_SITE_BILINGUAL_UI_QA_V1" in original,
            "Falta el módulo bilingüe estable")
    updated = patch(original)
    print("PRECHECK OK: dos frases del diccionario, resto intacto", flush=True)

    body = gzip.compress(updated.encode("utf-8"), mtime=0)
    digest = hashlib.sha256(body).hexdigest()
    expected = dict(old_files)
    expected[TARGET] = digest
    require(len(old_files) == len(expected), "Varió el número de archivos")
    require(all(expected[p] == h for p, h in old_files.items() if p != TARGET),
            "Se modificaría un archivo ajeno")

    new = api("POST", SITE + "/versions", json={"config": config})
    new_version = SITE + "/versions/" + new["name"].rsplit("/", 1)[1]
    already_uploaded = False
    pairs = list(expected.items())
    for offset in range(0, len(pairs), 1000):
        batch = api("POST", new_version + ":populateFiles",
                    json={"files": dict(pairs[offset:offset+1000])})
        required = set(batch.get("uploadRequiredHashes", []))
        require(required <= {digest}, "Firebase solicitó resubir archivos preservados")
        if digest in required and not already_uploaded:
            upload = session.post(batch["uploadUrl"] + "/" + digest, data=body,
                                  headers={"Content-Type": "application/octet-stream"}, timeout=60)
            upload.raise_for_status()
            already_uploaded = True

    require(manifest(new_version) == expected, "El inventario nuevo no coincide")
    api("PATCH", new_version, params={"updateMask": "status"}, json={"status": "FINALIZED"})
    require(active() == (old_release, old_version), "Hubo otro deploy; abortado por concurrencia")
    published = api("POST", SITE + "/releases", params={"versionName": new_version},
                    json={"message": "Radar Web App: translate two Web Push confirmation texts only; retain all assets"})
    require(active()[1] == new_version, "La nueva versión no quedó activa")
    require(manifest(new_version) == expected, "Comprobación posterior del inventario falló")
    # CDN verification: do not claim the user-facing phrase is visible until cached pages settle.
    served_ok = False
    for _ in range(12):
        live = requests.get(PUBLIC_BASE + TARGET,
                            params={"verified_deploy": time.time_ns()},
                            headers={"Cache-Control": "no-cache"}, timeout=30)
        if live.ok and all(json.dumps(es, ensure_ascii=False) + ":" +
                           json.dumps(en, ensure_ascii=False) in live.text for es, en in PHRASES):
            served_ok = True
            break
        time.sleep(5)
    require(served_ok, "Versión publicada, pero CDN aún no confirmó las dos entradas; revisar cache")

    print("PUBLICADO: solo " + TARGET, published.get("name", ""), flush=True)
    print("VERIFICADO: todos los demás hashes idénticos al Hosting anterior", flush=True)

if __name__ == "__main__":
    main()
