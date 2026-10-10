# DR Accesorios RD — Android v2.6
**Estado:** desarrollo aislado; sin APK compilado ni publicación.
**Rama:** `feature/android-v2.6-social`
**Base GitHub:** `main`, commit `56b4ce8cd3d16713f9721ee1a9bdc31ae889d5d7`
**Fecha de inicio:** 2026-10-10

## 0. Estado verificado y alcance
- El repositorio actual aloja automatización, releases y servicios complementarios, **no** los fuentes Android Studio (`.kt`, `AndroidManifest.xml`, Gradle).
- GitHub Releases: `2.4.1` es la última publicación pública; `2.5` tiene APK cargado **pero es borrador**. El workflow `release-2.5.yml` declara versión 21 y publicación prevista el 2026-12-02 a las 20:00 RD.
- **Reservar** `versionName = "2.6"`, `versionCode = 22` únicamente después de comprobar el APK y la rama Android de origen. El versionCode debe superar todas las distribuciones efectivamente publicadas.
- Este cambio solo inicializa el espacio de desarrollo y la especificación; no cambia app, APK, actualización remota, workers, Radar, audio ni Firebase.

## 1. Funcionalidades v2.6 acordadas
### P0 — Telegram en Social
- Incorporar acceso visible a **Bot oficial de DR Accesorios RD**: `https://t.me/draccesoriosrd_bot`.
- Abrir mediante intent HTTPS/Android App Links; permitir navegador cuando Telegram no esté instalado.
- No almacenar ni insertar token de bot, ID privado de usuarios ni secretos en el APK.
- Mantener intactas funciones de comentarios, respuestas, likes y acceso con Google.

### P0 — Suscripción por correo
- Integrar en Social la **suscripción existente al boletín de noticias** desde el punto de acceso público y verificado del servicio.
- Validar primero URL/endpoints reales y flujo de alta/baja en su servicio actual; no inventar API ni duplicar listas de correos.
- Evitar nuevo login o permisos Android; informar claramente de la suscripción y permitir darse de baja.
- No almacenar direcciones de correo en Analytics; evitar exponer claves de proveedores en el cliente.
- Backend (si se necesita modificar): usar integración existente, y evaluar Cloudflare Workers/D1; **Resend solo tras verificación de dominio**. No desplegar nada como parte de esta rama.

### P1 — Social renovado
- Unificar accesos existentes Instagram, TikTok, Telegram y boletín, y conservar los demás enlaces/funciones presentes una vez inspeccionado el proyecto Android real.
- Seguir Material 3/Compose, modo claro/oscuro/sistema, accesibilidad, adaptabilidad móvil, y estética de DR Accesorios RD.
- Conservar el logo oficial **sin redibujarlo** ni reconstruirlo.

### P1 — Compartir artículos
- Añadir acción de compartir URL canónica mediante Android Sharesheet; ofrecer WhatsApp, Telegram y otras apps instaladas sin depender de paquetes específicos.
- Acceso a Facebook por menú de compartir si está disponible y opción **Copiar enlace**.
- Preservar título y URL válidos; no romper la navegación, lector nativo, favoritos u offline.
- No exponer enlaces internos/de administración ni información privada.

### P2 — Intereses y medición
- Explorar filtro por etiquetas reales de Blogger, guardando preferencias localmente y sin exigir registro.
- Añadir eventos GA4 **sin información personal** para `social_telegram_open`, `newsletter_open`, `article_share`, `article_copy_link`.
- Diseñar eventos evitando duplicaciones y sin registrar email, contenido del comentario ni identificadores sensibles.
- No alterar la medición que ya existe para noticias, DR Radar o DR Audio.

## 2. Componentes que se prohíbe alterar incidentalmente
- DR Radar: alertas sísmicas, sincronización, FCM, expiraciones, publicación y hosting.
- DR Audio: voces, descarga, streaming, notificación foreground y workflows programados.
- Comunidad: comentarios, respuestas, likes, Firebase Auth, Firestore y App Check / Play Integrity.
- Inicio, notificaciones, historial, favoritos, lector Compose, offline, OTA, distribución y versionado remoto.
- Web app iOS/PWA, bot Telegram y sistema de boletín **de producción**.

