# DR Agenda Fase 2 — Revisión previa a activar en main

## Inventario verificado

Los archivos se encuentran ahora en la ruta esperada de la rama `feature/dr-agenda-telegram-fase2`:

- `.github/workflows/publish-dr-agenda-telegram.yml`
- `scripts/dr_agenda_publisher_core.py`
- `scripts/publish_dr_agenda_telegram.py`

Los dos scripts se trasladaron desde la raíz sin modificar su contenido.

## Pruebas realizadas

- 9 pruebas Python del publicador, sin conexión a Firebase real.
- 22 verificaciones de DR Agenda en JavaScript.
- Pruebas integradas de Telegram y doble confirmación de publicación, sin enviar avisos.
- Preflight simulado: conserva hashes de todos los archivos ajenos a `/agenda/`.

Estas pruebas **no verifican** el secreto GitHub ni garantizan el resultado del primer despliegue real en Firebase.

## Antes de fusionar/activar

1. El administrador confirmó haber creado el secreto `DR_AGENDA_TELEGRAM_BOT_TOKEN`. Su valor no es visible para ChatGPT y no debe compartirse por chat.
2. Comprobar que el secreto existente `FIREBASE_SERVICE_ACCOUNT_DR_ACCESORIOS_RD` sigue configurado. No rotar ni modificar el secreto de Radar.
3. Revisar PR #3 y obtener aprobación expresa para fusionar a `main`.
4. Solo tras la fusión, GitHub puede exponer el workflow a `workflow_dispatch`. **La fusión por sí sola no ejecuta ninguna publicación.**
5. Instalar Worker Telegram v2.6.1 en Cloudflare únicamente después de comprobar que el workflow está disponible; mantener respaldo del Worker actual.
6. Hacer una primera publicación controlada de un evento de prueba, sin FCM ni envíos masivos; validar `https://dr-accesorios-rd.web.app/agenda/events.json` y la foto optimizada.
7. Confirmar que Radar, Audio, PWA y los archivos de actualización permanecen accesibles.

## Límites de esta fase

- No hay publicación real verificada, limpieza automática de vencidos ni notificaciones masivas.
- No hay cambios de Blaze ni nuevos servicios Railway.
- No se ha desplegado el Worker Telegram v2.6.1 desde este PR.
- El código del publicador conserva archivos anteriores de Hosting por hash, pero la liberación simultánea de otros flujos merece vigilancia durante la primera prueba.

**Estado:** archivos completos y pruebas locales superadas. PR #3 pendiente de autorización para integrar a `main`.
