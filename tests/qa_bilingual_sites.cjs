'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),cp=require('node:child_process'),vm=require('node:vm');
const paths=[
  'landing/radar-alertas/index.html',
  'landing/radar-alertas/agenda.html',
  'agenda_site/agenda/index.html'
];
for(const path of paths){
  const original=cp.execFileSync('git',['show','origin/main:'+path],{encoding:'utf8'});
  const changed=fs.readFileSync(path,'utf8');
  const cut=original.lastIndexOf('</body>');
  assert.ok(cut>0,path+': missing original closing tag');
  assert.equal(changed.slice(0,cut),original.slice(0,cut),path+': changed original page logic');
  assert.ok(changed.endsWith(original.slice(cut)),path+': original footer changed');
  const addition=changed.slice(cut,changed.length-original.slice(cut).length);
  assert.ok(addition.includes('DR_SITE_BILINGUAL_UI_QA_V1'),path+': missing QA marker');
  assert.equal((addition.match(/<script>/g)||[]).length,1,path+': unexpected number of added scripts');
  assert.ok(addition.includes("new MutationObserver(schedule).observe(document.body"),path+': no UI observer');
  assert.ok(addition.includes("requestAnimationFrame("),path+': missing observer throttling');
  assert.ok(addition.includes("r.target.nodeValue!==lastWrite.get(r.target)"),path+': missing own-mutation guard');
  assert.ok(addition.includes("e.source!==wrapper.contentWindow"),path+': missing iframe source verification');
  assert.ok(addition.includes("e.origin!==childOrigin"),path+': missing child origin verification');
  assert.ok(addition.includes("window.parent.postMessage"),path+': missing bidirectional language update');
  assert.ok(addition.includes("b.blur();folded(false);select("),path+': selector focus blocks collapse');
  assert.ok(addition.includes("Object.prototype.hasOwnProperty.call(tr,text)"),path+': old-browser compatibility missing');
  assert.ok(!/\\b(?:fetch|XMLHttpRequest|sendBeacon|eval)\\s*\\(/.test(addition),path+': unexpected remote call or dynamic code');
  assert.ok(addition.includes("e.origin!=='https://dr-accesorios-rd.web.app'"),path+': origin must be verified');
  assert.ok(addition.includes("lang:choice"),path+': raw choice must sync across the iframe');
  assert.ok(addition.includes(".event-title,.event-description"),path+': user events must be excluded');
  const inner=addition.match(/<script>([\s\S]*?)<\/script>/);
  assert.ok(inner,path+': missing standalone JS');
  new vm.Script(inner[1],{filename:path+':bilingual-ui'});
  console.log('PASS UI script parsed; original HTML unchanged:',path);
}
for(const path of [
  'landing/radar-alertas/sw.js',
  'agenda_site/agenda/app.js',
  'agenda_site/agenda/events.json',
  'scripts/deploy_radar_widget.py'
]){
  assert.equal(fs.readFileSync(path,'utf8'),
    cp.execFileSync('git',['show','origin/main:'+path],{encoding:'utf8'}),
    path+': critical service was modified');
}
console.log('PASS critical service code, events JSON and service worker unchanged.');
