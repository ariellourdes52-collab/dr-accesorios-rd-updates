/**
 * DR Radar · Actividad sísmica
 * Backend gratuito para Google Apps Script.
 *
 * Flujo:
 * USGS (feed actualizado cada minuto) -> filtro RD/Caribe ->
 * Firebase Hosting radar.json -> FCM topic radar_seismic_v231
 *
 * No usa ubicación del usuario.
 * No es un sistema de alerta temprana.
 */

const CONFIG = Object.freeze({
  PROJECT_ID: 'dr-accesorios-rd',
  SITE_ID: 'dr-accesorios-rd',

  RADAR_URL: 'https://dr-accesorios-rd.web.app/radar.json',
  USGS_URL:
    'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson',

  SEISMIC_TOPIC: 'radar_seismic_v231',

  DR_CENTER_LAT: 18.7357,
  DR_CENTER_LON: -70.1627,

  FILTERS: [
    { maxKm: 250, minMag: 4.0 },
    { maxKm: 500, minMag: 4.5 },
    { maxKm: 900, minMag: 5.5 },
  ],

  ALERT_ACTIVE_HOURS: 12,
  RADAR_RETENTION_HOURS: 24,
  STATE_RETENTION_HOURS: 72,

  FCM_TTL_SECONDS: 15 * 60,

  STATE_KEY: 'DR_RADAR_SEISMIC_STATE_V1',
  BASELINE_KEY: 'DR_RADAR_SEISMIC_BASELINE_READY_V1',
  LAST_HEALTH_KEY: 'DR_RADAR_SEISMIC_LAST_HEALTH_V1',
  TEST_ENABLE_KEY: 'DR_RADAR_ALLOW_CONTROLLED_TEST_V1',

  HOSTING_API: 'https://firebasehosting.googleapis.com/v1beta1/',
  FCM_API:
    'https://fcm.googleapis.com/v1/projects/dr-accesorios-rd/messages:send',
});


function setupSeismicRadar() {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);

  try {
    const feed = fetchUsgs_();
    const relevant = getRelevantEvents_(feed);

    const active = getActiveHosting_();
    if (!active.versionName) {
      throw new Error('No se pudo resolver la versión activa de Firebase Hosting.');
    }

    const radar = fetchRadar_();
    if (!radar || !Array.isArray(radar.alerts)) {
      throw new Error('radar.json no contiene un array alerts válido.');
    }

    const state = {};
    relevant.forEach(function(item) {
      state[item.feature.id] = {
        signature: signatureForFeature_(item.feature),
        eventTime: Number(item.feature.properties.time || Date.now()),
      };
    });

    saveState_(state);

    PropertiesService.getScriptProperties()
      .setProperty(CONFIG.BASELINE_KEY, 'true');

    installMinuteTrigger_();

    recordHealth_('setup_ok', {
      relevantBaselineEvents: relevant.length,
      activeHostingVersion: active.versionName,
    });

    console.log(
      'DR Radar sísmico listo. Baseline:',
      relevant.length,
      'evento(s). Trigger: cada 1 minuto.'
    );
  } finally {
    lock.releaseLock();
  }
}


function seismicMinuteTick() {
  const lock = LockService.getScriptLock();

  if (!lock.tryLock(5000)) {
    console.log('Se omite ciclo: otro proceso sísmico sigue ejecutándose.');
    return;
  }

  try {
    if (!isBaselineReady_()) {
      initializeBaselineWithoutAlerting_();
      return;
    }

    const feed = fetchUsgs_();
    const relevant = getRelevantEvents_(feed);

    const oldState = loadState_();
    const nowMs = Date.now();

    const newEvents = [];
    const revisedEvents = [];

    relevant.forEach(function(item) {
      const feature = item.feature;
      const id = String(feature.id || '').trim();
      if (!id) return;

      const signature = signatureForFeature_(feature);
      const existing = oldState[id];

      if (!existing) {
        newEvents.push(item);
      } else if (existing.signature !== signature) {
        revisedEvents.push(item);
      }
    });

    const shouldHousekeep =
      new Date().getUTCMinutes() % 30 === 0;

    if (
      newEvents.length === 0 &&
      revisedEvents.length === 0 &&
      !shouldHousekeep
    ) {
      recordHealth_('ok_no_change', {
        feedEvents: Array.isArray(feed.features) ? feed.features.length : 0,
        relevantEvents: relevant.length,
      });
      return;
    }

    const currentRadar = fetchRadar_();
    const merge = mergeRadar_(currentRadar, relevant);

    if (merge.changed) {
      deployRadarJson_(merge.radar);
    }

    if (newEvents.length > 0) {
      sendSeismicRefresh_(newEvents);
    }

    const newState = buildState_(relevant, oldState, nowMs);
    saveState_(newState);

    recordHealth_('ok', {
      feedEvents: Array.isArray(feed.features) ? feed.features.length : 0,
      relevantEvents: relevant.length,
      newEvents: newEvents.map(function(x) { return x.feature.id; }),
      revisedEvents: revisedEvents.map(function(x) { return x.feature.id; }),
      radarChanged: merge.changed,
    });

  } catch (error) {
    recordHealth_('error', {
      message: String(error && error.stack ? error.stack : error),
    });
    throw error;
  } finally {
    lock.releaseLock();
  }
}


