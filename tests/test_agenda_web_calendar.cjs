const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('agenda_site/agenda/app.js','utf8');
assert(!source.includes('downloadCalendar('));
assert(!source.includes('a.download='));
assert(source.includes("actionLink('📅 Abrir calendario',calendarUrl)"));
const match=source.match(/  const utcICS = .*?\n  function actionLink\(/s);
assert(match,'calendarFormUrl helper not found');
const funcs=match[0].replace(/\n  function actionLink\($/,'');
const ctx={URLSearchParams,URL,Date,encodeURIComponent,CONFIG:{timeZone:'America/Santo_Domingo'}};
vm.createContext(ctx);
const get=vm.runInContext(funcs+'\ncalendarFormUrl',ctx);
const event={
 id:'evt-20261011-f63104291e81',title:'Presentación de Dr Agenda',
 description:'Prueba técnica',startAt:'2026-10-16T19:00:00-04:00',
 endAt:'2026-10-16T19:30:00-04:00',location:{type:'online'}
};
const calendar=new URL(get(event));
assert.equal(calendar.origin,'https://calendar.google.com');
assert.equal(calendar.searchParams.get('action'),'TEMPLATE');
assert.equal(calendar.searchParams.get('text'),event.title);
assert.equal(calendar.searchParams.get('dates'),'20261016T230000Z/20261016T233000Z');
assert.equal(calendar.searchParams.get('stz'),'America/Santo_Domingo');
assert.equal(calendar.searchParams.get('etz'),'America/Santo_Domingo');
assert.equal(calendar.searchParams.get('location'),'En línea');
assert.match(calendar.searchParams.get('details'),/f63104291e81/);
const fallback=new URL(get({...event,endAt:null}));
assert.equal(fallback.searchParams.get('dates'),'20261016T230000Z/20261017T000000Z');
assert.equal(get({...event,startAt:'invalid'}),null);
const place=new URL(get({...event,location:{name:'Auditorio',address:'Calle 3',city:'Santo Domingo'}}));
assert.equal(place.searchParams.get('location'),'Auditorio, Calle 3, Santo Domingo');
console.log('PASS Agenda web calendar: event form and UTC-4 correct, no download');
