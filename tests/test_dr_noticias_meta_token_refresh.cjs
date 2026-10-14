'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const mod = require('../scripts/dr_noticias_meta_token_refresh.cjs');
const repo = mod.REPOSITORY;
const A = 'AAA-' + 'x'.repeat(30);
const B = 'BBB-' + 'y'.repeat(30);
const C = 'CCC-' + 'z'.repeat(30);
const D = 'DDD-' + 'w'.repeat(30);
const env = {GITHUB_REPOSITORY:repo,GITHUB_REF:'refs/heads/main',GITHUB_EVENT_NAME:'schedule',
  DR_NOTICIAS_IG_ACCESS_TOKEN:A,DR_NOTICIAS_THREADS_ACCESS_TOKEN:B,
  GH_SECRETS_UPDATE_TOKEN:'pat_'+'z'.repeat(30),DR_NOTICIAS_TOKEN_REFRESH_ENABLED:'true'};
const json = obj => ({ok:true, status:200, json: async()=>obj});
function requester(url, opts) {
  const u = new URL(url);
  const instagram = u.hostname === 'graph.instagram.com';
  if (u.pathname.includes('refresh_access_token')) {
    return Promise.resolve(json({access_token:instagram?C:D,expires_in:5184000}));
  }
  assert.match(opts.headers.Authorization,/^Bearer /);
  return Promise.resolve(json({id:instagram?mod.CONFIG.instagram.id:mod.CONFIG.threads.id,
    username:'drnoticiastech', ...(instagram?{account_type:'BUSINESS'}:{})}));
}
test('solo acepta cuentas y secretos esperados', async()=>{
  assert.equal(Object.keys(mod.CONFIG).length,2);
  assert.throws(()=>mod.storeSecret('DR_NOTICIAS_FB_PAGE_TOKEN',A,env,()=>({status:0})),/no permitido/);
  await assert.rejects(mod.verifyIdentity(mod.CONFIG.threads,B,async()=>json({id:'bad',username:'drnoticiastech'})),/Identidad/);
});
test('modo audit: verifica sin refresh ni cambios de secretos',async()=>{
  let calls=[]; await mod.runMain('audit',env,async(u,o)=>{calls.push(u);return requester(u,o)},
    ()=>{throw Error('no debe ejecutar gh')},()=>{});
  assert.equal(calls.length,2);
  assert.ok(calls.every(u=>!u.includes('refresh_access_token')));
});
test('renovación semanal: ambas identidades antes/después y solo dos secretos',async()=>{
  const calls=[]; const gh=[];
  await mod.runMain('refresh',env,async(u,o)=>{calls.push(u);return requester(u,o)},
    (cmd,args,opts)=>{gh.push({cmd,args,input:opts.input});return {status:0}},()=>{});
  assert.equal(calls.length,6);
  assert.equal(gh.length,3);
  assert.deepEqual(gh.slice(1).map(v=>v.args[2]), ['DR_NOTICIAS_IG_ACCESS_TOKEN','DR_NOTICIAS_THREADS_ACCESS_TOKEN']);
  assert.equal(gh[1].input,C+'\n');assert.equal(gh[2].input,D+'\n');
});
test('no hace cambios cuando la renovación está deshabilitada',async()=>{
  let gh=0;await assert.rejects(mod.runMain('refresh', {...env,DR_NOTICIAS_TOKEN_REFRESH_ENABLED:'false'},requester,
    ()=>{gh++;return {status:0}},()=>{}),/deshabilitada/);assert.equal(gh,0);
});
test('si GitHub no puede guardar, no se llama refresh Meta',async()=>{
  let count=0;await assert.rejects(mod.runMain('refresh',env,async(u,o)=>{
    if(u.includes('refresh_access_token')) count++;return requester(u,o);
  },()=>({status:1}),()=>{}),/GitHub/);assert.equal(count,0);
});
test('no sustituye token si cuenta nueva es otra',async()=>{
  let gh=[];let mismatch=false;
  await assert.rejects(mod.runMain('refresh',env,async(u,o)=>{
    if(u.includes('refresh_access_token')) mismatch=true;
    if (mismatch && u.includes('/me?') && o.headers.Authorization.includes(C))
      return json({id:'0000',username:'mal',account_type:'BUSINESS'});
    return requester(u,o);
  },(cmd,args,opts)=>{gh.push(args);return {status:0}},()=>{}),/renovaciones/);
  assert.equal(gh.filter(a=>a.includes('DR_NOTICIAS_IG_ACCESS_TOKEN')).length,0);
});
test('rechaza respuesta con caducidad corta antes de guardar',async()=>{
  await assert.rejects(mod.refreshToken(mod.CONFIG.instagram,A,async()=>json({access_token:C,expires_in:3600})),/vigencia larga/);
});