function controlledSeismicTest() {
  const properties = PropertiesService.getScriptProperties();
  const allowed = properties.getProperty(CONFIG.TEST_ENABLE_KEY);

  if (allowed !== 'YES') {
    throw new Error(
      'Prueba bloqueada. Define ' +
      CONFIG.TEST_ENABLE_KEY +
      '=YES en Script Properties.'
    );
  }

  properties.deleteProperty(CONFIG.TEST_ENABLE_KEY);

  const lock = LockService.getScriptLock();
  lock.waitLock(20000);

  try {
    const now = new Date();
    const expires = new Date(now.getTime() + 30 * 60 * 1000);
    const testId = 'earthquake-test-' + now.getTime();

    const radar = fetchRadar_();

    const testAlert = {
      id: testId,
      active: true,
      createdAt: isoUtc_(now),
      expiresAt: isoUtc_(expires),
      category: 'earthquake',
      severity: 'critical',
      title: 'PRUEBA DR Radar · NO ES UN SISMO REAL',
      description:
        'Esta es una prueba controlada del sistema de actividad sísmica de ' +
        'DR Radar. No existe un terremoto asociado a este aviso.',
      url: '',
      target: {
        allDevices: true,
        manufacturers: [],
        models: [],
      },
      type: 'earthquake',
      earthquake: {
        magnitude: 5.4,
        place: 'PRUEBA CONTROLADA · República Dominicana',
        depthKm: 10.0,
        eventTime: now.getTime(),
        latitude: CONFIG.DR_CENTER_LAT,
        longitude: CONFIG.DR_CENTER_LON,
        source: 'DR Radar TEST',
        sourceEventId: testId,
        status: 'test',
      },
    };

    const next = deepClone_(radar);
    next.alerts = (Array.isArray(next.alerts) ? next.alerts : [])
      .filter(function(alert) {
        return alert && alert.id !== testId;
      })
      .concat([testAlert]);

    next.updatedAt = isoUtc_(now);

    deployRadarJson_(next);

    sendFcmData_({
      type: 'earthquake_alert',
      source: 'DR_RADAR_TEST',
      eventCount: '1',
      test: 'true',
    });

    properties.setProperty('DR_RADAR_LAST_TEST_ID_V1', testId);

    console.log('Prueba sísmica enviada:', testId);

  } finally {
    lock.releaseLock();
  }
}


function removeControlledSeismicTest() {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);

  try {
    const radar = fetchRadar_();
    const before = Array.isArray(radar.alerts) ? radar.alerts : [];

    const after = before.filter(function(alert) {
      const id = String(alert && alert.id ? alert.id : '');
      return !id.startsWith('earthquake-test-');
    });

    if (after.length === before.length) {
      console.log('No hay pruebas sísmicas que limpiar.');
      return;
    }

    const next = deepClone_(radar);
    next.alerts = after;
    next.updatedAt = isoUtc_(new Date());

    deployRadarJson_(next);

    console.log(
      'Pruebas eliminadas:',
      before.length - after.length
    );
  } finally {
    lock.releaseLock();
  }
}


