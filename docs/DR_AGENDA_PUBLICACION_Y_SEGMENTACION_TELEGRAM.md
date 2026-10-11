# DR Agenda — publicación multicanal y avisos privados seleccionados
Fecha de decisión: 10 octubre 2026. Estado: **contrato de versión final, NO habilitado en producción**.

## Reglas confirmadas

1. **La auditoría NO desaparece** al habilitar la publicación. En el panel `/adminagenda` se mantendrá siempre «🧪 Auditar en GitHub (sin publicar)», con `run_mode=auditar`; el botón de publicación será adicional e independiente y requerirá doble confirmación.
2. **Un evento publicado es público** en el catálogo único `https://dr-accesorios-rd-agenda.web.app/agenda/events.json`. Lo podrán consultar la web de Agenda y `/agenda` de Telegram. Android v2.6 y Blogger/WebView consumirán ese catálogo cuando estén integrados; NO afirmar que esas integraciones ya están en producción.
3. «**Evento privado**» en el contexto de este requisito significa **difusión privada del aviso de Telegram**, no ocultar el evento del catálogo público. La selección de destinatarios se hace solamente en el panel administrativo de Telegram.
4. El administrador podrá seleccionar por evento: **Ningún aviso** (predeterminado), **todos los suscriptores de avisos de Agenda**, o **suscriptores concretos** desde una lista paginada con búsqueda y opción para marcar/desmarcar.
5. Solo son destinatarios elegibles los usuarios actuales y activos `telegram_users.is_active=1` que hayan activado explícitamente `telegram_agenda_alert_prefs.enabled=1`. Revalidar ese consentimiento **justo antes de enviar** cada aviso. El administrador no puede forzar avisos a personas sin suscripción.
6. Los destinatarios seleccionados, identificadores de Telegram, chat_id, búsquedas y preferencias son **privados de Cloudflare D1**. Nunca incluirlos en `events.json`, Firebase Hosting, imagen, URL de evento, GitHub `payload_json`, logs ni archivos de respaldo público.
7. **Publicar no implica notificar.** Solo después de verificar la publicación pública en Firebase, el administrador podrá revisar la selección y pulsar una confirmación separada: «Enviar avisos en Telegram». Mostrar cantidad de elegibles/seleccionados y vista previa.
8. Evitar duplicados por **(event_id, telegram_user_id, kind='agenda')** mediante deduplicación persistente de entrega; proteger ante doble clic, reintento, caducidad de la operación y bloqueo del bot. Usar la cola y política de límites existentes de Telegram; no modificar crons, broadcasts de noticias, Radar o Audio ni crear un servicio Railway.
9. Fallar cerrado: selector o cola inactivos/incompletos => el evento **sigue público** pero **no se envía aviso**. Mantener auditoría y publicación independientes.
10. Mantener variables protectoras separadas:
    - Worker `DR_AGENDA_BOT_PUBLISH_ENABLED` (ausente por defecto).
    - Repositorio `DR_AGENDA_PUBLISH_ENABLED` (ausente por defecto).
    - Introducir la difusión privada de Telegram con una **tercera autorización**, desactivada por defecto; no reutilizar las variables de publicación.
11. Nada de FCM ni notificaciones masivas de Agenda durante el primer evento. La primera publicación real comprobará exclusivamente el catálogo y la imagen; se verificará /agenda de Telegram y la web. Cualquier expansión a Android/Blogger o sus avisos será una fase independiente.

## Flujo administrativo final

`/adminagenda` → **Crear/editar borrador** → **🧪 Auditar** (siempre visible) → **🚀 Revisar para publicar** (si se habilitó) → **Confirmar** → GitHub Actions (`run_mode=publicar`) → Firebase Hosting **sitio secundario solamente** → **Verificar que el evento existe** → **Difusión en Telegram [Ninguno | Todos los suscritos | Seleccionar]** → **Vista previa** → **Confirmación explícita** → Cola de Telegram con recibos de entrega.

## Pruebas obligatorias antes de habilitar difusión privada

- Ninguna emisión si no se dio consentimiento Agenda; ni aunque se seleccione el usuario.
- Publicar un evento NO provoca mensajes.
- Selector y listado admin disponibles solo en chat privado y para `TELEGRAM_ADMIN_ID[_2..4]`.
- Un usuario que desactive Agenda entre selección y envío queda excluido.
- Al reenviar o pulsar dos veces, solo existe un aviso por usuario/evento.
- Cada evento sigue accesible en web, Telegram y (cuando se incorporen) Android/Blogger, aunque el aviso Telegram esté segmentado.
- Verificar que Radar, DR Audio, noticias, suscripciones y cola anterior siguen intactos.

## Situación técnica a la fecha

- Website + catálogo de Agenda v1.0 publicados en Firebase Hosting secundario.
- `publish-dr-agenda-telegram.yml` acepta `auditar` y `publicar`. Su puerta para publicación real está apagada.
- Worker preparado en modo auditoría; debe conservarse un respaldo antes de activar publicación.
- Auditoría real lanzada desde Telegram: GitHub Actions #38102198166, verde, **no publicó**.
- La selección de destinatarios y el envío de avisos privados descritos arriba son **requisitos pendientes de implementación**, no una función desplegada.

**Prohibido:** tocar el Hosting principal, alterar trabajos de DR Radar/Audio, publicar directamente en Firebase sin la auditoría y el respaldo, activar avisos sin revisión independiente del usuario.
