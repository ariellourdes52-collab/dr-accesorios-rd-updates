/**
 * DR Noticias Tech — Cloudflare Workers Free Cron Trigger.
 *
 * Unico trabajo: pedir a GitHub Actions que ejecute los publicadores existentes.
 * NO publica en Meta, NO usa tokens de Instagram/FB/Threads, NO toca Blogger.
 * Desactivado por defecto hasta verificación de cuenta y secreto.
 *
 * Las 3 redes tienen locks, cupos diarios y ledger propios en GitHub.
 */
const CRON = "8 13-23,0-2 * * *";
const API = "https://api.github.com/repos/ariellourdes52-collab/dr-accesorios-rd-updates/actions/workflows/dr-noticias-railway-dispatch.yml/dispatches";

function allowedScheduledTime(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const hour = date.getUTCHours();
  const minute = date.getUTCMinutes();
  return minute === 8 && (hour >= 13 || hour <= 2);
}

export async function runScheduled(event, env, request = fetch) {
  if (!event || event.cron !== CRON || !allowedScheduledTime(event.scheduledTime)) {
    console.log("DR Noticias Tech: invocación fuera del calendario; sin cambios.");
    return "outside_schedule";
  }
  if (env?.DR_NOTICIAS_CF_ENABLED !== "true") {
    console.log("DR Noticias Tech: Cloudflare desactivado; sin despacho.");
    return "disabled";
  }
  const token = env?.GH_WORKFLOW_DISPATCH_TOKEN;
  if (typeof token !== "string" || token.trim().length < 20) {
    throw new Error("DR Noticias Tech: falta secreto GH_WORKFLOW_DISPATCH_TOKEN");
  }
  // NO incluir token en URL, body ni logs. NO reintentar respuesta incierta.
  let response;
  try {
    response = await request(API, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token.trim(),
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "DRNoticiasTechCloudflareCron/1.0",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ ref: "main" }),
      redirect: "error",
      signal: AbortSignal.timeout(15000)
    });
  } catch {
    throw new Error("DR Noticias Tech: fallo de red al activar GitHub; no reintentar automáticamente.");
  }
  if (response?.status !== 204) {
    // No leer body: puede contener detalles del servidor o de la autorización.
    const status = Number(response?.status || 0);
    throw new Error("DR Noticias Tech: GitHub workflow_dispatch devolvió HTTP " + status);
  }
  console.log("DR Noticias Tech: GitHub workflow_dispatch aceptado para main (204).");
  return "dispatched";
}

export default {
  async scheduled(controller, env) {
    await runScheduled(controller, env);
  }
};