function seismicHealth() {
  const props = PropertiesService.getScriptProperties();
  const health = props.getProperty(CONFIG.LAST_HEALTH_KEY);

  console.log('Baseline:', isBaselineReady_());
  console.log('Último estado:', health || 'sin datos');

  const triggers = ScriptApp.getProjectTriggers()
    .filter(function(trigger) {
      return trigger.getHandlerFunction() === 'seismicMinuteTick';
    });

  console.log('Triggers seismicMinuteTick:', triggers.length);

  return {
    baselineReady: isBaselineReady_(),
    lastHealth: health ? JSON.parse(health) : null,
    triggerCount: triggers.length,
  };
}


function disableSeismicMinuteTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(function(trigger) {
      return trigger.getHandlerFunction() === 'seismicMinuteTick';
    })
    .forEach(function(trigger) {
      ScriptApp.deleteTrigger(trigger);
    });

  console.log('Trigger sísmico desactivado.');
}


function fetchUsgs_() {
  const url = CONFIG.USGS_URL + '?dr_radar=' + Date.now();

  const response = UrlFetchApp.fetch(url, {
    method: 'get',
    headers: {
      Accept: 'application/geo+json, application/json',
      'Cache-Control': 'no-cache',
    },
    muteHttpExceptions: true,
  });

  requireHttpOk_(response, 'USGS');

  const payload = JSON.parse(response.getContentText());

  if (!payload || payload.type !== 'FeatureCollection') {
    throw new Error('USGS devolvió un payload inesperado.');
  }

  return payload;
}


function getRelevantEvents_(feed) {
  const features = Array.isArray(feed.features) ? feed.features : [];

  return features
    .map(function(feature) {
      const result = evaluateFeature_(feature);
      return {
        feature: feature,
        accepted: result.accepted,
        distanceKm: result.distanceKm,
      };
    })
    .filter(function(item) {
      return item.accepted;
    })
    .sort(function(a, b) {
      return Number(a.feature.properties.time || 0) -
        Number(b.feature.properties.time || 0);
    });
}


function evaluateFeature_(feature) {
  const properties = feature && feature.properties
    ? feature.properties
    : {};

  const geometry = feature && feature.geometry
    ? feature.geometry
    : {};

  const coordinates = Array.isArray(geometry.coordinates)
    ? geometry.coordinates
    : [];

  if (
    String(properties.type || '').toLowerCase() !== 'earthquake' ||
    typeof properties.mag !== 'number' ||
    coordinates.length < 2
  ) {
    return {
      accepted: false,
      distanceKm: Infinity,
    };
  }

  const lon = Number(coordinates[0]);
  const lat = Number(coordinates[1]);

  if (!isFinite(lon) || !isFinite(lat)) {
    return {
      accepted: false,
      distanceKm: Infinity,
    };
  }

  const distanceKm = haversineKm_(
    CONFIG.DR_CENTER_LAT,
    CONFIG.DR_CENTER_LON,
    lat,
    lon
  );

  let minMag = null;

  CONFIG.FILTERS.some(function(rule) {
    if (distanceKm <= rule.maxKm) {
      minMag = rule.minMag;
      return true;
    }
    return false;
  });

  if (minMag === null) {
    return {
      accepted: false,
      distanceKm: distanceKm,
    };
  }

  return {
    accepted: Number(properties.mag) >= minMag,
    distanceKm: distanceKm,
  };
}


function haversineKm_(lat1, lon1, lat2, lon2) {
  const radius = 6371.0088;

  const p1 = lat1 * Math.PI / 180;
  const p2 = lat2 * Math.PI / 180;
  const dp = (lat2 - lat1) * Math.PI / 180;
  const dl = (lon2 - lon1) * Math.PI / 180;

  const a =
    Math.sin(dp / 2) * Math.sin(dp / 2) +
    Math.cos(p1) * Math.cos(p2) *
    Math.sin(dl / 2) * Math.sin(dl / 2);

  return radius * 2 * Math.atan2(
    Math.sqrt(a),
    Math.sqrt(1 - a)
  );
}


function signatureForFeature_(feature) {
  const p = feature.properties || {};
  const c = feature.geometry && Array.isArray(feature.geometry.coordinates)
    ? feature.geometry.coordinates
    : [];

  return [
    p.mag,
    p.updated,
    p.status,
    p.place,
    c[0],
    c[1],
    c[2],
  ].join('|');
}


