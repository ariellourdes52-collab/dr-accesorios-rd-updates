# DR Agenda Fase 2 — NO MEZCLAR A MAIN TODAVÍA

Esta rama tiene el workflow de publicación protegido en Firebase Hosting Spark, pero **está incompleta** mientras no se suban los dos scripts Python del paquete probado.

## Archivos que faltan en la rama
- `scripts/dr_agenda_publisher_core.py`
- `scripts/publish_dr_agenda_telegram.py`

Los dos scripts están disponibles en el ZIP de la Fase 2 entregado por ChatGPT. Deben copiarse sin alteraciones a la carpeta `scripts/` de esta rama. No ejecutar ni fusionar el workflow antes de que ambos estén disponibles y se revisen.

## Antes de activar
1. Revisar el código y probar el workflow en la rama sin hacer publicaciones.
2. Comprobar en GitHub Actions los secretos `FIREBASE_SERVICE_ACCOUNT_DR_ACCESORIOS_RD` (existente) y `DR_AGENDA_TELEGRAM_BOT_TOKEN` (nuevo, token actual del bot).
3. Aprobar cambios en `main` únicamente cuando la rama tenga scripts, workflow y pruebas.
4. Solo después desplegar Worker Telegram v2.6.1 en Cloudflare.
5. Primera publicación de evento con dos confirmaciones, sin FCM ni envío masivo.
6. Verificar catálogo y foto en `https://dr-accesorios-rd.web.app/agenda/events.json`, y la APK Android v2.6.

**No se ha cambiado Firebase Hosting ni Cloudflare por estos commits.** La automatización de vencimientos, la notificación masiva a Telegram y Android, y la integración Blogger siguen pendientes de fases futuras.
