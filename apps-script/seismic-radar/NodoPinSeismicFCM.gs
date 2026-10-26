/**
 * NodoPin: FCM complementario de alta prioridad para sismos USGS.
 * Añadir como archivo NUEVO al proyecto Apps Script real.
 * Nunca reemplazar sendSeismicRefresh_() ni tocar DR Radar existente.
 *
 * ScriptProperties (NUNCA almacenar claves en el código):
 *  NODOPIN_IMMEDIATE_FCM_ENABLED = false inicialmente
 *  NODOPIN_FCM_SENDER_JSON = JSON completo de la cuenta autorizada
 *    nodopin-dr-fcm-sender@dr-accesorios-rd.iam.gserviceaccount.com
 */
var NODOPIN_SEISMIC_FCM_V1 = Object.freeze({
  endpoint: 'https://fcm.googleapis.com/v1/projects/nexlink-3d017/messages:send',
  topic: 'nodopin_dr_seismic_v1',
  packageName: 'org.nodaviva.mesh',
  email: 'nodopin-dr-fcm-sender@dr-accesorios-rd.iam.gserviceaccount.com',
  sourceProject: 'dr-accesorios-rd',
  tokenUrl: 'https://oauth2.googleapis.com/token',
  scope: 'https://www.googleapis.com/auth/firebase.messaging'
});

function nodopinSeismicBridgeStatus_() {
  var props = PropertiesService.getScriptProperties();
  var enabled = props.getProperty('NODOPIN_IMMEDIATE_FCM_ENABLED') === 'true';
  var raw = props.getProperty('NODOPIN_FCM_SENDER_JSON');
  var credentialsValid = false;
  if (raw) {
    try {
      var c = JSON.parse(raw);
      credentialsValid = c.type === 'service_account' &&
        c.project_id === NODOPIN_SEISMIC_FCM_V1.sourceProject &&
        c.client_email === NODOPIN_SEISMIC_FCM_V1.email &&
        typeof c.private_key === 'string' && c.private_key.indexOf('PRIVATE KEY') >= 0;
    } catch (e) { credentialsValid = false; }
  }
  console.log('NodoPin bridge: enabled=' + enabled + ', credentialsValid=' + credentialsValid);
  return { enabled: enabled, credentialsValid: credentialsValid };
}

function nodopinB64_(v) {
  return Utilities.base64EncodeWebSafe(v).replace(/=+$/g, '');
}

function nodopinAccessToken_() {
  var cache = CacheService.getScriptCache();
  var cached = cache.get('nodopin_direct_fcm_token_v1');
  if (cached) return cached;
  var raw = PropertiesService.getScriptProperties().getProperty('NODOPIN_FCM_SENDER_JSON');
  if (!raw) throw new Error('NodoPin FCM: credential absent');
  var c = JSON.parse(raw);
  if (c.type !== 'service_account' ||
      c.project_id !== NODOPIN_SEISMIC_FCM_V1.sourceProject ||
      c.client_email !== NODOPIN_SEISMIC_FCM_V1.email ||
      typeof c.private_key !== 'string') {
    throw new Error('NodoPin FCM: incorrect credential');
  }
  var now = Math.floor(Date.now() / 1000);
  var h = nodopinB64_(JSON.stringify({alg:'RS256',typ:'JWT'}));
  var p = nodopinB64_(JSON.stringify({
    iss: c.client_email,
    scope: NODOPIN_SEISMIC_FCM_V1.scope,
    aud: NODOPIN_SEISMIC_FCM_V1.tokenUrl,
    iat: now, exp: now + 1800
  }));
  var toSign = h + '.' + p;
  var signature = Utilities.base64EncodeWebSafe(
    Utilities.computeRsaSha256Signature(toSign, c.private_key)).replace(/=+$/g, '');
  var res = UrlFetchApp.fetch(NODOPIN_SEISMIC_FCM_V1.tokenUrl, {
    method: 'post', contentType: 'application/x-www-form-urlencoded',
    payload: 'grant_type=' + encodeURIComponent('urn:ietf:params:oauth:grant-type:jwt-bearer') +
      '&assertion=' + encodeURIComponent(toSign + '.' + signature),
    muteHttpExceptions: true
  });
  if (res.getResponseCode() !== 200) throw new Error('NodoPin FCM: OAuth HTTP ' + res.getResponseCode());
  var obj = JSON.parse(res.getContentText());
  if (typeof obj.access_token !== 'string' || !obj.access_token) {
    throw new Error('NodoPin FCM: missing access token');
  }
  cache.put('nodopin_direct_fcm_token_v1', obj.access_token,
    Math.min(1500, Math.max(1, Number(obj.expires_in || 1800) - 120)));
  return obj.access_token;
}

/**
 * Invocar únicamente tras publicar el feed sísmico y enviar el FCM DR original:
 * try { nodopinSeismicNewEvents_(newEvents); }
 * catch (e) { console.warn('NodoPin FCM complementario falló; DR Radar intacto.'); }
 *
 * newEvents = colección de eventos USGS NUEVOS ya filtrados por la lógica actual,
 * cada uno con .feature.id y .feature.properties. No pasar todo el historial.
 */