function fetchRadar_() {
  const response = UrlFetchApp.fetch(
    CONFIG.RADAR_URL + '?dr_radar=' + Date.now(),
    {
      method: 'get',
      headers: {
        'Cache-Control': 'no-cache',
      },
      muteHttpExceptions: true,
    }
  );

  requireHttpOk_(response, 'radar.json');

  const radar = JSON.parse(response.getContentText());

  if (!radar || !Array.isArray(radar.alerts)) {
    throw new Error('radar.json no tiene estructura válida.');
  }

  return radar;
}


function mergeRadar_(current, relevant) {
  const now = new Date();
  const retentionCutoff =
    now.getTime() - CONFIG.RADAR_RETENTION_HOURS * 60 * 60 * 1000;

  const technological = [];
  const earthquakesBySourceId = {};

  (Array.isArray(current.alerts) ? current.alerts : [])
    .forEach(function(alert) {
      if (!alert || typeof alert !== 'object') return;

      if (String(alert.type || '').toLowerCase() !== 'earthquake') {
        technological.push(alert);
        return;
      }

      if (String(alert.id || '').startsWith('earthquake-test-')) {
        const time = earthquakeTimeMs_(alert);
        if (!time || time >= retentionCutoff) {
          earthquakesBySourceId[String(alert.id)] = alert;
        }
        return;
      }

      const sourceId = earthquakeSourceId_(alert);
      if (!sourceId) return;

      const eventTime = earthquakeTimeMs_(alert);

      if (eventTime && eventTime < retentionCutoff) {
        return;
      }

      earthquakesBySourceId[sourceId] = alert;
    });

  relevant.forEach(function(item) {
    const feature = item.feature;
    const id = String(feature.id || '').trim();

    if (!id) return;

    earthquakesBySourceId[id] =
      buildEarthquakeAlert_(feature, item.distanceKm);
  });

  const earthquakes = Object.keys(earthquakesBySourceId)
    .map(function(key) {
      return earthquakesBySourceId[key];
    })
    .sort(function(a, b) {
      return earthquakeTimeMs_(a) - earthquakeTimeMs_(b);
    });

  const target = deepClone_(current);
  target.schemaVersion = Number(current.schemaVersion || 1);
  target.enabled = current.enabled !== false;
  target.alerts = technological.concat(earthquakes);

  const currentComparable = deepClone_(current);
  const targetComparable = deepClone_(target);

  delete currentComparable.updatedAt;
  delete targetComparable.updatedAt;

  const changed =
    stableStringify_(currentComparable) !== stableStringify_(targetComparable);

  if (changed) {
    target.updatedAt = isoUtc_(now);
  }

  return {
    radar: target,
    changed: changed,
  };
}


function buildEarthquakeAlert_(feature, distanceKm) {
  const p = feature.properties || {};
  const c = feature.geometry && Array.isArray(feature.geometry.coordinates)
    ? feature.geometry.coordinates
    : [];

  const sourceId = String(feature.id || '').trim();
  const mag = Number(p.mag);
  const place = String(
    p.place || 'Ubicación no especificada por la fuente'
  ).trim();

  const eventTimeMs = Number(p.time || Date.now());
  const eventDate = new Date(eventTimeMs);
  const expires = new Date(
    eventTimeMs + CONFIG.ALERT_ACTIVE_HOURS * 60 * 60 * 1000
  );

  const lon = Number(c[0]);
  const lat = Number(c[1]);
  const depth = c.length >= 3 && isFinite(Number(c[2]))
    ? Number(c[2])
    : null;

  const magText = mag.toFixed(1);

  let description =
    'USGS reportó un sismo de magnitud ' +
    magText +
    ' en ' +
    place +
    '. El epicentro se encuentra aproximadamente a ' +
    Math.round(distanceKm) +
    ' km del centro de República Dominicana.';

  if (depth !== null) {
    description +=
      ' Profundidad reportada: ' +
      depth.toFixed(1) +
      ' km.';
  }

  description +=
    ' Esta información corresponde a un evento ya detectado; ' +
    'no es una predicción ni una alerta temprana.';

  return {
    id: 'earthquake-usgs-' + sourceId,
    active: true,
    createdAt: isoUtc_(eventDate),
    expiresAt: isoUtc_(expires),
    category: 'earthquake',
    severity: 'critical',
    title: 'Actividad sísmica detectada · M' + magText,
    description: description,
    url: String(p.url || ''),
    target: {
      allDevices: true,
      manufacturers: [],
      models: [],
    },
    type: 'earthquake',
    earthquake: {
      magnitude: mag,
      place: place,
      depthKm: depth,
      eventTime: eventTimeMs,
      latitude: lat,
      longitude: lon,
      source: 'USGS',
      sourceEventId: sourceId,
      status: String(p.status || ''),
    },
  };
}


