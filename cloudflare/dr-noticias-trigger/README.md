# DR Noticias Tech — Disparador Cloudflare Workers Free

**Estado inicial: DESACTIVADO.** Este Worker solo dispara el workflow
`.github/workflows/dr-noticias-railway-dispatch.yml` de GitHub Actions.
El nombre del archivo de workflow es histórico; **Railway no interviene**.

GitHub continúa haciendo el trabajo editorial de Blogger, preparando portadas
JPEG y publicando en **Instagram @drnoticiastech**, la fanpage Facebook DR
Noticias Tech y **Threads @drnoticiastech**. Los tokens Meta siguen exclusivamente
en GitHub Actions Secrets; no se copian a Cloudflare.

## Arquitectura y horario

- **Cloudflare Cron Trigger**: `8 13-23,0-2 * * *` (UTC). Entre las 09:08 y las
  22:08 de República Dominicana, una ejecución cada hora, 14 al día.
- **Cloudflare Worker**: una sola petición HTTPS al endpoint oficial de GitHub
  `POST /repos/ariellourdes52-collab/dr-accesorios-rd-updates/actions/workflows/dr-noticias-railway-dispatch.yml/dispatches`,
  cuerpo fijo `{"ref":"main"}`. Nunca hace POST a Facebook, Instagram o Threads.
- **GitHub Actions**: ejecuta cada canal con sus secretos existentes y sus grupos
  de concurrencia independientes, fecha de activación, límite de tres noticias por
  día y ledger por cuenta.
- **Sin endpoint HTTP público**: solo existe el handler `scheduled`.
- **Sin reintentos automáticos del Worker**: una respuesta incierta queda en
  Cloudflare como error; GitHub conserva su propia deduplicación.
- Los cron originales de GitHub **NO se modifican**; son respaldo hasta validar
  Cloudflare. Cuando se valide y se desee ahorrar minutos en GitHub, estudiar
  reducir su frecuencia sin perder respaldo.

## Compilaciones Preview (PR)

**Configuración de este proyecto: Preview Builds desactivados en Cloudflare.**
Este Worker solo tiene un handler `scheduled` (no sirve HTTP), así que las
Previews automáticas no son necesarias para probar su función. Los PR se verifican
con los tests offline en GitHub Actions. Production Builds siguen habilitados
para despliegues de `main`, con Build command vacío, Deploy command
`npx wrangler deploy` y Root directory `/cloudflare/dr-noticias-trigger`.

Como defensa adicional, `[previews.vars]` conserva
`DR_NOTICIAS_CF_ENABLED = "false"` por si más adelante se activan Previews.
Un estado rojo histórico de Cloudflare Preview no demuestra un fallo de
despliegue en Production; verificar siempre el build de la rama `main`.

## Pruebas antes del despliegue

Desde la carpeta `cloudflare/dr-noticias-trigger`, ejecutar:

```bash
node --test tests/trigger.test.mjs
```

No requieren tokens ni redes y no publican contenido.

## Instalación en Cloudflare

1. Crear o abrir cuenta gratuita en https://dash.cloudflare.com/ y comprobar que
   haya al menos un Cron Trigger disponible.
2. En una copia local de este repositorio, con Node.js instalado, abrir terminal
   en `cloudflare/dr-noticias-trigger`:
   ```bash
   npx wrangler login
   npx wrangler deploy
   ```
   Este despliegue tiene `DR_NOTICIAS_CF_ENABLED = "false"`, y por tanto **no
   dispara GitHub ni publica nada**. No utilizar `wrangler dev` con un token
   real; las pruebas son offline.
3. En GitHub, crear un **fine-grained personal access token** para uso exclusivo
   de Cloudflare: propietario correcto, restringido al repositorio
   `ariellourdes52-collab/dr-accesorios-rd-updates`, permiso de repositorio
   **Actions: Read and write** (Contents no requiere escritura), expiración corta
   y recordatorio para renovar. GitHub podría pedir aprobación del propietario.
   **No escribir el token en README, archivos, chat, issues ni logs.**
4. En **Cloudflare → Workers & Pages → dr-noticias-tech-trigger → Settings →
   Variables and Secrets → Add**, añadir **Secret** llamado
   `GH_WORKFLOW_DISPATCH_TOKEN`, pegar su valor allí y desplegar el cambio.
5. Confirmar en Cloudflare que el trigger está configurado correctamente y
   que el Worker sigue **desactivado** por la variable.
6. Cuando se apruebe la activación, cambiar únicamente
   `DR_NOTICIAS_CF_ENABLED` de `false` a `true` en el
   `wrangler.toml` **desplegado** y ejecutar nuevamente `npx wrangler deploy`.
   No activar mientras falte el secreto. Si la variable se cambia en Dashboard
   en vez de Wrangler, futuras implementaciones desde este archivo podrían
   volver a dejarla en `false`.
7. Supervisar el primer Cron Trigger en Cloudflare (Logs/Observability), que
   debe informar `GitHub workflow_dispatch aceptado para main (204)`. Luego
   comprobar GitHub Actions y los ledgers. **La respuesta 204 solo confirma
   el despacho**, no garantiza que Meta haya publicado: revisar cada job.
8. Desactivar rápido sin perder cron/credenciales: restaurar
   `DR_NOTICIAS_CF_ENABLED = "false"` y desplegar. No borrar secretos si
   se piensa reactivar.

## Límites y costos

La documentación de Workers Free (octubre 2026) indica 100,000
solicitudes/día, CPU 10 ms por invocación y cinco Cron Triggers por cuenta.
Este esquema usa **~14 invocaciones/día** (unas 420 al mes) y una subpetición
a GitHub por invocación habilitada. No crea ninguna carga de Railway ni otro
proyecto. Cloudflare y GitHub pueden retrasar o perder ejecuciones; el cron de
GitHub original puede servir de respaldo.

Ver: https://developers.cloudflare.com/workers/platform/limits/
Cron: https://developers.cloudflare.com/workers/configuration/cron-triggers/
Secrets: https://developers.cloudflare.com/workers/configuration/secrets/

## Seguridad y restricciones

- El Worker NO necesita ni recibe tokens de Meta, Firebase o Railway.
- `GH_WORKFLOW_DISPATCH_TOKEN` se guarda exclusivamente como Secret Cloudflare;
  restringirlo a un único repositorio y a Actions write.
- Fallos del API GitHub no se reintentan automáticamente: los logs solo
  incluyen código HTTP, nunca cabeceras ni cuerpo del error.
- No modificar DR Radar, DR Audio, DR Agenda, Android o Blogger.
- Los cron existentes permanecen hasta verificar la cadena completa y revisar
  el costo de minutos GitHub Actions.
