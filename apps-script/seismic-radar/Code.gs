/**
 * DR Radar · Actividad sísmica
 * Backend gratuito para Google Apps Script.
 *
 * Flujo:
 * USGS (feed actualizado cada minuto) -> filtro SOLO República Dominicana ->
 * Firebase Hosting radar-seismic-v231.json -> FCM topic radar_seismic_v231
 *
 * No usa ubicación del usuario.
 * No es un sistema de alerta temprana.
 */

const CONFIG = Object.freeze({
  PROJECT_ID: 'dr-accesorios-rd',
  SITE_ID: 'dr-accesorios-rd',

  RADAR_URL: 'https://dr-accesorios-rd.web.app/radar.json',
  SEISMIC_RADAR_PATH: '/radar-seismic-v231.json',
  SEISMIC_RADAR_URL:
    'https://dr-accesorios-rd.web.app/radar-seismic-v231.json',
  USGS_URL:
    'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson',

  SEISMIC_TOPIC: 'radar_seismic_v231',

  DR_CENTER_LAT: 18.7357,
  DR_CENTER_LON: -70.1627,

  /*
   * MODO GEOGRÁFICO ESTRICTO:
   * solo aceptamos eventos cuyo lugar reportado por la fuente
   * identifique explícitamente a República Dominicana.
   *
   * USGS normalmente usa texto en inglés ("Dominican Republic").
   * Se acepta también la forma en español por robustez.
   */
  DR_PLACE_TOKENS: [
    'dominican republic',
    'república dominicana',
    'republica dominicana',
  ],

  // Escalonado para reducir avisos irrelevantes dentro del país:
  // <= 250 km  => M4.0+
  // <= 500 km  => M4.5+
  // <= 900 km  => M5.5+
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


/**
 * Ejecutar UNA VEZ manualmente desde Apps Script.
 *
 * - Autoriza las APIs.
 * - Verifica USGS.
 * - Verifica Firebase Hosting.
 * - Toma como baseline los sismos relevantes ya existentes
 *   para no mandar avisos retroactivos.
 * - Instala un solo trigger cada 1 minuto.
 */
function setupSeismicRadar() {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);

  try {
    const feed = fetchUsgs_();
    const relevant = getRelevantEvents_(feed);

    // Verifica que el usuario tenga acceso al Hosting del proyecto.
    const active = getActiveHosting_();
    if (!active.versionName) {
      throw new Error('No se pudo resolver la versión activa de Firebase Hosting.');
    }

    /*
     * Verifica el radar tecnológico compartido y elimina únicamente
     * posibles terremotos heredados de la implementación anterior.
     * Así las versiones antiguas dejan de poder descargar sismos.
     */
    const mainRadar = fetchMainRadar_();
    const cleanedMain = removeEarthquakesFromMainRadar_(mainRadar);

    if (cleanedMain.changed) {
      deployHostingJson_(
        '/radar.json',
        cleanedMain.radar,
        'radar.json'
      );
    }

    /*
     * Crea/actualiza el feed sísmico aislado SOLO con eventos que USGS
     * identifica como República Dominicana. setup NO envía FCM.
     */
    const existingSeismic =
      fetchSeismicRadar_(true);

    const currentSeismic =
      existingSeismic || emptySeismicRadar_();

    const seismicMerge =
      mergeSeismicRadar_(currentSeismic, relevant);

    if (
      seismicMerge.changed ||
      existingSeismic === null
    ) {
      deploySeismicRadarJson_(
        seismicMerge.radar
      );
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


/**
 * Función llamada automáticamente cada minuto.
 */
function seismicMinuteTick() {
  const lock = LockService.getScriptLock();

  // Si la ejecución anterior todavía está activa, saltamos este ciclo.
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

    const currentRadar =
      fetchSeismicRadar_(true) || emptySeismicRadar_();

    const merge =
      mergeSeismicRadar_(currentRadar, relevant);

    if (merge.changed) {
      deploySeismicRadarJson_(merge.radar);
    }

    /*
     * Solo un EVENTO NUEVO dispara FCM.
     * Revisiones de magnitud/profundidad actualizan Radar sin volver
     * a alarmar al usuario.
     */
    if (newEvents.length > 0) {
      sendSeismicRefresh_(newEvents);
    }

    /*
     * Guardamos el estado después de publicar/enviar correctamente.
     * Si algo falla antes, el siguiente minuto reintentará.
     */
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


/**
 * PRUEBA CONTROLADA.
 *
 * Antes de usarla:
 * 1) En "Configuración del proyecto > Propiedades de la secuencia de comandos"
 *    crea:
 *      DR_RADAR_ALLOW_CONTROLLED_TEST_V1 = YES
 *
 * 2) Ejecuta controlledSeismicTest().
 *
 * La función borra automáticamente el permiso YES después de usarlo una vez.
 *
 * La alerta se identifica claramente como PRUEBA y NO como un sismo real.
 */
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

  // One-shot: se desarma incluso si después ocurre un fallo.
  properties.deleteProperty(CONFIG.TEST_ENABLE_KEY);

  const lock = LockService.getScriptLock();
  lock.waitLock(20000);

  try {
    const now = new Date();
    const expires = new Date(now.getTime() + 30 * 60 * 1000);
    const testId = 'earthquake-test-' + now.getTime();

    const radar =
      fetchSeismicRadar_(true) || emptySeismicRadar_();

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

    deploySeismicRadarJson_(next);

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


/**
 * Elimina del feed sísmico cualquier tarjeta creada por controlledSeismicTest().
 * No envía otro FCM.
 */
function removeControlledSeismicTest() {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);

  try {
    const radar =
      fetchSeismicRadar_(true) || emptySeismicRadar_();
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

    deploySeismicRadarJson_(next);

    console.log(
      'Pruebas eliminadas:',
      before.length - after.length
    );
  } finally {
    lock.releaseLock();
  }
}


/**
 * Estado rápido para diagnóstico.
 */
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


/**
 * Elimina únicamente el trigger sísmico.
 */
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


/* =========================================================
 * USGS
 * ========================================================= */

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

  const place = String(properties.place || '').trim();

  /*
   * Filtro geográfico estricto:
   * nada de Puerto Rico, Haití, Islas Vírgenes ni Caribe general.
   * El evento debe estar identificado por la fuente como
   * República Dominicana.
   */
  if (!isDominicanRepublicPlace_(place)) {
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


function isDominicanRepublicPlace_(rawPlace) {
  const place = String(rawPlace || '')
    .trim()
    .toLowerCase();

  if (!place) {
    return false;
  }

  return CONFIG.DR_PLACE_TOKENS.some(function(token) {
    return place.indexOf(token) !== -1;
  });
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


/* =========================================================
 * RADAR JSON
 * ========================================================= */

function fetchMainRadar_() {
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


function fetchSeismicRadar_(allowNotFound) {
  const response = UrlFetchApp.fetch(
    CONFIG.SEISMIC_RADAR_URL + '?dr_seismic=' + Date.now(),
    {
      method: 'get',
      headers: {
        'Cache-Control': 'no-cache',
      },
      muteHttpExceptions: true,
    }
  );

  const status = response.getResponseCode();

  if (allowNotFound === true && status === 404) {
    return null;
  }

  requireHttpOk_(
    response,
    'radar-seismic-v231.json'
  );

  const radar = JSON.parse(response.getContentText());

  if (!radar || !Array.isArray(radar.alerts)) {
    throw new Error(
      'radar-seismic-v231.json no tiene estructura válida.'
    );
  }

  return radar;
}


function emptySeismicRadar_() {
  return {
    schemaVersion: 1,
    enabled: true,
    updatedAt: '',
    alerts: [],
  };
}


/*
 * Limpia del radar compartido únicamente alertas earthquake heredadas.
 * No modifica ninguna alerta tecnológica.
 */
function removeEarthquakesFromMainRadar_(current) {
  const sourceAlerts =
    Array.isArray(current.alerts) ? current.alerts : [];

  const filtered = sourceAlerts.filter(function(alert) {
    return String(
      alert && alert.type ? alert.type : ''
    ).toLowerCase() !== 'earthquake';
  });

  if (filtered.length === sourceAlerts.length) {
    return {
      radar: current,
      changed: false,
    };
  }

  const target = deepClone_(current);
  target.alerts = filtered;
  target.updatedAt = isoUtc_(new Date());

  return {
    radar: target,
    changed: true,
  };
}


function mergeSeismicRadar_(current, relevant) {
  const now = new Date();
  const retentionCutoff =
    now.getTime() -
    CONFIG.RADAR_RETENTION_HOURS * 60 * 60 * 1000;

  const earthquakesBySourceId = {};

  (Array.isArray(current.alerts) ? current.alerts : [])
    .forEach(function(alert) {
      if (!alert || typeof alert !== 'object') return;

      if (
        String(alert.type || '').toLowerCase() !==
        'earthquake'
      ) {
        return;
      }

      // Las pruebas se conservan hasta limpieza manual/retención.
      if (
        String(alert.id || '').startsWith(
          'earthquake-test-'
        )
      ) {
        const time = earthquakeTimeMs_(alert);

        if (!time || time >= retentionCutoff) {
          earthquakesBySourceId[
            String(alert.id)
          ] = alert;
        }

        return;
      }

      /*
       * El feed real es exclusivo de República Dominicana. Si quedó
       * almacenado un evento anterior de otra zona del Caribe, se elimina
       * automáticamente en el siguiente merge.
       */
      const storedPlace = String(
        alert.earthquake && alert.earthquake.place
          ? alert.earthquake.place
          : ''
      ).trim();

      if (!isDominicanRepublicPlace_(storedPlace)) {
        return;
      }

      const sourceId =
        earthquakeSourceId_(alert);

      if (!sourceId) return;

      const eventTime =
        earthquakeTimeMs_(alert);

      if (
        eventTime &&
        eventTime < retentionCutoff
      ) {
        return;
      }

      earthquakesBySourceId[sourceId] =
        alert;
    });

  relevant.forEach(function(item) {
    const feature = item.feature;
    const id =
      String(feature.id || '').trim();

    if (!id) return;

    earthquakesBySourceId[id] =
      buildEarthquakeAlert_(
        feature,
        item.distanceKm
      );
  });

  const earthquakes =
    Object.keys(earthquakesBySourceId)
      .map(function(key) {
        return earthquakesBySourceId[key];
      })
      .sort(function(a, b) {
        return (
          earthquakeTimeMs_(a) -
          earthquakeTimeMs_(b)
        );
      });

  const target = {
    schemaVersion:
      Number(current.schemaVersion || 1),
    enabled:
      current.enabled !== false,
    updatedAt:
      String(current.updatedAt || ''),
    alerts:
      earthquakes,
  };

  const currentComparable =
    deepClone_(current);

  const targetComparable =
    deepClone_(target);

  delete currentComparable.updatedAt;
  delete targetComparable.updatedAt;

  const changed =
    stableStringify_(currentComparable) !==
    stableStringify_(targetComparable);

  if (changed) {
    target.updatedAt =
      isoUtc_(now);
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


/* =========================================================
 * FIREBASE HOSTING
 * ========================================================= */

function deploySeismicRadarJson_(targetRadar) {
  deployHostingJson_(
    CONFIG.SEISMIC_RADAR_PATH,
    targetRadar,
    'radar-seismic-v231.json'
  );
}


function deployHostingJson_(hostingPath, targetRadar, label) {
  const activeBefore =
    getActiveHosting_();

  const oldVersionName =
    activeBefore.versionName;

  const oldVersion =
    firebaseApi_(
      'get',
      CONFIG.HOSTING_API +
        oldVersionName
    );

  const oldFiles =
    listHostingFiles_(
      oldVersionName
    );

  if (
    Object.keys(oldFiles).length === 0
  ) {
    throw new Error(
      'Firebase Hosting no devolvió archivos activos.'
    );
  }

  const config =
    deepClone_(
      oldVersion.config || {}
    );

  config.headers =
    Array.isArray(config.headers)
      ? config.headers
      : [];

  let targetHeader = null;

  for (
    let i = config.headers.length - 1;
    i >= 0;
    i--
  ) {
    if (
      config.headers[i] &&
      config.headers[i].glob === hostingPath
    ) {
      targetHeader =
        config.headers[i];
      break;
    }
  }

  if (!targetHeader) {
    targetHeader = {
      glob: hostingPath,
      headers: {},
    };

    config.headers.push(
      targetHeader
    );
  }

  targetHeader.headers =
    targetHeader.headers || {};

  targetHeader.headers['Cache-Control'] =
    'no-cache, no-store, must-revalidate';

  const raw =
    JSON.stringify(
      targetRadar,
      null,
      2
    ) + '\n';
