'use strict';
// Browser QA entirely offline: intercepted Firebase domains; no actual sign-in, push or deployment.
const {strict: assert} = require('node:assert');
const fs=require('node:fs'), path=require('node:path');
const {chromium,webkit}=require('playwright');
const webOrigin='https://dr-accesorios-rd.web.app';
const agendaOrigin='https://dr-accesorios-rd-agenda.web.app';
const fixture={
  schemaVersion:1,revision:1,updatedAt:'2034-01-01T00:00:00Z',
  events:[{
    id:'evt-qa-comiccon',title:'ComicCon RD: nombre oficial',
    description:'Descripción original de ComicCon sin traducción automática.',
    category:'tech',status:'confirmed',
    startAt:'2034-11-20T20:00:00.000Z',endAt:'2034-11-20T23:00:00.000Z',
    location:{name:'Centro de Convenciones',city:'Santo Domingo',type:'physical'}
  }]
};
const urls=new Map([
  [webOrigin+'/radar-alertas/index.html',['landing/radar-alertas/index.html','text/html']],
  [webOrigin+'/radar-alertas/agenda.html',['landing/radar-alertas/agenda.html','text/html']],
  [agendaOrigin+'/agenda/',['agenda_site/agenda/index.html','text/html']],
  [agendaOrigin+'/agenda/index.html',['agenda_site/agenda/index.html','text/html']],
  [agendaOrigin+'/agenda/app.js',['agenda_site/agenda/app.js','application/javascript']],
  [agendaOrigin+'/agenda/styles.css',['agenda_site/agenda/styles.css','text/css']]
]);
async function offline(route){
  const requestURL=new URL(route.request().url());
  const u=requestURL.origin+requestURL.pathname;
  if(u===agendaOrigin+'/agenda/events.json'){
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixture)});
  }
  const known=urls.get(u);
  if(known){
    return route.fulfill({status:200,contentType:known[1],body:fs.readFileSync(path.join(process.cwd(),known[0]))});
  }
  // No requests can contact production or external services.
  return route.abort('blockedbyclient');
}
async function waitText(locator,expected){
  await locator.waitFor({state:'visible',timeout:15000});
  await locator.page().waitForFunction(({selector,expected})=>document.querySelector(selector)?.textContent?.includes(expected),{selector:await locator.evaluate(el=>el.id?'#'+el.id:null),expected}).catch(()=>{});
  const t=await locator.textContent();
  assert.ok(t.includes(expected),'Expected "'+expected+'", got: '+t);
}
async function choose(scope,lang){
  const option=scope.locator('#dr-site-language [data-lang="'+lang+'"]');
  // If already selected (possibly persisted from the Web App on the same origin),
  // no click is needed, even if the selector has collapsed.
  if(await option.getAttribute('aria-pressed')==='true')return;
  const overlay=scope.locator('#dr-site-language');
  for(let attempt=0;attempt<3;attempt++){
    if(await overlay.evaluate(el=>el.classList.contains('folded'))){
      await scope.locator('#dr-site-language .toggle').click();
    }
    try{await option.click({timeout:3000});return;}
    catch(error){if(attempt===2)throw error;}
  }
}
async function run(engineName,engine,mobile){
  const browser=await engine.launch({headless:true});
  try{
    const ctx=await browser.newContext({
      locale:'en-US',
      viewport:mobile?{width:390,height:844}:{width:1280,height:820},
      isMobile:mobile,
      hasTouch:mobile,
      serviceWorkers:'block',
      reducedMotion:'reduce'
    });
    const page=await ctx.newPage();
    await page.route('**/*',offline);
    const errorText=[];
    page.on('pageerror',error=>{errorText.push(error.message)});
    await page.goto(webOrigin+'/radar-alertas/index.html',{waitUntil:'domcontentloaded'});
    await choose(page,'en');
    await page.waitForFunction(()=>document.querySelector('.head h1')?.textContent.includes('24/7 Alerts'));
    assert.match(await page.locator('.body .lead').innerText(),/Enable essential/);
    await choose(page,'es');
    await page.waitForFunction(()=>document.querySelector('.head h1')?.textContent.includes('Alertas 24/7'));
    await choose(page,'en');
    await page.waitForFunction(()=>document.querySelector('.head h1')?.textContent.includes('24/7 Alerts'));
    await page.waitForTimeout(4800);
    assert.ok(await page.locator('#dr-site-language').evaluate(e=>e.classList.contains('folded')),'Autocollapse 4.5 seconds');
    await page.locator('#dr-site-language .toggle').click();
    assert.ok(await page.locator('#dr-site-language [data-lang="en"]').isVisible(),'Selector reopens');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'No mobile horizontal overflow Web App');

    await page.goto(webOrigin+'/radar-alertas/agenda.html?evento=evt-qa-comiccon',{waitUntil:'domcontentloaded'});
    const frame=page.frameLocator('#viewer');
    await frame.locator('#dr-site-language [data-lang="en"]').waitFor({timeout:20000});
    await choose(page,'en');
    await page.waitForFunction(()=>document.querySelector('header strong')?.textContent==='DR Events');
    await frame.locator('#search-events').waitFor({timeout:12000});
    await frame.locator('.event-title').first().waitFor({timeout:12000});
    assert.equal(await frame.locator('.event-title').first().innerText(),fixture.events[0].title);
    assert.equal(await frame.locator('.event-description').first().innerText(),fixture.events[0].description);
    await frame.locator('.event-date').first().evaluate(async el=>{for(let i=0;i<50&&!el.textContent.includes('November');i++)await new Promise(r=>setTimeout(r,80));});
    assert.match(await frame.locator('.event-date').first().innerText(),/November 20, 2034, 4:00 PM/);
    if(await frame.locator('#event-dialog').evaluate(el=>el.open))await frame.locator('#dialog-close').click();
    await choose(frame,'es');
    await page.waitForFunction(()=>document.querySelector('header strong')?.textContent==='DR Agenda');
    assert.match(await frame.locator('.event-date').first().innerText(),/noviembre/);
    await choose(frame,'en');
    await page.waitForFunction(()=>document.querySelector('header strong')?.textContent==='DR Events');
    await frame.locator('#search-events').fill('unmatched-query-x');
    await frame.locator('#empty-heading').waitFor({state:'visible',timeout:12000});
    await frame.locator('#empty-heading').waitFor({state:'attached'});
    await frame.locator('#empty-heading').evaluate(async el=>{
      for(let i=0;i<50&&!el.textContent.includes('No matches found.');i++)await new Promise(r=>setTimeout(r,80));
    });
    assert.equal(await frame.locator('#empty-heading').innerText(),'No matches found.');
    await frame.locator('#search-events').fill('ComicCon');
    await frame.locator('.event-title').first().waitFor({timeout:12000});
    await frame.locator('.event-action').first().click();
    assert.equal(await frame.locator('#dialog-title').innerText(),fixture.events[0].title);
    assert.equal(await frame.locator('#dialog-description').innerText(),fixture.events[0].description);
    const catalogUrl=await page.locator('#openExternal').getAttribute('href');
    assert.ok(catalogUrl.includes('evento=evt-qa-comiccon')&&catalogUrl.includes('lang=en'),'Link must carry language and event id');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'No mobile horizontal overflow Agenda wrapper');
    assert.ok(await frame.locator('#event-grid').count(),'Agenda grid still exists');
    assert.ok(await page.locator('#history').count(),'Notification history still exists');
    // Original event-date string is intentionally not overwritten by UI translation layer.
    const dateText=await frame.locator('.event-date').first().innerText();
    assert.match(dateText,/November 20, 2034, 4:00 PM/,'UI date must be localized to English');
    assert.match(await frame.locator('#dialog-meta').innerText(),/November 20, 2034, 4:00 PM/,'Dialog date must match');

    // Verify visual-only localization of two real cards without mutating the catalog.
    const realTitle='ComicCon República Dominicana 2026';
    const englishTitle='ComicCon Dominican Republic 2026';
    const realDescription='📅 Fecha: Sábado 24 y domingo 25 de octubre de 2026\n📍 Lugar: Puerto San Souci, Santo Domingo Este\n\nDos días de cultura geek, videojuegos, cosplay, cómics, anime e invitados especiales. Entrada general por un día desde RD$1,900 según Uepa Tickets; los precios pueden cambiar.';
    const englishDescription='📅 Date: Saturday, October 24 and Sunday, October 25, 2026\n📍 Location: Puerto San Souci, Santo Domingo Este\n\nTwo days of geek culture, video games, cosplay, comics, anime and special guests. One-day general admission starts at RD$1,900 according to Uepa Tickets; prices may change.';
    await frame.locator('#event-grid').evaluate((grid,{realTitle,realDescription})=>{
      const sample=document.createElement('article');sample.id='qa-real-event-card';
      sample.innerHTML='<h3 class="event-title"></h3><p class="event-description"></p><p class="event-place">Puerto San Souci</p>';
      sample.querySelector('.event-title').textContent=realTitle;
      sample.querySelector('.event-description').textContent=realDescription;
      grid.append(sample);
    },{realTitle,realDescription});
    await frame.locator('#qa-real-event-card .event-title').evaluate(async(el)=>{
      for(let i=0;i<50&&el.textContent!=='ComicCon Dominican Republic 2026';i++)await new Promise(r=>setTimeout(r,50));
    });
    assert.equal(await frame.locator('#qa-real-event-card .event-title').innerText(),englishTitle);
    assert.equal(await frame.locator('#qa-real-event-card .event-description').innerText(),englishDescription);
    assert.equal(await frame.locator('#qa-real-event-card .event-place').innerText(),'Puerto San Souci');
    await choose(frame,'es');
    assert.equal(await frame.locator('#qa-real-event-card .event-title').innerText(),realTitle);
    assert.equal(await frame.locator('#qa-real-event-card .event-description').innerText(),realDescription);
    await choose(frame,'en');
    assert.equal(await frame.locator('#qa-real-event-card .event-title').innerText(),englishTitle);
    await frame.locator('#qa-real-event-card').evaluate(el=>el.remove());
    const severe=errorText.filter(x=>/DR_SITE_BILINGUAL|SyntaxError|Maximum call stack|Too much recursion/.test(x));
    assert.equal(severe.length,0,'No serious translation JS error: '+severe.join(' ; '));
    console.log('PASS '+engineName+' '+(mobile?'mobile':'desktop')+' real-browser QA; source event untouched; date='+dateText);
    await ctx.close();
  }finally{await browser.close()}
}
(async()=>{
  await run('Chromium',chromium,false);
  await run('Chromium',chromium,true);
  await run('WebKit',webkit,true);
})().catch(err=>{console.error(err);process.exitCode=1});