function nodopinSeismicNewEvents_(newEvents) {
  if (PropertiesService.getScriptProperties().getProperty('NODOPIN_IMMEDIATE_FCM_ENABLED') !== 'true') {
    return {active: false, accepted: 0};
  }
  if (!Array.isArray(newEvents)) throw new Error('NodoPin FCM: expected newEvents array');
  var now = Date.now(), cache = CacheService.getScriptCache(), ids = [];
  newEvents.slice(0, 8).forEach(function(entry) {
    var feature = entry && entry.feature;
    var id = feature && String(feature.id || '').trim();
    var properties = feature && feature.properties || {};
    if (!id || !/^[A-Za-z0-9_.:-]{1,94}$/.test(id)) return;
    var place = String(properties.place || '').toLowerCase();
    if (place.indexOf('dominican republic') < 0 &&
        place.indexOf('república dominicana') < 0 &&
        place.indexOf('republica dominicana') < 0) return;
    var age = now - Number(properties.time);
    if (!isFinite(age) || age < -300000 || age > 2400000) return;
    var key = 'earthquake-usgs-' + id;
    if (!cache.get('sent_' + key)) ids.push(key);
  });
  if (!ids.length) return {active: true, accepted: 0};
  var bearer = nodopinAccessToken_(), sent = 0;
  ids.forEach(function(id) {
    var payload = {message: {
      topic: NODOPIN_SEISMIC_FCM_V1.topic,
      data: {kind:'nodopin_dr_public_v1', category:'seismic', id:id},
      android: {priority:'high', ttl:'300s',
        restricted_package_name:NODOPIN_SEISMIC_FCM_V1.packageName}
    }};
    var response = UrlFetchApp.fetch(NODOPIN_SEISMIC_FCM_V1.endpoint, {
      method:'post', contentType:'application/json',
      headers:{Authorization:'Bearer ' + bearer},
      payload:JSON.stringify(payload), muteHttpExceptions:true
    });
    if (response.getResponseCode() !== 200) {
      console.warn('NodoPin seismic FCM HTTP ' + response.getResponseCode());
      return;
    }
    cache.put('sent_' + id, '1', 21600);
    sent++;
  });
  console.log('NodoPin seismic FCM accepted=' + sent);
  return {active:true, accepted:sent};
}


/**
 * Diagnóstico de autenticación SIN enviar notificaciones y SIN imprimir tokens.
 * Ejecutar desde el menú de funciones de Apps Script tras configurar la
 * propiedad privada NODOPIN_FCM_SENDER_JSON.
 */
function nodopinSeismicCheckAuth_() {
  var status = nodopinSeismicBridgeStatus_();
  if (!status.credentialsValid) {
    throw new Error('NodoPin: NODOPIN_FCM_SENDER_JSON no es una cuenta de servicio válida.');
  }
  var token = nodopinAccessToken_();
  if (!token) throw new Error('NodoPin: no se obtuvo token OAuth.');
  console.log('NodoPin OAuth OK; ningún FCM enviado.');
  return 'OAuth OK; ningún FCM enviado';
}

/**
 * ÚNICA PRUEBA MANUAL Y VISIBLE de transporte: NO CREA un sismo ni altera feeds.
 * Dirigida al topic sísmico de NodoPin (todos los teléfonos suscritos).
 * El bloqueo de 15 minutos reduce pruebas duplicadas accidentales.
 */
function nodopinSeismicTestFcm_() {
  var scriptProps = PropertiesService.getScriptProperties();
  var now = Date.now();
  var last = Number(scriptProps.getProperty('NODOPIN_FCM_LAST_TEST_MS') || 0);
  if (last > 0 && now - last < 15 * 60 * 1000) {
    throw new Error('NodoPin: prueba enviada recientemente. No se repetirá durante 15 minutos.');
  }
  var token = nodopinAccessToken_();
  var payload = {message: {
    topic: NODOPIN_SEISMIC_FCM_V1.topic,
    notification: {
      title: 'PRUEBA FCM · NodoPin',
      body: 'Prueba de recepción. NO ES UN SISMO REAL ni una alerta de emergencia.'
    },
    data: {nodopin_open_centro_dr: 'true', nodopin_test: 'true'},
    android: {
      priority: 'high',
      ttl: '300s',
      restricted_package_name: NODOPIN_SEISMIC_FCM_V1.packageName
    }
  }};
  var result = UrlFetchApp.fetch(NODOPIN_SEISMIC_FCM_V1.endpoint, {
    method: 'post', contentType: 'application/json',
    headers: {Authorization: 'Bearer ' + token},
    payload: JSON.stringify(payload), muteHttpExceptions: true
  });
  if (result.getResponseCode() !== 200) {
    throw new Error('NodoPin prueba FCM: HTTP ' + result.getResponseCode() + '; no se confirmó envío.');
  }
  scriptProps.setProperty('NODOPIN_FCM_LAST_TEST_MS', String(Date.now()));
  console.log('FCM TEST ACCEPTED, tipo=PRUEBA, topic=NodoPin sísmico; no es alerta real.');
  return 'FCM TEST ACCEPTED; recepción visual pendiente de comprobar en Android';
}
