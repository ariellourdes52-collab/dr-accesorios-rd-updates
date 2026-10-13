#!/usr/bin/env python3
"""Read-only production probe for the DR Radar seismic JSON feed.

No Firebase credentials or write permissions. Works with the legitimate empty
feed while no earthquakes are active. GitHub Actions handles incident issues.
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PUBLIC_FEED = "https://dr-accesorios-rd.web.app/radar-seismic-v231.json"
MAX_BYTES = 512_000


def validate_feed(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("El feed no es un objeto JSON")
    if raw.get("schemaVersion") != 1:
        raise ValueError("schemaVersion sísmico inesperado")
    if raw.get("enabled") is not True:
        raise ValueError("Feed sísmico desactivado")
    if not isinstance(raw.get("alerts"), list):
        raise ValueError("alerts no es una lista")
    if not isinstance(raw.get("updatedAt"), str):
        raise ValueError("updatedAt no es texto")
    return raw


def fetch_feed() -> dict:
    url = PUBLIC_FEED + "?dr_health=" + str(time.time_ns())
    request = Request(url, headers={
        "User-Agent": "DR-Radar-Seismic-Health/1.0",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    with urlopen(request, timeout=12) as response:
        status = response.status
        if status != 200:
            raise RuntimeError(f"HTTP {status} en feed sísmico")
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise RuntimeError("Feed sísmico demasiado grande")
    return validate_feed(json.loads(body.decode("utf-8")))


def check(retries: int = 3, delay: float = 7, reader=None) -> dict:
    reader = reader or fetch_feed
    last = None
    for attempt in range(1, retries + 1):
        try:
            result = validate_feed(reader())
            print(f"OK intento {attempt}: HTTP 200, schemaVersion=1, "
                  f"alerts={len(result['alerts'])}", flush=True)
            return result
        except (Exception) as exc:
            last = exc
            print(f"AVISO intento {attempt}/{retries}: {type(exc).__name__}: {exc}",
                  flush=True)
            if attempt != retries:
                time.sleep(delay)
    raise RuntimeError(f"Feed sísmico no saludable tras {retries} intentos: {last}")


def ping_healthchecks() -> None:
    """Optional: Healthchecks.io emails when this separate ping goes missing."""
    value = os.environ.get("DR_SEISMIC_HEALTHCHECKS_PING_URL", "").strip()
    if not value:
        print("Ping de Healthchecks sísmico no configurado (opcional).", flush=True)
        return
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in ("hc-ping.com", "hc-ping.eu"):
        raise ValueError("URL de Healthchecks inválida; usar https://hc-ping.com/...")
    req = Request(value, headers={"User-Agent": "DR-Radar-Seismic-Health/1.0"})
    with urlopen(req, timeout=12) as response:
        if response.status not in (200, 201):
            raise RuntimeError(f"Ping Healthchecks HTTP {response.status}")
    print("Healthchecks sísmico: ping enviado.", flush=True)


def main() -> int:
    try:
        check()
    except Exception as exc:
        print(f"::error title=DR Radar sísmico caído::{type(exc).__name__}: {exc}",
              flush=True)
        return 1
    try:
        ping_healthchecks()
    except Exception as exc:
        # Feed is healthy; missing pings still alert via independent HC check.
        print(f"::warning title=Healthchecks sísmico::{type(exc).__name__}: {exc}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
