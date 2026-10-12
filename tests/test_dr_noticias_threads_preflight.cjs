'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {validateThreadsProfile, checkThreadsToken, API_URL} =
  require('../scripts/dr_noticias_threads_preflight.cjs');

test('Threads acepta solo el handle oficial y un ID de Threads', () => {
  assert.deepEqual(validateThreadsProfile({id:'29384756283901235', username:'drnoticiastech'}),
    {id:'29384756283901235',username:'drnoticiastech'});
  assert.deepEqual(validateThreadsProfile({id:'29384756283901235',username:'@DRNOTICIASTECH'}),
    {id:'29384756283901235',username:'drnoticiastech'});
});

test('No confunde Instagram o Facebook con Threads', () => {
  assert.throws(()=>validateThreadsProfile({id:'29384756283901235',username:'draccesoriosrd'}),/NO corresponde/);
  assert.throws(()=>validateThreadsProfile({id:'29384756283901235',username:'otra_cuenta'}),/NO corresponde/);
  assert.throws(()=>validateThreadsProfile({id:'',username:'drnoticiastech'}),/ID de usuario/);
  assert.throws(()=>validateThreadsProfile(null),/no válida/);
});

test('Solo GET a graph.threads.net, token en Authorization y nunca en URL', async () => {
  const secret = 'SUPER_SECRET_THREADS_ACCESS_TOKEN_12345678';
  const calls = [];
  const profile = await checkThreadsToken(secret, async (url, opts) => {
    calls.push({url,opts});
    return {ok:true,status:200,json:async()=>({id:'29384756283901235',username:'drnoticiastech'})};
  });
  assert.equal(profile.username,'drnoticiastech');
  assert.equal(calls.length,1);
  assert.equal(new URL(calls[0].url).origin,'https://graph.threads.net');
  assert.equal(new URL(calls[0].url).pathname, '/v1.0/me');
  assert.equal(new URL(calls[0].url).searchParams.get('fields'), 'id,username');
  assert.ok(calls[0].url.startsWith(API_URL));
  assert.ok(!calls[0].url.includes(secret));
  assert.equal(calls[0].opts.method, 'GET');
  assert.equal(calls[0].opts.redirect, 'error');
  assert.equal(calls[0].opts.headers.Authorization, 'Bearer ' + secret);
});

test('No expone tokens al fallar la consulta o la red', async () => {
  const secret = 'DO_NOT_LOG_THIS_TOKEN_987654321234567';
  await assert.rejects(
    () => checkThreadsToken(secret, async () => ({
      ok:false, status:403,
      json:async()=>({error:{message:'TOKEN: '+secret}})
    })),
    err => {
      assert.match(err.message,/HTTP 403/);
      assert.ok(!err.message.includes(secret));
      return true;
    });
  await assert.rejects(
    () => checkThreadsToken(secret, async () => {throw new Error('Authorization: Bearer '+secret)}),
    err => {
      assert.ok(!err.message.includes(secret));
      return true;
    });
});

test('Detecta secreto ausente sin realizar peticiones', async () => {
  await assert.rejects(
    () => checkThreadsToken('',async()=>{throw new Error('No debe llamar API')}),
    /secreto DR_NOTICIAS_THREADS_ACCESS_TOKEN/);
});
