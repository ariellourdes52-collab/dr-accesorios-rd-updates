/* DR_RADAR_WEBPUSH_SW_V3: Radar, posts, social and DR Agenda iOS */
self.addEventListener('install', function() { self.skipWaiting(); });
self.addEventListener('activate', function(event) { event.waitUntil(self.clients.claim()); });

function drAgendaEventId(url) {
  try {
    var value = new URL(url).searchParams.get('evento') || '';
    return /^evt-[a-z0-9][a-z0-9-]{1,42}$/.test(value) ? value : null;
  } catch (_) { return null; }
}

function drAgendaStoreNotification(data, eventId) {
  return new Promise(function(resolve) {
    try {
      var req = indexedDB.open('dr-agenda-push-v1', 1);
      req.onupgradeneeded = function() {
        if (!req.result.objectStoreNames.contains('events')) {
          req.result.createObjectStore('events', {keyPath:'id'});
        }
      };
      req.onerror = function() { resolve(); };
      req.onsuccess = function() {
        var db = req.result;
        var tx = db.transaction('events', 'readwrite');
        var store = tx.objectStore('events');
        var get = store.get(eventId);
        get.onsuccess = function() {
          store.put({
            id:eventId,
            title:String(data.body || 'Evento DR Agenda').slice(0,200),
            receivedAt:Date.now(),
            saved:Boolean(get.result && get.result.saved)
          });
        };
        tx.oncomplete = function() { db.close(); resolve(); };
        tx.onerror = function() { db.close(); resolve(); };
      };
    } catch (_) { resolve(); }
  });
}

self.addEventListener('push', function(event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_) {
    data = {title:'DR Radar', body:event.data ? event.data.text() : 'Nueva alerta activa.'};
  }
  if (!data || typeof data !== 'object') data = {};
  var agenda = data.category === 'agenda';
  var id = agenda ? drAgendaEventId(data.url || '') : null;
  var agendaUrl = id ? '/radar-alertas/agenda.html?evento=' + encodeURIComponent(id)
    : '/radar-alertas/agenda.html';
  var link = agenda ? agendaUrl : (data.url || 'https://draccesoriosrd.blogspot.com/');
  var title = data.title || (agenda ? 'DR Agenda · Nuevo evento' : 'DR Radar');
  var options = {
    body: data.body || (agenda ? 'Nuevo evento disponible en DR Agenda.' : 'Hay una nueva alerta activa en DR Radar.'),
    icon: '/radar-alertas/icon-192.png',
    tag: data.tag || (agenda ? 'dr-agenda-' + (id || 'event') : 'dr-radar-alert'),
    renotify: false,
    data: {url: link, category: data.category || '', eventId: id || ''}
  };
  var visible = self.registration.showNotification(title, options);
  var saved = agenda && id ? drAgendaStoreNotification(data, id) : Promise.resolve();
  event.waitUntil(Promise.all([visible, saved]).then(function() {
    if (agenda && id) {
      return self.clients.matchAll({type:'window', includeUncontrolled:true}).then(function(list) {
        list.forEach(function(client) {
          if ('postMessage' in client) client.postMessage({type:'dr-agenda-push', eventId:id});
        });
      });
    }
  }));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || 'https://draccesoriosrd.blogspot.com/';
  event.waitUntil(
    self.clients.matchAll({type:'window', includeUncontrolled:true}).then(function(list) {
      for (var i=0; i<list.length; i++) {
        var client=list[i];
        if ('focus' in client) {
          if ('navigate' in client) {
            return client.navigate(url).then(function(c) { return (c || client).focus(); })
              .catch(function() { return client.focus(); });
          }
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
