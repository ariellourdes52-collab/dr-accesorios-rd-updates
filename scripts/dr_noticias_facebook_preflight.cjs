'use strict';
/**
 * DR Noticias Tech — diagnostico read-only del Page Access Token.
 * NUNCA publica, modifica permisos o expone el token.
 * No tiene dependencias, jobs programados ni APIs de escritura.
 */
const PAGE_ID = '875159429020764';
const PAGE_NAME_MARKER = 'dr noticias tech';
const GRAPH_BASE = 'https://graph.facebook.com/v26.0/me';
const TOKEN_ENV = 'DR_NOTICIAS_FB_PAGE_TOKEN';

function validatePage(data) {
  if (!data || typeof data !== 'object') {
    throw new Error('Respuesta JSON de Meta inesperada');
  }
  if (String(data.id || '') !== PAGE_ID) {
    throw new Error('Este secreto NO es el Page Access Token de DR Noticias Tech; detenerse. Puede ser un token de usuario o de otra página.');
  }
  const name = String(data.name || '').trim();
  if (!name.toLocaleLowerCase('es').includes(PAGE_NAME_MARKER)) {
    throw new Error('Nombre de la página no coincide con DR Noticias Tech. No continuar.');
  }
  return { id: PAGE_ID, name: name.slice(0, 120) };
}

async function verifyPageToken(token, request = fetch) {
  if (typeof token !== 'string' || token.trim().length < 20) {
    throw new Error('Falta el secreto DR_NOTICIAS_FB_PAGE_TOKEN o está vacío.');
  }
  const url = new URL(GRAPH_BASE);
  url.searchParams.set('fields', 'id,name');
  let response;
  try {
    response = await request(url.toString(), {
      method: 'GET',
      redirect: 'error',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/json',
        'User-Agent': 'DRNoticiasTechPagePreflight/1.0'
      },
      signal: AbortSignal.timeout(15000)
    });
  } catch (error) {
    // No mostrar el objeto Error, que puede incluir detalles de transporte.
    throw new Error('No se pudo contactar a la API de Facebook con el token; consulta fallida.');
  }
  if (!response || !response.ok) {
    const status = Number(response?.status || 0);
    throw new Error('Facebook Graph API devolvió HTTP ' + status + '. Revisar expiración, tipo de token o permisos. No se ha publicado.');
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error('Facebook devolvió una respuesta que no es JSON válido.');
  }
  return validatePage(data);
}

async function main() {
  console.log('DR NOTICIAS TECH — FACEBOOK PREFLIGHT — SOLO LECTURA');
  console.log('Página esperada: ' + PAGE_ID);
  const page = await verifyPageToken(process.env[TOKEN_ENV]);
  console.log('TOKEN DE PÁGINA VALIDADO');
  console.log('ID de página: ' + page.id);
  console.log('Nombre: ' + page.name);
  console.log('FINALIZADO: 0 publicaciones, 0 cambios en Meta, Blogger o Instagram.');
  console.log('NOTA: este control no determina la fecha de caducidad del token.');
}

if (require.main === module) {
  main().catch(error => {
    console.error('VALIDACIÓN FALLIDA: ' + String(error?.message || 'Error inesperado').slice(0, 240));
    process.exitCode = 1;
  });
}
module.exports = { PAGE_ID, PAGE_NAME_MARKER, validatePage, verifyPageToken };