## 3. Secuencia de implementación
- [x] Crear rama exclusiva para v2.6 desde el commit verificado de `main`.
- [x] Documentar alcance, límites, dependencias y pruebas.
- [ ] Obtener/integrar el proyecto fuente Android Studio **2.5 completo** y comprobar las correcciones ya presentes de 2.4.1.
- [ ] Identificar composables/actividades de Social, menú/lector y compartir; registrar un diff de mínimo alcance.
- [ ] Confirmar URL y experiencia de suscripción al boletín en producción.
- [ ] Programar Telegram y suscripción, con UI y pruebas unitarias/de instrumentación.
- [ ] Implementar compartir artículos y controles de accesibilidad; después considerar filtros.
- [ ] Ejecutar lint, test, assembleRelease y probar manualmente en Android 7+ y versiones modernas.
- [ ] Verificar que el APK resultante conserva namespace y firma, e incrementa versionCode correctamente.
- [ ] Preparar un borrador de Release 2.6 **solo** cuando exista un APK probado y checksum SHA-256.
- [ ] Auditar despliegue de `version.json`, FCM y enlaces Latest antes de habilitar publicación, únicamente tras aprobación explícita.

## 4. Criterios de aceptación
1. Social abre Telegram y formulario de boletín correctamente, con fallback cuando la app externa no existe.
2. El usuario sigue pudiendo comentar, responder, dar like e iniciar sesión con Google.
3. Compartir distribuye la URL canónica y Copiar enlace funciona.
4. No se añaden permisos Android ni tokens embebidos.
5. No cambia ningún flujo de Radar, DR Audio, notificaciones, app iOS, ni publicación de APK.
6. Build firmada y pruebas de humo en dispositivo completadas antes de declarar v2.6 estable.

## 5. Reglas para el futuro release
- Nunca reutilizar APK 2.5 con etiqueta 2.6 ni generar actualización remota sin APK 2.6 real.
- No editar ni activar `.github/workflows/release-2.5.yml` ni `.github/workflows/release-2.4.1.yml`.
- No crear cron de publicación v2.6 por defecto; ninguna fecha inventada.
- No tocar `main` antes de revisar diferencias y aprobar integración.
- Mantener una única etiqueta/versionName 2.6 al publicar y bloquear con digest del APK.

## 6. Requisito ampliado — DR Agenda + Centro de Notificaciones
**Acordado:** DR Agenda abarcará eventos tecnológicos, cine, gaming y eventos locales, con imágenes, ubicación/mapas, enlaces oficiales y opción de añadir al calendario.

### 6.1. Navegación y presentación
- Nueva pestaña **Agenda** junto a Todo, Noticias, Radar y Social dentro de `NotificationCenterChrome`, con filtros generales «No leídas» y «Guardadas» que también funcionan con la categoría Agenda.
- Cada aviso debe mostrar distintivo claro **DR AGENDA**, título, descripción, fecha local y, cuando aplique, ubicación. La imagen del evento se muestra dentro de la pantalla de detalle, sin exigir descargarla para renderizar el Centro de Notificaciones.
- Al tocar una tarjeta: marcar como leída y abrir directamente el **detalle de ese evento** (`eventId`), no la lista general ni un artículo.
- Al tocar la notificación del sistema Android: misma apertura directa a ese evento; si fue borrado o cancelado, mostrar una explicación accesible y permitir volver a Agenda.
- No alterar vistas y rutas de Noticias, Radar, Social, favoritos ni edición de notificaciones existentes.

### 6.2. Identidad del mensaje y deduplicación
- Mensaje FCM **solo datos** con `type=agenda_event`, `event_id`, `event_revision`, `notice_kind` (published/updated/reminder/cancelled), `title`, `body`.
- Tipo local de centro: `AGENDA_EVENT`, categoría `DR Agenda`, clave de origen determinista `agenda:<event_id>:<revision>:<notice_kind>`. Deduplicar vía `NotificationStore.addNotification(sourceKey=...)`; conservar leída/guardada frente a reintentos. El identificador debe validarse antes de usarse y resolver el evento solo en nuestro catálogo HTTPS, nunca abrir URLs arbitrarias suministradas por push.
- La actualización de fecha debe alterar el evento de origen; notificación nueva solo si hay cambio significativo y versión nueva, no por cada refresco de caché.
- Guardar primero en el centro local; después crear la notificación del sistema únicamente si los permisos y preferencias lo permiten.
- La versión inicial no debe generar automáticamente alertas para eventos de demostración o no confirmados.