function earthquakeSourceId_(alert) {
  const eq = alert.earthquake || {};
  const direct = String(eq.sourceEventId || '').trim();

  if (direct) return direct;

  const id = String(alert.id || '');
  const prefix = 'earthquake-usgs-';

  return id.startsWith(prefix)
    ? id.substring(prefix.length)
    : '';
}


function earthquakeTimeMs_(alert) {
  const eq = alert.earthquake || {};

  if (typeof eq.eventTime === 'number') {
    return eq.eventTime;
  }

  const parsed = Date.parse(String(alert.createdAt || ''));

  return isNaN(parsed) ? 0 : parsed;
}


function deployRadarJson_(targetRadar) {
  const activeBefore = getActiveHosting_();
  const oldVersionName = activeBefore.versionName;

  const oldVersion = firebaseApi_(
    'get',
    CONFIG.HOSTING_API + oldVersionName
  );

  const oldFiles = listHostingFiles_(oldVersionName);

  if (Object.keys(oldFiles).length === 0) {
    throw new Error('Firebase Hosting no devolvió archivos activos.');
  }

  const config = deepClone_(oldVersion.config || {});
  config.headers = Array.isArray(config.headers)
    ? config.headers
    : [];

  let radarHeader = null;

  for (let i = config.headers.length - 1; i >= 0; i--) {
    if (config.headers[i] && config.headers[i].glob === '/radar.json') {
      radarHeader = config.headers[i];
      break;
    }
  }

  if (!radarHeader) {
    radarHeader = {
      glob: '/radar.json',
      headers: {},
    };
    config.headers.push(radarHeader);
  }

  radarHeader.headers = radarHeader.headers || {};
  radarHeader.headers['Cache-Control'] =
    'no-cache, no-store, must-revalidate';

  const raw =
    JSON.stringify(targetRadar, null, 2) + '\n';

  const gzipBlob = Utilities.gzip(
    Utilities.newBlob(raw, 'application/json', 'radar.json')
  );

  const gzipBytes = gzipBlob.getBytes();
  const digest = sha256Hex_(gzipBytes);

  const expected = deepClone_(oldFiles);
  expected['/radar.json'] = digest;

  const created = firebaseApi_(
    'post',
    CONFIG.HOSTING_API +
      'sites/' +
      encodeURIComponent(CONFIG.SITE_ID) +
      '/versions',
    {
      config: config,
    }
  );

  const newVersionName = String(created.name || '');

  if (!newVersionName) {
    throw new Error('Firebase Hosting no creó una versión nueva.');
  }

  const paths = Object.keys(expected);

  for (let start = 0; start < paths.length; start += 1000) {
    const slice = paths.slice(start, start + 1000);
    const files = {};

    slice.forEach(function(path) {
      files[path] = expected[path];
    });

    const populated = firebaseApi_(
      'post',
      CONFIG.HOSTING_API +
        newVersionName +
        ':populateFiles',
      {
        files: files,
      }
    );

    const required = Array.isArray(populated.uploadRequiredHashes)
      ? populated.uploadRequiredHashes
      : [];

    const unexpected = required.filter(function(hash) {
      return hash !== digest;
    });

    if (unexpected.length > 0) {
      throw new Error(
        'Firebase pidió archivos preservados que el script no debe reemplazar: ' +
        unexpected.join(', ')
      );
    }

    if (required.indexOf(digest) >= 0) {
      uploadHostingBlob_(
        String(populated.uploadUrl || '') + '/' + digest,
        gzipBytes
      );
    }
  }

  const beforeFinalize = listHostingFiles_(newVersionName);

  if (stableStringify_(beforeFinalize) !== stableStringify_(expected)) {
    throw new Error(
      'El inventario de Hosting no coincide antes de finalizar.'
    );
  }

  firebaseApi_(
    'patch',
    CONFIG.HOSTING_API +
      newVersionName +
      '?updateMask=status',
    {
      status: 'FINALIZED',
    }
  );

  const activeStill = getActiveHosting_();

  if (
    activeStill.releaseName !== activeBefore.releaseName ||
    activeStill.versionName !== activeBefore.versionName
  ) {
    throw new Error(
      'Otro despliegue de Firebase Hosting ocurrió en paralelo. ' +
      'Se abortó antes de publicar radar.json.'
    );
  }

  firebaseApi_(
    'post',
    CONFIG.HOSTING_API +
      'sites/' +
      encodeURIComponent(CONFIG.SITE_ID) +
      '/releases?versionName=' +
      encodeURIComponent(newVersionName),
    {
      message:
        'DR Radar seismic update; preserve all other Hosting files',
    }
  );

  const activeAfter = getActiveHosting_();

  if (activeAfter.versionName !== newVersionName) {
    throw new Error(
      'La versión sísmica no quedó activa en Firebase Hosting.'
    );
  }

  const live = fetchRadar_();

  if (stableStringify_(live) !== stableStringify_(targetRadar)) {
    throw new Error(
      'radar.json publicado no coincide con el contenido esperado.'
    );
  }

  console.log('radar.json publicado:', newVersionName);
}


