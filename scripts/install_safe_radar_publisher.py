#!/usr/bin/env python3
"""Patch the Cloud Shell Radar publisher so it never performs a full Hosting deploy."""
from pathlib import Path
import shutil
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "publish_radar_alert_unified.py"

OLD = '''        run_command(
            [
                "firebase",
                "deploy",
                "--only",
                "hosting"
            ]
        )'''

NEW = '''        run_command(
            [
                sys.executable,
                str(
                    PROJECT_DIR /
                    "scripts" /
                    "deploy_radar_preserving_hosting.py"
                ),
                str(RADAR_FILE)
            ]
        )'''

SAFE_MARKER = "deploy_radar_preserving_hosting.py"


def main():
    if not TARGET.exists():
        raise SystemExit(
            f"No existe {TARGET}. Ejecuta este instalador desde el clon ~/dr-updates."
        )

    text = TARGET.read_text(encoding="utf-8")

    if SAFE_MARKER in text:
        print("✅ El publicador de Radar ya está blindado.")
        return

    if OLD not in text:
        raise SystemExit(
            "❌ No encontré el bloque antiguo de firebase deploy. "
            "No se modificó nada."
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = TARGET.with_name(
        TARGET.name + f".before_safe_hosting_{stamp}.bak"
    )
    shutil.copy2(TARGET, backup)

    text = text.replace(OLD, NEW, 1)
    TARGET.write_text(text, encoding="utf-8")

    print(f"✅ Backup: {backup}")
    print("✅ Publicador Radar blindado.")
    print(
        "Desde ahora solo reemplaza /radar.json y preserva "
        "/descargar/, DR Audio y el resto de Firebase Hosting."
    )


if __name__ == "__main__":
    main()