### 6.3. Canales, privacidad y autonomía
- **Canal Android exclusivo para Agenda**, p. ej. `dr_agenda_channel_v1`, importancia normal (no alta por defecto). Jamás reutilizar o modificar el canal urgente de sismos y DR Radar.
- Tópico FCM opcional `dr_agenda_updates` con preferencia propia habilitable/deshabilitable por el usuario; no suscribirlo desde `subscribeToRequiredTopics()` ni alterar `blog_updates` o `radar_seismic_v231`.
- Activar solo los avisos pertinentes (nuevo evento destacado, cambio significativo, cancelación); recordatorios opcionales y sin aumentar la carga de WorkManager. Al añadir al calendario Android, los recordatorios los gestiona el calendario del usuario.
- No solicitar localización ni permisos de calendario. Abrir Google Maps/navegador mediante intent público tras pulsación voluntaria.
- Confirmar expiración/estado antes de abrir y mantener fallback offline con caché de la ficha.

### 6.4. Integración técnica y pruebas
- Crear `AgendaActivity`/detalle antes de habilitar `onMessageReceived` para `agenda_event`; si no existe pantalla destino, NO activar envío.
- Extender **sin reemplazar** `NotificationCenterChrome` y `NotificationCenterActivity`: clasificación Agenda excluida de «Noticias», pestaña seleccionable, icono propio, etiqueta DR AGENDA, color diferenciado y navegación a detalle.
- Asegurar click desde notificación del sistema tanto con app abierta como cerrada, Android 7+, y Android 13+ con permiso de avisos denegado (el Centro debe seguir pudiendo recibir eventos cuando corresponda).
- Pruebas: noticia, social, radar y OTA siguen abriendo correctamente; duplicado FCM no crea duplicados; evento eliminado y evento pospuesto abren un estado válido; leído/guardado persisten; links no válidos no redirigen a pantallas externas; permisos de agenda desactivados no afectan Radar.
- **Estado actual**: requisito documentado, todavía no implementado ni publicado; no hay APK de Agenda lista ni alertas enviadas.

## 7. Alimentación desde terminal Firebase — especificación de implementación pendiente
**Estado:** no existe aún `scripts/dr_agenda_admin.py` ni están publicados los endpoints; los comandos siguientes representan la interfaz que se implementará, NO comandos disponibles.

### 7.1 Datos obligatorios y ficha
- Los eventos tendrán `id`, `title`, `description`, `category`, `startAt` con zona inequívoca, `endAt` opcional, `timezone` y `status`.
- Se admitirán `imageUrl` (HTTPS propia, JPG/WebP optimizada), `venueName`, `address`, `city`, `province`, `country`, `lat`, `lng`, `mapsUrl`, `officialUrl` y `articleUrl`.
- Listado y detalle: portada, fecha, hora en `America/Santo_Domingo` y localización explícitas; si el evento es online, indicar «En línea» en vez de mapa. Si no hay imagen, placeholder con marca sin recrear el logo.
- Centro de Notificaciones: para avisos de Agenda, mostrar **fecha/hora y sede/ciudad** en el resumen cuando existan; miniatura opcional de caché, sin bloquear listado ni descargar imágenes desde FCM; imagen grande se carga en detalle.
- La notificación del sistema abre detalle por `event_id` seguro; no depender de `imageUrl` del mensaje como URL a abrir.

