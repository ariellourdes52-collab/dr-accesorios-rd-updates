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
