# DR Agenda Fase 2 — Implementación QUIRÚRGICA y AISLADA (10 octubre 2026)

## Dictamen técnico tras investigación oficial

Firebase Hosting REST publica cada versión como manifiesto completo; compartir el **mismo sitio LIVE** entre Radar, Audio y Agenda genera competencia entre despliegues que no puede eliminarse solo modificando el workflow de Agenda. **Solución elegida: un segundo sitio Firebase Hosting en el mismo proyecto Spark**, exclusivo de Agenda y con versión/release independiente. Es una capacidad oficial de Firebase Hosting disponible en Spark.

- Sitio actual **PROHIBIDO para Agenda**: `sites/dr-accesorios-rd`; `https://dr-accesorios-rd.web.app`. No tocarlo.
- Sitio nuevo previsto, **todavía no creado ni reservado**: `sites/dr-accesorios-rd-agenda`; `https://dr-accesorios-rd-agenda.web.app`.
- Ruta de catálogo: `https://dr-accesorios-rd-agenda.web.app/agenda/events.json`.
- Rutas de imágenes: `https://dr-accesorios-rd-agenda.web.app/agenda/images/<id>.webp`.

El subdominio necesita disponibilidad global y su sitio debe crearse **en el mismo proyecto Firebase**, sin cambiar facturación ni crear recursos de Blaze. Las cuotas Spark de Hosting se comparten a nivel del proyecto.

## ÚNICOS archivos alterados en rama Agenda

1. `scripts/dr_agenda_publisher_core.py`: sitio fijo de Agenda, distinta URL e imágenes, verificación `sites.get`, soporte de sitio recién creado sin release, rechaza usar Hosting principal en todo despliegue.
2. `.github/workflows/publish-dr-agenda-telegram.yml`: se agrega prueba de aislamiento, `run_mode=auditar` predeterminado, `DR_AGENDA_PUBLISH_ENABLED=true` obligatorio para LIVE, respaldo exportado como artefacto.
3. `tests/test_agenda_hosting_isolation.py`: verifica aislamiento, no configuración mutable del sitio, fallar cerrado si el sitio no existe, first release y URL de imágenes externas.
4. Esta documentación. El script `scripts/publish_dr_agenda_telegram.py` conserva su lógica y permisos, obtiene las URL del core aislado.

**No modificar** scripts, workflows, publicadores ni agrupaciones de concurrencia de Radar/Audio, versión APK, configuración actual Hosting, Cloudflare, Blogger, PWA o Firebase Rules.

## Integración prevista cuando se apruebe

- El Worker Telegram v2.6.1 de la futura fase se cambiará **solo en dos constantes nuevas de Agenda**: `DR_AGENDA_CATALOG_URL` y `DR_AGENDA_ORIGIN`; conservar Worker v2.6.0 desplegado hasta realizar pruebas.
- Android v2.6 Fase 3 requiere dos cambios exclusivos de Agenda: `AgendaRepository.kt` URL de catálogo y `AgendaModels.kt` hosts permitidos para imágenes.
- Blogger y WebView se integrarán en la última fase desde el sitio secundario; no insertar widgets ahora.

## Pruebas locales realizadas

- Suite del publicador: **20 pruebas Python** incluidas verificaciones de seguridad y aislamiento, todas superadas con datos simulados. No se envió nada a Firebase.
- Se preparó Worker v2.6.1 con solo dos constantes nuevas modificadas; **no desplegado**.
- Debe ejecutarse posteriormente una auditoría real de solo lectura en la nueva URL una vez creado el sitio.
- La existencia real del nuevo sitio, sus permisos IAM, disponibilidad del subdominio y resultado de primer deploy **no están verificadas**. No declarar producción lista.

## Activación — orden obligatorio

1. Confirmar autorización para crear segundo sitio Firebase Hosting sin cambiar Spark. En Firebase Console > Hosting > Add another site, elegir `dr-accesorios-rd-agenda` si está disponible. **No desplegar contenido en sitio principal**.
2. Verificar el sitio en consola y que la URL nueva pertenece al proyecto esperado.
3. Revisar PR #3 y fusionar solamente después de autorización expresa; no ejecutar hasta site creado.
4. Ejecutar en modo `auditar` con payload de evento ficticio y revisar el manifiesto sin escrituras.
5. Verificar el secreto `DR_AGENDA_TELEGRAM_BOT_TOKEN` y credenciales de servicio. Mantener `DR_AGENDA_PUBLISH_ENABLED` ausente, no habilitar LIVE todavía.
6. Una vez aprobado el preflight, respaldos y despliegues de prueba, publicar 1 solo evento sin FCM ni mensajes masivos y verificar HTTPS público, imagen, Radar y Audio.
7. Actualizar las **dos constantes de Agenda** del Worker y **dos archivos Agenda** de Android v2.6; no sobreescribir cambios posteriores de esos archivos, fusionar quirúrgicamente.
8. Activar avisos y expiración SOLO en fases posteriores, con pruebas y consentimiento independiente.

## Estado

**Código aislado listo en rama de desarrollo, PR #3 NO fusionado.** La publicación LIVE sigue desactivada por defecto. **No se ha creado un segundo sitio, desplegado un evento, modificado el Hosting principal, Cloudflare ni la APK.**

Documentación oficial: https://firebase.google.com/docs/hosting/multisites ; https://firebase.google.com/pricing ; https://firebase.google.com/docs/hosting/api-deploy .
