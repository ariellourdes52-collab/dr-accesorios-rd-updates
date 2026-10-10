# DR Agenda Fase 2 — Auditoría de seguridad y aislamiento (10 de octubre de 2026)

## Dictamen

**No existe garantía de riesgo cero. No activar la publicación LIVE todavía.** Los cambios permanecen en el PR #3, aislados de `main`, y ninguna prueba modificó Firebase Hosting.

### Comprobaciones verificadas

- PR #3 cambia únicamente código nuevo de DR Agenda, workflow, pruebas y esta documentación. No edita workflows, datos, scripts ni configuración de DR Radar, DR Audio, APK o Blogger.
- GitHub Actions utiliza `workflow_dispatch` solamente, no `push`, `schedule` ni disparadores automáticos.
- El workflow otorga al `GITHUB_TOKEN` solo `contents: read`; acceso a Hosting mediante `FIREBASE_SERVICE_ACCOUNT_DR_ACCESORIOS_RD`, reutilizado sin modificarlo.
- El publicador comprueba versiones y rutas críticas e intenta conservar íntegramente archivos y configuración de Firebase Hosting; compara el manifiesto con la versión activa antes de liberar.
- Imagen comprimida a WebP con límites, validación de id/fechas y duplicados. Sin FCM ni envíos masivos.
- La API oficial Hosting exige un manifiesto **completo** por despliegue, no parches independientes de directorio.

### Riesgos detectados y mitigación

**1. CRÍTICO — despliegues simultáneos de Radar/Audio y Agenda.** Los publicadores mantienen diferentes grupos de concurrencia y Hosting sustituye toda la versión LIVE. Si uno prepara su manifiesto justo antes de que otro libere una versión, un despliegue posterior podría revertir cambios del primero; las verificaciones antes de publicar reducen, pero no eliminan, ese riesgo entre lectura y escritura. **Pendiente:** acordar mecanismo común de exclusión/serialización entre todos los procesos que escriben al mismo sitio (GitHub y cualquier publicador externo), o reservar una ventana de mantenimiento y verificar después. **Hasta entonces NO habilitar publicación LIVE de Agenda**.

**2. ALTO — respaldo temporal no recuperable.** El respaldo anterior se guardaba dentro de `TemporaryDirectory`, que se eliminaba al terminar el workflow. **Corregido:** usar `DR_AGENDA_BACKUP_DIR` externo y subir `firebase-manifest.json` junto a `local.json` como artefacto de GitHub con retención de 7 días. No es copia integral de los archivos; la recuperación efectiva depende de la versión anterior conservada por Hosting y exige procedimiento de rollback verificado.

**3. ALTO — publicación habilitada por defecto.** Originalmente, un `workflow_dispatch` podía iniciar escrituras inmediatamente con el JSON introducido. **Corregido:** selector `run_mode` con valor por defecto `auditar`; el modo `publicar` solo procede cuando la variable GitHub `DR_AGENDA_PUBLISH_ENABLED` sea exactamente `true`. En ausencia de la variable, falla *antes de abrir una sesión de escritura*. Así, fusionar el PR no activa publicaciones.

**4. MEDIO — control de origen de la solicitud.** La comprobación de administrador y doble confirmación reside en el Worker; el workflow solo ve un JSON autenticado a través de los permisos de GitHub y una constante de CI. No verifica criptográficamente que la orden surgió de Telegram. **Pendiente:** evaluar firma HMAC con secreto independiente y permisos mínimos de GitHub, sobre todo si más personas adquieren capacidad de ejecutar workflows.

**5. MEDIO — falta prueba en Hosting real.** Las pruebas son locales/simuladas; todavía no se ha comparado un inventario real, permisos de cuenta de servicio, disponibilidad de la URL ni ejecución GitHub bajo secretos. **Pendiente:** ejecutar `auditar` sobre un evento ficticio válido, comprobando que no crea versiones/release.

**6. MEDIO — compatibilidad del bot.** El Worker v2.6.1 empaquetado antes de esta auditoría debe enviar `run_mode: publicar`; sin este campo el workflow funciona por defecto en modo **auditar**, y no crea el evento aunque Telegram diga solicitud enviada. **No desplegar Worker v2.6.1 hasta actualizarlo y probarlo**. Esto no afecta al Worker v2.6.0 actualmente en Cloudflare.

**7. BAJO — imágenes históricas y expiración.** La eliminación automática de eventos/imágenes no pertenece a esta fase; retirar archivos de Hosting LIVE no purga versiones antiguas, clientes ni fotos enviadas por Telegram. No prometer eliminación definitiva.

### Pruebas

- Suite previa: **9 pruebas Python** del publicador + **22 verificaciones JS** + pruebas de integración simuladas con el bot.
- Nueva suite: **3 pruebas específicas de seguridad** en `tests/test_dr_agenda_guardrails.py`: modo auditoría sin publicación, rechazo de LIVE sin variable y respaldo persistente. Probadas localmente y añadidas al workflow antes de cualquier acceso a Hosting.

### Antes de permitir publicación LIVE

1. Revisar coordinación de los escritores Firebase existentes sin romper Radar ni Audio.
2. Mantener `DR_AGENDA_PUBLISH_ENABLED` **sin definir**, y fusionar PR #3 solo con autorización explícita (por defecto publicación bloqueada).
3. Ejecutar una auditoría remota real en modo `auditar`, sin crear versiones ni releases.
4. Revisar los artefactos de respaldo y ruta de rollback a versión anterior de Hosting.
5. Verificar que Cloudflare conserva la versión estable del bot hasta tener su actualización compatible.
6. Solo tras cumplir lo anterior, autorizar `DR_AGENDA_PUBLISH_ENABLED=true`, publicar un evento de prueba controlado, y comprobar Radar, Audio, descarga APK, PWA y Blogger.

### Costos y alcance

No introduce Cloud Functions, Cloud Storage ni Blaze; usa Firebase Hosting Spark, GitHub Actions y Telegram existentes. Se mantienen cuotas y límites gratuitos. No se han activado notificaciones masivas, limpieza automática de vencidos ni edición de Blogger.

**ESTADO FINAL:** revisión de código con dos mejoras preventivas implementadas. Riesgo de concurrencia **todavía sin resolver**: NO dar por segura la publicación real ni fusionar sin consentimiento.
