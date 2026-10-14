'use strict';
/** Solo Instagram/Threads de DR Noticias Tech. No Cloudflare, FB, Blogger ni publicadores. */
const { spawnSync } = require('node:child_process');

const REPOSITORY = 'ariellourdes52-collab/dr-accesorios-rd-updates';
const CONFIG = Object.freeze({
  instagram: Object.freeze({
    secret: 'DR_NOTICIAS_IG_ACCESS_TOKEN',
    id: '28639578679032150',
    username: 'drnoticiastech',
    profile: 'https://graph.instagram.com/v26.0/me?fields=id,username,account_type',
    refresh: 'https://graph.instagram.com/refresh_access_token',
    grant: 'ig_refresh_token',
  }),
  threads: Object.freeze({
    secret: 'DR_NOTICIAS_THREADS_ACCESS_TOKEN',
    id: '28329657306656174',
    username: 'drnoticiastech',
    profile: 'https://graph.threads.net/v1.0/me?fields=id,username',
    refresh: 'https://graph.threads.net/refresh_access_token',
    grant: 'th_refresh_token',
  }),
});

function assertToken(value, label) {
  if (typeof value !== 'string' || value.trim().length < 20 || /\s/.test(value)) {
    throw Error('Token faltante o inválido para ' + label + '. Sin cambios.');
  }
  return value.trim();
}

async function apiJSON(url, options, requester = fetch) {
  let response;
  try {
    response = await requester(url, {
      ...options, redirect: 'error', signal: AbortSignal.timeout(20000),
    });
  } catch (_) {
    throw Error('No se pudo conectar con Meta de forma segura. Sin cambios de secreto.');
  }
  if (!response || !response.ok) {
    throw Error('La API Meta rechazó la operación, HTTP ' + Number(response?.status || 0) + '.');
  }
  try {
    const result = await response.json();
    if (!result || typeof result !== 'object' || Array.isArray(result)) throw Error('json');
    return result;
  } catch (_) {
    throw Error('Meta devolvió una respuesta inesperada. Sin cambios de secreto.');
  }
}

async function verifyIdentity(config, token, requester = fetch) {
  const profile = await apiJSON(config.profile, {
    method: 'GET',
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/json' },
  }, requester);
  if (String(profile.id || '') !== config.id ||
      String(profile.username || '').toLowerCase().replace(/^@/, '') !== config.username ||
      (config.grant === 'ig_refresh_token' &&
       String(profile.account_type || '').toUpperCase() !== 'BUSINESS')) {
    throw Error('Identidad inesperada de @drnoticiastech; no se reemplaza el secreto.');
  }
  return true;
}

async function refreshToken(config, token, requester = fetch) {
  const url = new URL(config.refresh);
  url.searchParams.set('grant_type', config.grant);
  url.searchParams.set('access_token', token);
  // URL requerida por Meta: nunca mostrarla ni registrar excepciones de transporte.
  const data = await apiJSON(url.toString(), { method: 'GET' }, requester);
  const next = assertToken(data.access_token, config.secret);
  const expires = Number(data.expires_in);
  if (!Number.isFinite(expires) || expires < 30 * 86400 || expires > 90 * 86400) {
    throw Error('Meta no confirmó vigencia larga (30 a 90 días); secreto intacto.');
  }
  return { token: next, expiresIn: expires };
}

function githubCLI(args, env, input = '', run = spawnSync) {
  const result = run('gh', args, {
    input, encoding: 'utf8', timeout: 30000,
    env: { ...process.env, GH_TOKEN: env.GH_SECRETS_UPDATE_TOKEN },
    maxBuffer: 300000,
  });
  // No transmitir stdout/stderr del comando: nunca imprimir valores sensibles.
  if (result.error || result.status !== 0) {
    throw Error('GitHub rechazó la operación segura de secretos; revisa permisos.');
  }
}

function precheckGitHub(env, run = spawnSync) {
  assertToken(env.GH_SECRETS_UPDATE_TOKEN, 'GH_SECRETS_UPDATE_TOKEN');
  githubCLI(['api', 'repos/' + REPOSITORY + '/actions/secrets/public-key'], env, '', run);
}

function storeSecret(name, token, env, run = spawnSync) {
  if (!Object.values(CONFIG).some(config => config.secret === name)) {
    throw Error('Secreto no permitido; operación bloqueada.');
  }
  githubCLI(['secret', 'set', name, '--repo', REPOSITORY], env, token + '\n', run);
}

async function runMain(mode, env = process.env, requester = fetch, cli = spawnSync, log = console.log) {
  if (mode !== 'audit' && mode !== 'refresh') throw Error('Modo no permitido.');
  if (env.GITHUB_REPOSITORY !== REPOSITORY) throw Error('Repositorio inesperado; cancelado.');
  const secrets = Object.values(CONFIG).map(config => ({
    config, token: assertToken(env[config.secret], config.secret),
  }));
  // Preflight de ambas identidades. Si una no es correcta, no se renueva ninguna.
  for (const item of secrets) await verifyIdentity(item.config, item.token, requester);
  log('Instagram y Threads: identidad @drnoticiastech verificada.');
  if (mode === 'audit') { log('AUDITORÍA: sin renovación, publicaciones ni cambios.'); return; }
  if (env.GITHUB_REF !== 'refs/heads/main' || env.GITHUB_EVENT_NAME === 'pull_request' ||
      env.DR_NOTICIAS_TOKEN_REFRESH_ENABLED !== 'true') {
    throw Error('Renovación deshabilitada o ejecución no autorizada; 0 cambios.');
  }
  // Antes de solicitar nuevos tokens Meta, comprobar capacidad de persistirlos.
  precheckGitHub(env, cli);
  let failed = 0;
  for (const item of secrets) {
    try {
      const next = await refreshToken(item.config, item.token, requester);
      await verifyIdentity(item.config, next.token, requester);
      storeSecret(item.config.secret, next.token, env, cli);
      log('Renovación y almacenamiento seguro confirmados: ' +
          (item.config.grant === 'ig_refresh_token' ? 'Instagram' : 'Threads') + '.');
    } catch (_) {
      failed++;
      log('ATENCIÓN: falló renovación o almacenamiento de ' +
          (item.config.grant === 'ig_refresh_token' ? 'Instagram' : 'Threads') +
          '. Comprobar estado del secreto en GitHub antes de reintentar.');
    }
  }
  if (failed) throw Error('Una o más renovaciones requieren revisión manual; ningún token se registró en logs.');
}

if (require.main === module) {
  runMain(process.argv[2] || 'audit').catch(() => {
    console.error('ERROR: auditoría/renovación de tokens no completada. Revisar configuración o API de Meta; no divulgar credenciales.');
    process.exitCode = 1;
  });
}
module.exports = { REPOSITORY, CONFIG, assertToken, verifyIdentity, refreshToken,
  precheckGitHub, storeSecret, runMain };
