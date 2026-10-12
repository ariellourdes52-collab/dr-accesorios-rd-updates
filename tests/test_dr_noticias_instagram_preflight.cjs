const assert = require('node:assert/strict');
const {test} = require('node:test');
const {coverUrl,coverFromHTML,textOnly,article}=require('../scripts/dr_noticias_instagram_preflight.cjs');
const NOW=Date.parse('2026-10-12T20:00:00.000Z');
const sample={
  id:{$t:'tag:blogger.com,1999:blog-123.post-456'},
  title:{$t:'Apple lanza nueva selección de juegos'},
  published:{$t:'2026-10-12T13:00:00-04:00'},
  link:[{rel:'alternate',type:'text/html',href:'https://draccesoriosrd.blogspot.com/2026/10/apple-juegos-iphone.html'}],
  content:{$t:'<div><img alt="Portada" src="https://blogger.googleusercontent.com/img/b/sample/s1600/portada.jpg"><p>Noticias de tecnología y gaming.</p></div>'}
};
test('solo aceptar imagenes alojadas en Blogger y HTTPS',()=>{
  assert(coverUrl('https://blogger.googleusercontent.com/img/b/img.jpg'));
  assert.equal(coverUrl('http://blogger.googleusercontent.com/a.jpg'),null);
  assert.equal(coverUrl('https://evil.example.net/a.jpg'),null);
  assert.equal(coverUrl('https://blogger.googleusercontent.com.evil.invalid/'),null);
});
test('recupera la primera imagen de portada sin alterar el HTML',()=>{
  assert.match(coverFromHTML(sample.content.$t),/portada.jpg$/);
  assert.equal(coverFromHTML('<p>Solo texto</p>'),null);
});
test('extrae noticias recientes, caption y enlace de bio sin inventar URLs',()=>{
  const post=article(sample,NOW);
  assert.equal(post.title,sample.title.$t);
  assert.match(post.caption,/#DRNoticiasTech/);
  assert.match(post.caption,/enlace de nuestra biografía/);
  assert.equal(post.url,sample.link[0].href);
});
test('rechaza entradas viejas, futuras, dominios ajenos o fechas invalidas',()=>{
  assert.equal(article({...sample,published:{$t:'2026-01-01T00:00:00Z'}},NOW),null);
  assert.equal(article({...sample,published:{$t:'2026-12-01T00:00:00Z'}},NOW),null);
  assert.equal(article({...sample,published:{$t:'mal'}},NOW),null);
  assert.equal(article({...sample,link:[{rel:'alternate',type:'text/html',href:'https://evil.example.net/x'}]},NOW),null);
});
test('limpia etiquetas y scripts',()=>{
  assert.equal(textOnly('<script>alert("a")</script><p>Hola &amp; adiós</p>'),'Hola & adiós');
});