### 7.2 Interfaz de publicación prevista en Windows/Firebase
- Ejecutar desde raíz del proyecto que contenga `scripts/dr_agenda_admin.py`, sin cambiar a ciegas el proyecto activo.
- Flujo previsto:
  - `python scripts/dr_agenda_admin.py nuevo` → formulario interactivo que solicita título, categoría, fecha-hora, localidad o online, imagen local, enlaces y detalles.
  - `python scripts/dr_agenda_admin.py listar` → resumen del catálogo.
  - `python scripts/dr_agenda_admin.py editar ID`, `cancelar ID`, `posponer ID` → modificaciones versionadas.
  - `python scripts/dr_agenda_admin.py validar` → valida esquema/fechas/enlaces, duplicados, rutas de imagen, ancho/alto, peso y estado.
  - `python scripts/dr_agenda_admin.py publicar --preflight` → simula cambios e inventario activo.
  - `python scripts/dr_agenda_admin.py publicar` → requiere confirmación expresa; publica exclusivamente `/agenda/events.json` y `/agenda/images/<id>.webp` o `.jpg` en Hosting.
  - `python scripts/dr_agenda_admin.py avisar ID --confirm` → segunda acción independiente **solo cuando v2.6 maneje avisos Agenda**, con FCM tipado y deduplicado.
- Guardar localmente catálogo, imágenes optimizadas y un respaldo de cada versión; registrar `updatedAt` y revisión por evento. No almacenar secretos dentro del repositorio.

### 7.3 Despliegue sin impacto en Firebase
- Utilizar la API REST de Firebase Hosting y el mismo patrón de preservación de inventario activo que `scripts/deploy_radar_preserving_hosting.py` y `scripts/deploy_version_preserving_hosting.py`.
- Antes de publicar: obtener release activo, comprobar integridad de todos los hashes, rutas críticas (Radar, DR Audio, `version.json`, PWA y landing), y verificar que el catálogo remoto no cambió desde el inicio de edición.
- Construir nueva versión incluyendo **todas** las rutas actuales y solo nuevos hashes bajo `/agenda/`; detectar despliegues concurrentes y abortar en caso de conflicto; verificar URLs e inventario después del release.
- NO ejecutar `firebase deploy --only hosting` sobre un directorio parcial: una versión de Hosting describe el conjunto completo de archivos activos.
- Notificaciones Agenda deben permanecer desacopladas de la publicación de eventos; no emitir FCM por defecto.
- El publicador y sus comandos son una **especificación**: requieren implementación y pruebas en entorno aislado antes de usarlos con el Hosting real.

## 8. Retención y eliminación automática de imágenes de DR Agenda

**Requisito (10 oct. 2026):** administrar DR Agenda desde el bot oficial de Telegram, con Firebase como motor de publicación y eliminación. No añadir servicios a Railway. Toda imagen de evento se despublica y se elimina del inventario activo cuando se cancela o cuando el evento expira por fecha/hora; no eliminar contenido de Blogger, DR Audio, DR Radar, Telegram ni imágenes ajenas a Agenda.

### 8.1. Estados y reglas de tiempo
- Cada evento tiene `startAt` y un `endAt` **obligatorio para activar la expiración automática por horario**; el formulario del bot no permitirá confirmar un evento sin un `endAt` válido y posterior a `startAt`. Para eventos sin fin conocido, exigir decisión explícita sobre fecha/hora de caducidad `expiresAt` en lugar de asumir que expiran al empezar.
- Un evento `cancelled`: retirar del listado público y despublicar su imagen inmediatamente después de confirmar la cancelación del administrador; mantener un registro textual mínimo con ID, estado y fecha para abrir una notificación previa sin error.
- Un evento `confirmed` que alcance `expiresAt` (o `endAt` definido como caducidad): retirar del catálogo público y despublicar imagen. `postponed` conserva su imagen hasta reprogramación, cancelación o caducidad explícita; no borrar por fecha antigua mientras esté pospuesto.
- Repetir tareas de limpieza debe ser idempotente. No borrar imágenes si son utilizadas por otro evento, aunque en principio el nombre de archivo use el identificador exclusivo.
- Los avisos ya registrados mantienen título y estado en el Centro de Notificaciones; una ficha que fue eliminada muestra «Evento finalizado» o «Evento cancelado» sin descargar imagen inexistente.
- Guardar `deletedAt`, `deletionReason` y auditoría mínima sin conservar binarios de imagen.

