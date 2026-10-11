'use strict';
(() => {
  const CONFIG = Object.freeze({ url: '/agenda/events.json', timeZone: 'America/Santo_Domingo', refreshMs: 60000 });
  const categories = {tech:'Tecnología', cine:'Cine', gaming:'Gaming', local:'Eventos RD', promocion:'Promociones', lanzamiento:'Lanzamientos', conferencia:'Conferencias', streaming:'Streaming', otros:'Otros'};
  const $ = (selector) => document.querySelector(selector);
  const grid = $('#event-grid'), empty = $('#empty-state'), status = $('#status-message');
  const model = { events:[], category:'all', search:'', loaded:false, error:false, updatedAt:null };
  $('#year').textContent = String(new Date().getFullYear());
  const collator = new Intl.Collator('es');
  const dateFmt = new Intl.DateTimeFormat('es-DO', {timeZone:CONFIG.timeZone,day:'numeric',month:'long',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true});
  const dayFmt = new Intl.DateTimeFormat('es-DO',{timeZone:CONFIG.timeZone,year:'numeric',month:'numeric'});
  const readableDate = (raw) => { const date = new Date(raw); return Number.isNaN(date.getTime()) ? 'Fecha por confirmar' : dateFmt.format(date); };
  const sameRDMonth = (start) => dayFmt.format(new Date(start)) === dayFmt.format(new Date());
  const node = (type, cls, text) => {const n=document.createElement(type); if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  const safeHTTPS = (url) => {try{const u=new URL(url);return u.protocol==='https:' && !u.username && !u.password ? u.href : null;}catch{return null;}};
  const ownImage = (event) => {try{const u=new URL(event.imageUrl,location.origin); return u.origin===location.origin && u.pathname==='/agenda/images/'+event.id+'.webp' && !u.search && !u.hash ? u.href : null;}catch{return null;}};
  const locationName = (event) => {const loc=event.location||{}; if(loc.type==='online'||loc.online)return 'Evento en línea';return [loc.name||loc.address,loc.city].filter(Boolean).join(' · ')||'Lugar por confirmar';};
  const mapsLink = (event) => {const loc=event.location||{};if(loc.type==='online'||loc.online)return null;const query=[loc.name,loc.address,loc.city,loc.province].filter(Boolean).join(', ');if(!query)return null;return 'https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(query);};
  const utcICS = (iso) => {const d=new Date(iso);return Number.isNaN(d.getTime()) ? '' : d.toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'');};
  const icsEscape = (v) => String(v||'').replace(/\\/g,'\\\\').replace(/\r?\n/g,'\\n').replace(/,/g,'\\,').replace(/;/g,'\\;');
  function downloadCalendar(event){
    const start=utcICS(event.startAt),end=utcICS(event.endAt||event.startAt);
    if(!start || !end)return;
    const id=String(event.id).replace(/[^a-zA-Z0-9_-]/g,'').slice(0,95);
    const loc=event.location||{};
    const lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//DR Accesorios RD//DR Agenda//ES','CALSCALE:GREGORIAN','BEGIN:VEVENT','UID:'+id+'@dr-accesorios-rd-agenda.web.app','DTSTAMP:'+utcICS(new Date().toISOString()),'DTSTART:'+start,'DTEND:'+end,'SUMMARY:'+icsEscape(event.title),'DESCRIPTION:'+icsEscape(event.description||''),'LOCATION:'+icsEscape([loc.name,loc.address,loc.city,loc.province].filter(Boolean).join(', ')),'END:VEVENT','END:VCALENDAR'];
    const blob=new Blob([lines.join('\r\n')+'\r\n'],{type:'text/calendar;charset=utf-8'});
    const url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download='dr-agenda-'+id+'.ics';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),4000);
  }
  function actionLink(text,href){const a=node('a','',text);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a;}
  function showDialog(event){
    const dialog=$('#event-dialog');if(typeof dialog.showModal!=='function')return;
    $('#dialog-title').textContent=event.title;$('#dialog-description').textContent=event.description||'';
    $('#dialog-category').textContent=categories[event.category]||'Evento';
    const photo=$('#dialog-photo');photo.style.backgroundImage='';const src=ownImage(event);if(src){photo.style.backgroundImage='url("'+src.replace(/"/g,'')+'")';}
    const meta=$('#dialog-meta');meta.replaceChildren(node('div','', '◷  '+readableDate(event.startAt)),node('div','', '⌖  '+locationName(event)));
    const actions=$('#dialog-actions');actions.replaceChildren();const cal=node('button','','+  Añadir al calendario');cal.type='button';cal.addEventListener('click',()=>downloadCalendar(event));actions.append(cal);
    const maps=mapsLink(event);if(maps)actions.append(actionLink('⌖  Ver mapa',maps));const website=safeHTTPS(event.officialUrl||event.articleUrl);if(website)actions.append(actionLink('↗  Sitio oficial',website));
    dialog.showModal();const u=new URL(location.href);u.searchParams.set('evento',event.id);history.replaceState(null,'',u.pathname+u.search);
  }
  function card(event){const card=node('article','event-card');const cover=node('div','event-cover');const src=ownImage(event);if(src){const img=node('img');img.src=src;img.alt='Imagen del evento '+event.title;img.loading='lazy';img.addEventListener('error',()=>img.replaceWith(node('span','event-cover-placeholder','AGENDA')));cover.append(img);}else cover.append(node('span','event-cover-placeholder','AGENDA'));
    if(event.status==='postponed'){cover.append(node('span','event-badge','POSPUESTO'));}
    card.append(cover);const body=node('div','event-body');body.append(node('span','event-category',categories[event.category]||'Evento'));const title=node('h3','event-title',event.title);body.append(title,node('p','event-date','◷  '+readableDate(event.startAt)),node('p','event-place','⌖  '+locationName(event)));
    const desc=node('p','event-description',event.description||'');body.append(desc);const btn=node('button','event-action','Ver detalles →');btn.type='button';btn.addEventListener('click',()=>showDialog(event));body.append(btn);card.append(body);return card;}
  function shownEvents(){const query=model.search.toLocaleLowerCase('es').trim();return model.events.filter(e=>{
    if(!['confirmed','postponed'].includes(e.status))return false;
    const finish=new Date(e.endAt||e.startAt).getTime();if(!Number.isFinite(finish)||finish<Date.now())return false;
    if(model.category!=='all' && (model.category==='otros' ? !['otros','conferencia','streaming','promocion'].includes(e.category):e.category!==model.category))return false;
    return !query||[e.title,e.description,e.location?.name,e.location?.city].filter(Boolean).join(' ').toLocaleLowerCase('es').includes(query);
  }).sort((a,b)=>new Date(a.startAt)-new Date(b.startAt)||collator.compare(a.title,b.title));}
  function render(){
    const upcoming=model.events.filter(e=>['confirmed','postponed'].includes(e.status)&&new Date(e.endAt||e.startAt).getTime()>=Date.now());
    $('#stat-upcoming').textContent=model.loaded?String(upcoming.length):'—';$('#stat-month').textContent=model.loaded?String(upcoming.filter(e=>sameRDMonth(e.startAt)).length):'—';
    $('#stat-refresh').textContent=model.error?'Sin conexión':model.loaded?'Sincronizado':'Conectando';
    const visible=shownEvents();grid.replaceChildren(...visible.map(card));empty.hidden=visible.length>0;
    if(!visible.length){if(model.error&&!model.loaded){$('#empty-heading').textContent='No pudimos cargar la agenda.';$('#empty-copy').textContent='Revisa tu conexión e inténtalo de nuevo. Los demás servicios de DR Accesorios RD siguen disponibles.';}else if(model.search||model.category!=='all'){$('#empty-heading').textContent='No hay coincidencias.';$('#empty-copy').textContent='Prueba otra categoría o cambia los términos de búsqueda.';}else{$('#empty-heading').textContent='Lo próximo está por llegar.';$('#empty-copy').textContent='Estamos preparando eventos verificados. Cuando publiquemos el siguiente, aparecerá aquí automáticamente.';}}
    status.textContent=model.error?'No se pudo actualizar el catálogo de eventos.':visible.length+' eventos disponibles.';
    const deep=new URLSearchParams(location.search).get('evento');if(deep){const selected=model.events.find(e=>e.id===deep);if(selected&& !$('#event-dialog').open)showDialog(selected);}
  }
  async function update(){try{
    const response=await fetch(CONFIG.url,{cache:'no-store',headers:{Accept:'application/json'}});if(!response.ok)throw Error('HTTP '+response.status);
    const raw=await response.text();if(raw.length>650000)throw Error('Tamaño inesperado');const payload=JSON.parse(raw);
    if(payload.schemaVersion!==1||!Number.isInteger(payload.revision)||!Array.isArray(payload.events)||payload.events.length>250)throw Error('Catálogo incompatible');
    model.events=payload.events.filter(e=>e&&typeof e.id==='string'&&/^[a-zA-Z0-9_-]{3,96}$/.test(e.id)&&typeof e.title==='string'&&e.title.length<200&&typeof e.startAt==='string'&&Number.isFinite(new Date(e.startAt).getTime()));model.updatedAt=payload.updatedAt;model.loaded=true;model.error=false;
  }catch(err){model.error=true;console.warn('DR Agenda: catálogo no disponible',err.name);}render();}
  $('#filters').addEventListener('click',e=>{const btn=e.target.closest('button[data-category]');if(!btn)return;model.category=btn.dataset.category;document.querySelectorAll('.filter').forEach(b=>{b.classList.toggle('selected',b===btn);b.setAttribute('aria-pressed',String(b===btn));});render();});
  $('#search-events').addEventListener('input',e=>{model.search=e.target.value;render();});
  $('#dialog-close').addEventListener('click',()=>$('#event-dialog').close());
  $('#event-dialog').addEventListener('close',()=>{const u=new URL(location.href);u.searchParams.delete('evento');history.replaceState(null,'',u.pathname+u.search);});
  $('#event-dialog').addEventListener('click',e=>{if(e.target===$('#event-dialog'))$('#event-dialog').close();});
  window.addEventListener('focus',update);document.addEventListener('visibilitychange',()=>{if(!document.hidden)update();});setInterval(()=>{if(!document.hidden)update();},CONFIG.refreshMs);update();
})();