function getActiveHosting_() {
  const channel = firebaseApi_(
    'get',
    CONFIG.HOSTING_API +
      'sites/' +
      encodeURIComponent(CONFIG.SITE_ID) +
      '/channels/live'
  );

  const release = channel.release || {};
  const version = release.version || {};

  return {
    releaseName: String(release.name || ''),
    versionName: String(version.name || ''),
  };
}


function listHostingFiles_(versionName) {
  const result = {};
  let pageToken = '';

  do {
    let url =
      CONFIG.HOSTING_API +
      versionName +
      '/files?pageSize=1000&status=ACTIVE';

    if (pageToken) {
      url += '&pageToken=' + encodeURIComponent(pageToken);
    }

    const page = firebaseApi_('get', url);

    (Array.isArray(page.files) ? page.files : [])
      .forEach(function(item) {
        const path = String(item.path || '');
        const hash = String(item.hash || '');

        if (!path || !hash) return;

        if (Object.prototype.hasOwnProperty.call(result, path)) {
          throw new Error('Ruta Hosting duplicada: ' + path);
        }

        result[path] = hash;
      });

    pageToken = String(page.nextPageToken || '');
  } while (pageToken);

  return result;
}


function uploadHostingBlob_(url, bytes) {
  if (!url) {
    throw new Error('Firebase Hosting no devolvió uploadUrl.');
  }

  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    headers: {
      Authorization: 'Bearer ' + ScriptApp.getOAuthToken(),
    },
    contentType: 'application/octet-stream',
    payload: bytes,
    muteHttpExceptions: true,
  });

  requireHttpOk_(response, 'upload Firebase Hosting');
}


function sendSeismicRefresh_(newEvents) {
  const ids = newEvents
    .map(function(item) {
      return String(item.feature.id || '');
    })
    .filter(Boolean);

  sendFcmData_({
    type: 'earthquake_alert',
    source: 'USGS',
    eventCount: String(ids.length),
    newestEventId: ids.length ? ids[ids.length - 1] : '',
  });

  console.log(
    'FCM sísmico enviado al topic',
    CONFIG.SEISMIC_TOPIC,
    'eventos:',
    ids
  );
}


function sendFcmData_(data) {
  const stringData = {};

  Object.keys(data).forEach(function(key) {
    stringData[key] = String(data[key]);
  });

  const payload = {
    message: {
      topic: CONFIG.SEISMIC_TOPIC,
      android: {
        priority: 'high',
        ttl: CONFIG.FCM_TTL_SECONDS + 's',
        collapse_key: 'dr_radar_earthquake',
        restricted_package_name: 'com.draccesoriosrd.app',
      },
      data: stringData,
      fcm_options: {
        analytics_label: 'radar_earthquake',
      },
    },
  };

  firebaseApi_(
    'post',
    CONFIG.FCM_API,
    payload
  );
}


