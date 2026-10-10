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