### 8.2. Almacenamiento y eliminación real
- **Compatibilidad actual:** la APK Fase 3 consulta `/agenda/events.json` e imágenes `/agenda/images/<id>.webp` bajo Firebase **Hosting**, por lo que quitar un enlace JSON **no** borra el archivo.
- Para una imagen alojada en Hosting, el publicador/backend debe crear una nueva versión **completa** del sitio que preserve todas las rutas ajenas a `/agenda/` y omita específicamente las imágenes huérfanas, sin reintroducirlas en despliegues posteriores; comparar manifiesto antes/después y abortar ante concurrencia. Nunca borrar `/agenda/events.json`, otros activos ni rutas críticas.
- Hosting puede retener copias en releases anteriores y cachés. La eliminación de la **versión activa** no equivale a destrucción inmediata de todas las copias históricas. Auditar políticas de caché y retención; nunca prometer borrado global instantáneo.
- Si en una implementación futura se migra a Cloud Storage, debe actualizarse primero el contrato de URL de la APK y protegerse el borrado con reglas por ID de evento; **no** cambiar silenciosamente URLs que Fase 3 restringe al Hosting propio.

### 8.3. Ejecución automática y publicación desde Telegram
- Las cancelaciones iniciadas por administrador en el bot disparan una actualización transaccional del catálogo y limpieza del archivo (tras confirmación), **no** una operación manual de PowerShell.
- El vencimiento automático se resolverá SIN Cloud Functions ni Cloud Scheduler: usar la tarea programada del entorno gratuito ya existente del bot (si se confirma su capacidad y cuotas), o GitHub Actions con un cron y un flujo disparado por el bot, verificando retrasos/omisiones y reintentando cuando el bot reciba cualquier comando. El cron no garantiza segundo exacto. El job no envía FCM por borrar imágenes.
- **Restricción del producto: permanecer en Firebase Spark, sin Blaze, sin Cloud Functions y sin Cloud Storage for Firebase; no crear servicios nuevos en Railway.** Hosting Spark tiene cuotas gratuitas compartidas con el resto del proyecto. Verificar cuotas antes de publicar y no declarar la limpieza activada hasta probarla.
- Solo habilitar limpieza automática después de implementar y probar el backend, incluida prueba de eliminación real de ruta `/agenda/images/...` en la versión LIVE, sin afectar el manifiesto de DR Radar, DR Audio, la PWA y `version.json`.

