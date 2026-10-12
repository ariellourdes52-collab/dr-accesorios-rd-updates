'use strict';
/* DR Noticias Tech - Solo lectura. NUNCA publica ni cambia assets. */
const EXPECTED='drnoticiastech';
const FEED='https://draccesoriosrd.blogspot.com/feeds/posts/default?alt=json&max-results=12';
const GRAPH='https://graph.instagram.com/v26.0/me?fields=id,username,account_type';
const MAX_AGE=72*60*60*1000;
function coverUrl(raw) {
  try { const u=new URL(raw);
    return u.protocol==='https:' && !u.username && !u.password && !u.port &&
      ['blogger.googleusercontent.com','lh3.googleusercontent.com'].includes(u.hostname.toLowerCase()) ? u.href : null;
  } catch {return null;}
}
function coverFromHTML(raw) {
  const images=String(raw||'').match(/<img\b[^>]*>/gi)||[];
  for (const img of images) {
    const match=img.match(/\bsrc\s*=\s*(["'])(.*?)\1/i);
    if (match) {const good=coverUrl(match[2].replace(/&amp;/g,'&'));if(good)return good;}
  }
  return null;
}
function textOnly(html) {
  return String(html||'').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,' ')
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi,' ')
    .replace(/<[^>]+>/g,' ').replace(/&nbsp;/gi,' ')
    .replace(/&amp;/gi,'&').replace(/&quot;/gi,'"')
    .replace(/&#39;/g,"'").replace(/\s+/g,' ').trim();
}
function article(raw,now=Date.now()) {
  const id=String(raw?.id?.$t||'');
  const title=String(raw?.title?.$t||'').trim();
  const date=Date.parse(raw?.published?.$t||'');
  const url=(raw?.link||[]).find(x=>x.rel==='alternate'&&x.type?.includes('html'))?.href;
  if(!id||!title||!Number.isFinite(date)||date>now+600000||now-date>MAX_AGE) return null;
  if(!url||!/^https:\/\/draccesoriosrd\.blogspot\.com\/20\d{2}\/\d{2}\//.test(url)) return null;
  const html=raw?.content?.$t||'';
  const desc=textOnly(html).slice(0,220).replace(/\s+\S*$/,'');
  const caption=[title,desc,'Lee la noticia completa en el enlace de nuestra biografía.',
    '#Tecnologia #NoticiasTech #RepublicaDominicana #DRAccesoriosRD #DRNoticiasTech'].filter(Boolean).join('\n\n').slice(0,2200);
  return {id,title,url,publishedAt:new Date(date).toISOString(),image:coverFromHTML(html),caption};
}
async function json(url,headers) {
  const r=await fetch(url,{headers,redirect:'error',signal:AbortSignal.timeout(20000)});
  if(!r.ok)throw Error('HTTP '+r.status);
  const raw=await r.text();
  if(raw.length>1200000)throw Error('Respuesta demasiado grande');
  return JSON.parse(raw);
}
async function verifyAccount(token) {
  if(!token)throw Error('No existe DR_NOTICIAS_IG_ACCESS_TOKEN en los secretos de Actions');
  const profile=await json(GRAPH,{Authorization:'Bearer '+token,Accept:'application/json'});
  const name=String(profile.username||'').toLowerCase().replace(/^@/,'');
  if(name!==EXPECTED||!profile.id)throw Error('Cuenta de Instagram inesperada; no continuar');
  return {name,id:String(profile.id),kind:profile.account_type||'sin dato'};
}
async function checkImage(url) {
  if(!url)return 'sin portada';
  try {
    const r=await fetch(url,{headers:{Accept:'image/jpeg'},signal:AbortSignal.timeout(15000)});
    const type=(r.headers.get('content-type')||'').split(';')[0].toLowerCase();
    await r.body?.cancel();
    return r.ok ? type : 'HTTP '+r.status;
  }catch{return 'no accesible';}
}
async function main() {
  console.log('DR Noticias Tech | AUDITORÍA DE SOLO LECTURA, NO PUBLICAR');
  if(process.env.DR_NOTICIAS_CHECK_ACCOUNT!=='false') {
    const acct=await verifyAccount(process.env.DR_NOTICIAS_IG_ACCESS_TOKEN);
    console.log('Cuenta correcta: @'+acct.name+'; tipo: '+acct.kind);
    console.log('IG ID (no es un secreto): '+acct.id);
  }
  const raw=await json(FEED,{Accept:'application/json','User-Agent':'DRNoticiasTechBot/1.0'});
  if(!Array.isArray(raw?.feed?.entry)) throw Error('El feed de Blogger no devolvió entradas');
  const posts=raw.feed.entry.map(x=>article(x)).filter(Boolean).sort((a,b)=>a.publishedAt.localeCompare(b.publishedAt));
  console.log('Noticias de las últimas 72 horas: '+posts.length);
  for(const p of posts.slice(-8)){
    const imageType=await checkImage(p.image);
    console.log('- '+p.title.slice(0,90)+' | imagen: '+imageType);
  }
  console.log('FINALIZADO: 0 publicaciones, 0 modificaciones en Meta o Blogger.');
}
if(require.main===module)main().catch(e=>{console.error('ERROR: '+String(e?.message||e).slice(0,160));process.exitCode=1;});
module.exports={coverUrl,coverFromHTML,textOnly,article,verifyAccount};
