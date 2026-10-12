'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { PAGE_ID, validatePage, verifyPageToken } = require('../scripts/dr_noticias_facebook_preflight.cjs');

test('acepta exclusivamente ID correcto y nombre del fanpage', () => {
  const p = validatePage({id: PAGE_ID, name: 'DR Noticias Tech DR Accesorios RD'});
  assert.equal(p.id, PAGE_ID);
  assert.match(p.name, /^DR Noticias Tech/);
});

test('rechaza tokens de usuario y páginas comerciales incluso con acceso total', () => {
  assert.throws(() => validatePage({id: '12345', name: 'Cuenta personal'}), /NO es el Page Access Token/);
  assert.throws(() => validatePage({id: '12345', name: 'DR Accesorios RD'}), /NO es el Page Access Token/);
  assert.throws(() => validatePage({id: PAGE_ID, name: 'Cuenta distinta'}), /Nombre de la página/);
});

test('realiza GET y nunca coloca token en URL', async () => {
  const token = 'TEST_TOKEN_SUPER_SECRET_1234567890';
  const calls = [];
  const result = await verifyPageToken(token, async (url, options) => {
    calls.push({url,options});
    return {ok:true,status:200,json:async()=>({id:PAGE_ID,name:'DR Noticias Tech DR Accesorios RD'})};
  });
  assert.equal(result.id, PAGE_ID);
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /^https:\/\/graph\.facebook\.com\/v26\.0\/me\?fields=id%2Cname$/);
  assert.equal(calls[0].options.method, 'GET');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer ' + token);
  assert.equal(calls[0].options.redirect,'error');
  assert.ok(!calls[0].url.includes(token));
});

test('no imprime ni devuelve detalles sensibles de errores Graph API', async () => {
  const secret = 'MY_ULTRA_SECRET_ACCESS_TOKEN_123456789';
  await assert.rejects(() => verifyPageToken(secret, async () => ({
    ok:false,status:401,json:async()=>({error:{message:'contains '+secret}})
  })), error => {
    assert.match(error.message, /HTTP 401/);
    assert.ok(!error.message.includes(secret));
    return true;
  });
  await assert.rejects(() => verifyPageToken(secret, async () => {
    throw new Error('transport failure with ' + secret);
  }), error => {
    assert.ok(!error.message.includes(secret));
    return true;
  });
});

test('falla ante token ausente o muy corto', async () => {
  await assert.rejects(()=>verifyPageToken('', async () => {throw new Error('must not call')}),/Falta el secreto/);
  await assert.rejects(()=>verifyPageToken('abc', async () => {throw new Error('must not call')}),/Falta el secreto/);
});