### 8.4. Pruebas obligatorias
- Cancelar evento: desaparece de listado y su imagen devuelve 404 desde la URL activa (teniendo en cuenta caché); sus notificaciones antiguas siguen abriendo estado «Cancelado».
- Expirar evento con hora final explícita: mismo comportamiento; evento futuro no se borra y evento pospuesto no se borra por su fecha anterior.
- Dos eventos y dos imágenes: solo se elimina la imagen del evento caducado. Repetición de job no elimina nada adicional.
- Condición de carrera: si Hosting cambió, operación aborta y reintenta sin volver a publicar imágenes huérfanas ni revivir eventos.
- Verificación de imágenes y hashes de todos los archivos **fuera de /agenda/**, incluido DR Radar, DR Audio, actualizador y Web App iOS.
- Estado actual: **especificado pero no implementado ni activado**; ni bot ni Firebase borran imágenes automáticamente aún.

### 8.5. Regla definitiva: borrado real del origen y caché Android
- **El usuario requiere dos borrados independientes y automáticos:** (1) retirar/eliminar la imagen subida del almacenamiento remoto cuando el evento se cancele, se elimine o expire según su fecha y hora de vencimiento; (2) eliminar la copia en la **caché propia de DR Agenda Android** cuando su sincronización detecte el estado `cancelled`, `expired` o la desaparición del evento. El botón normal «Borrar caché» del dispositivo también elimina esas copias.
- **Decisión definitiva sin Blaze:** alojar las imágenes comprimidas WebP exclusivamente en **Firebase Hosting Spark**, bajo `/agenda/images/<eventId>.webp`. Al cancelar, borrar o expirar evento, crear nueva versión del Hosting activo quitando solamente el archivo de ese evento, con preservación del resto del manifiesto. **Limitación:** Hosting conserva versiones antiguas por su política de retención; no prometer borrado físico inmediato de todos los originales/copias. Controlar versiones/revisiones históricas y almacenamiento con opciones de retención que no comprometan DR Radar, Audio, PWA ni el sistema de rollback.
- **Compatibilidad APK Fase 3:** conservar el formato existente `/agenda/events.json` y las imágenes en `/agenda/images/<id>.webp` bajo el Hosting propio. No migrar ni abrir dominios externos; así evitamos recompilar el lector por un cambio de proveedor. Al cancelar/expirar, despublicar esa URL del inventario activo, controlar la caché y el historial; no afirmar borrado físico definitivo hasta que los releases retenidos que incluyan esa imagen hayan expirado o sido eliminados.
- Implementar una caché gestionada por `eventId` y `imageRevision`, con eliminación específica de entradas de memoria/disco al detectar cancelación, expiración, borrado o cambio de imagen. No limpiar la caché de noticias, comentarios, Audio, Radar ni el resto de la app. Las fotos cacheadas por **Telegram** están fuera del control de la APK y siguen la limpieza propia de Telegram.
- **Sin conexión:** no se puede conocer una cancelación remota ni purgar su imagen local hasta la siguiente sincronización; además, las cachés intermediarias/respaldos del proveedor pueden retener una copia temporalmente. Eliminar objeto de Storage y caché del teléfono **no permite garantizar destrucción simultánea de todas las copias históricas**.
- Para eventos terminados mantener solo los metadatos mínimos de un marcador de estado, sin imagen; las notificaciones históricas abren «Evento finalizado/cancelado» sin recargar imágenes.
- Validar con pruebas de extremo a extremo: publicar, visualizar, expirar/cancelar, comprobar que la ruta de la imagen ya no pertenece al release LIVE de Firebase Hosting y que la sincronización elimina solo la caché de ese ID; comprobar que otro evento y todos los servicios ajenos a Agenda permanecen intactos. Auditar límites Spark, CDN y retención histórica; no activar la limpieza hasta superar estas pruebas.
- **Estado:** requisito diseñado/documentado, **no implementado**; el ZIP Android Fase 3 y publicador local Fase 4A actuales todavía no hacen este borrado real.


## 9. Arquitectura sin Blaze (requisito del usuario)
- **Administración:** comandos privados de `@draccesoriosrd_bot`, verificando IDs de administrador en el servidor y confirmación antes de publicar/cancelar. Cargar foto por Telegram, validar categoría, fechas, lugar, URL y status.
- **Ejecución:** el alojamiento ya existente del bot recibe los comandos; un GitHub Actions protegido realiza la publicación segura mediante la API REST de Hosting con inventario preservado. **Firebase Hosting no ejecuta código de servidor**, solo sirve contenido estático. Si hay scheduler en el entorno del bot existente, puede disparar el barrido sin recurso nuevo; GitHub Actions cron como alternativa con latencia/omisiones posibles.
- **Datos:** `/agenda/events.json` e imágenes WebP en `/agenda/images/`, bajo cuota Spark. Cada imagen es propia de un ID, mantener referencias y revisión. El borrado de eventos retira la imagen de la versión activa y la marca como eliminada en registros; controlar releases anteriores sin sacrificar indebidamente rollback.
- **Notificaciones:** una vez la v2.6 esté probada, FCM gratuito para Android y notificación Telegram desde el bot existente. Ningún evento de prueba envía notificaciones automáticamente; separar publicar y avisar.
- **Caché Android:** la app borra caché de imágenes al detectar `cancelled`, `expired` o ausencia del ID tras sincronizar, sin borrar caché de otras funciones.
- **No hacer:** Blaze, Cloud Functions, Cloud Storage for Firebase, nuevos servicios Railway, exponer tokens en chat/GitHub; no ejecutar deploy parcial de Firebase Hosting.
- **Estado:** solo contrato de implementación: NO se ha desplegado el bot, ni el publicador remoto, ni el job de expiración, ni los cambios Kotlin de caché automática.
