'use strict';
/**
 * DR Noticias Tech — Threads API: comprobación segura y únicamente de lectura.
 * GET /me?fields=id,username sobre graph.threads.net (NO Facebook ni Instagram).
 * Solo se imprime identidad de cuenta y estado; jamás el access token.
 */
const EXPECTED_USERNAME = 'drnoticiastech';
const API_URL = 'https://graph.threads.net/v1.0/me';
const TOKEN_ENV = 'DR_NOTICIAS_THREADS_ACCESS_TOKEN';

function validateThreadsProfile(profile) {
  if (!profile || typeof profile !== 'object' || Array.isArray(profile)) {
    throw new Error('Respuesta de Threads no válida.');
  }
  const id = String(profile.id || '').trim();
  const username = String(profile.username || '').trim().replace(/^@/, '').toLowerCase();
  if (!/^\d{5,30}$/.test(id)) {
    throw new Error('Threads no devolvió un ID de usuario válido.');
  }
  if (username !== EXPECTED_USERNAME) {
    throw new Error('El token NO corresponde a @drnoticiastech. Detenerse sin publicar.');
  }
  return {id, username};
}

async function checkThreadsToken(token, request = fetch) {
  if (typeof token !== 'string' || token.trim().length < 20) {
    throw new Error('El secreto DR_NOTICIAS_THREADS_ACCESS_TOKEN falta o está vacío.');
  }
  const url = new URL(API_URL);
  url.searchParams.set('fields', 'id,username');
  let response;
  try {
    response = await request(url.toString(), {
      method: 'GET',
      redirect: 'error',
      headers: {
        Authorization: 'Bearer ' + token.trim(),
        Accept: 'application/json',
        'User-Agent': 'DRNoticiasThreadsPreflight/1.0'
      },
      signal: AbortSignal.timeout(16000)
    });
  } catch {
    // Jamás interpolar el objeto Error: puede exponer cabeceras de autorización.
    throw new Error('Threads API no respondió. Verifica conexión, tipo y validez del token.');
  }
  if (!response || !response.ok) {
    const code = Number(response?.status || 0);
    throw new Error('Threads API respondió HTTP ' + code + '. Revisa token y permisos; sin publicaciones.');
  }
  let json;
  try {
    json = await response.json();
  } catch {
    throw new Error('Threads devolvió JSON inválido.');
  }
  return validateThreadsProfile(json);
}

async function main() {
  console.log('DR NOTICIAS TECH — THREADS PREFLIGHT — SOLO LECTURA');
  console.log('Cuenta prevista: @' + EXPECTED_USERNAME);
  const account = await checkThreadsToken(process.env[TOKEN_ENV]);
  console.log('TOKEN DE THREADS VALIDADO');
  console.log('Username: @' + account.username);
  console.log('Threads User ID: ' + account.id);
  console.log('RESULTADO: 0 publicaciones, 0 cambios en Facebook, Instagram o Blogger.');
  console.log('NOTA: la consulta solo verifica identidad/acceso al perfil. NO demuestra permiso de publicación ni vigencia de largo plazo.');
}

if (require.main === module) {
  main().catch(error => {
    console.error('VALIDACIÓN FALLIDA: ' + String(error?.message || 'Error desconocido').slice(0, 180));
    process.exitCode = 1;
  });
}
module.exports = {EXPECTED_USERNAME, API_URL, validateThreadsProfile, checkThreadsToken};