function firebaseApi_(method, url, payload) {
  const options = {
    method: method,
    headers: {
      Authorization: 'Bearer ' + ScriptApp.getOAuthToken(),
      Accept: 'application/json',
    },
    muteHttpExceptions: true,
  };

  if (payload !== undefined) {
    options.contentType = 'application/json; charset=UTF-8';
    options.payload = JSON.stringify(payload);
  }

  const response = UrlFetchApp.fetch(url, options);

  requireHttpOk_(response, url);

  const text = response.getContentText();

  return text ? JSON.parse(text) : {};
}


function requireHttpOk_(response, label) {
  const code = response.getResponseCode();

  if (code >= 200 && code < 300) {
    return;
  }

  throw new Error(
    label +
    ' respondió HTTP ' +
    code +
    ': ' +
    response.getContentText().substring(0, 1000)
  );
}


function installMinuteTrigger_() {
  ScriptApp.getProjectTriggers()
    .filter(function(trigger) {
      return trigger.getHandlerFunction() === 'seismicMinuteTick';
    })
    .forEach(function(trigger) {
      ScriptApp.deleteTrigger(trigger);
    });

  ScriptApp.newTrigger('seismicMinuteTick')
    .timeBased()
    .everyMinutes(1)
    .create();
}


function initializeBaselineWithoutAlerting_() {
  const feed = fetchUsgs_();
  const relevant = getRelevantEvents_(feed);

  const state = {};

  relevant.forEach(function(item) {
    state[item.feature.id] = {
      signature: signatureForFeature_(item.feature),
      eventTime: Number(item.feature.properties.time || Date.now()),
    };
  });

  saveState_(state);

  PropertiesService.getScriptProperties()
    .setProperty(CONFIG.BASELINE_KEY, 'true');

  recordHealth_('baseline_initialized_automatically', {
    relevantBaselineEvents: relevant.length,
  });

  console.log(
    'Baseline inicializado sin enviar alertas:',
    relevant.length
  );
}


function isBaselineReady_() {
  return PropertiesService.getScriptProperties()
    .getProperty(CONFIG.BASELINE_KEY) === 'true';
}


function loadState_() {
  const raw = PropertiesService.getScriptProperties()
    .getProperty(CONFIG.STATE_KEY);

  if (!raw) return {};

  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object'
      ? parsed
      : {};
  } catch (error) {
    return {};
  }
}


function saveState_(state) {
  PropertiesService.getScriptProperties()
    .setProperty(CONFIG.STATE_KEY, JSON.stringify(state));
}


function buildState_(relevant, oldState, nowMs) {
  const cutoff =
    nowMs - CONFIG.STATE_RETENTION_HOURS * 60 * 60 * 1000;

  const result = {};

  Object.keys(oldState || {}).forEach(function(id) {
    const item = oldState[id];

    if (
      item &&
      Number(item.eventTime || 0) >= cutoff
    ) {
      result[id] = item;
    }
  });

  relevant.forEach(function(item) {
    const feature = item.feature;
    const id = String(feature.id || '').trim();

    if (!id) return;

    result[id] = {
      signature: signatureForFeature_(feature),
      eventTime: Number(feature.properties.time || nowMs),
    };
  });

  return result;
}


function recordHealth_(status, details) {
  const payload = {
    status: status,
    at: new Date().toISOString(),
    details: details || {},
  };

  PropertiesService.getScriptProperties()
    .setProperty(
      CONFIG.LAST_HEALTH_KEY,
      JSON.stringify(payload)
    );
}


function sha256Hex_(bytes) {
  const digest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    bytes
  );

  return digest
    .map(function(value) {
      const unsigned = value < 0 ? value + 256 : value;
      return ('0' + unsigned.toString(16)).slice(-2);
    })
    .join('');
}


function isoUtc_(date) {
  return date.toISOString();
}


function deepClone_(value) {
  return JSON.parse(JSON.stringify(value));
}


function stableStringify_(value) {
  return JSON.stringify(sortObject_(value));
}


function sortObject_(value) {
  if (Array.isArray(value)) {
    return value.map(sortObject_);
  }

  if (value && typeof value === 'object') {
    const result = {};

    Object.keys(value)
      .sort()
      .forEach(function(key) {
        result[key] = sortObject_(value[key]);
      });

    return result;
  }

  return value;
}
