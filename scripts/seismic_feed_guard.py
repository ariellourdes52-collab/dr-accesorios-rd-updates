"""Fail-closed guard: ordinary Firebase Hosting publications cannot drop seismic feed."""
from __future__ import annotations

SEISMIC_PATH = "/radar-seismic-v231.json"


def require_seismic_preserved(before: dict[str, str], after: dict[str, str]) -> None:
    """Must run BEFORE :populateFiles / release in all non-seismic primary-site writers.

    The only exceptions are the dedicated seismic-feed maintenance and restore
    operations, which may legitimately change the feed on purpose.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise RuntimeError("ABORTADO: inventario Hosting inválido para protección sísmica")
    original = before.get(SEISMIC_PATH)
    if not isinstance(original, str) or not original:
        raise RuntimeError("ABORTADO: feed sísmico falta en versión LIVE; no desplegar")
    if after.get(SEISMIC_PATH) != original:
        raise RuntimeError(
            "ABORTADO: el despliegue intentó eliminar/modificar "
            "/radar-seismic-v231.json; hashes deben conservarse intactos"
        )
