/* DR_RADAR_WEBPUSH_SW_V2 */
self.addEventListener('install', function() {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', function(event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_) {
    data = {title:'DR Radar', body:event.data ? event.data.text() : 'Nueva alerta activa.'};
  }

  var title = data.title || 'DR Radar';
  var options = {
    body: data.body || 'Hay una nueva alerta activa en DR Radar.',
    tag: data.tag || 'dr-radar-alert',
    renotify: false,
    data: {url: data.url || 'https://draccesoriosrd.blogspot.com/'}
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || 'https://draccesoriosrd.blogspot.com/';
  event.waitUntil(
    self.clients.matchAll({type:'window', includeUncontrolled:true}).then(function(list) {
      for (var i=0; i<list.length; i++) {
        var client=list[i];
        if ('focus' in client) {
          if ('navigate' in client) client.navigate(url);
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
