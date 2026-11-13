/* Offline regression for EMSC/USGS persistence; never calls Firebase or FCM. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('apps-script/seismic-radar/Code.gs', 'utf8');
const sandbox = vm.createContext({ console });
vm.runInContext(code, sandbox, {filename: 'Code.gs', timeout: 1000});
const invoke = (name, ...args) => sandbox[name](...args);

const T = Date.now() - 120000;
const event = (id, time=T, lat=18.7357, lon=-70.1627, magnitude=4.5) => ({
  id, eventTime:time, latitude:lat, longitude:lon,
  magnitude, place:'DOMINICAN REPUBLIC', depthKm:15,
});
const usgs = (id, time=T, lat=18.7357, lon=-70.1627, magnitude=4.5) => ({
  feature:{id,properties:{type:'earthquake',mag:magnitude,
    time,place:'10 km of Dominican Republic',status:'reviewed',updated:time},
    geometry:{coordinates:[lon,lat,12]}},distanceKm:0,
});
const empty = () => ({schemaVersion:1,enabled:true,updatedAt:'',alerts:[]});
const merge = (current, usgsEvents, emscEvents) =>
  invoke('mergeSeismicRadar_',current,usgsEvents,emscEvents).radar;
function checks(){
  const E = event('emsc-1');
  const U = usgs('usgs-1');
  // EMSC arrives first; already present before USGS and stays on every refresh.
  const a = merge(empty(), [], [E]);
  assert.equal(a.alerts.length,1);
  assert.equal(a.alerts[0].earthquake.source,'EMSC-RTS');
  assert.equal(a.alerts[0].earthquake.verificationStatus,'single_source');
  const b = merge(a,[],[]);
  assert.equal(b.alerts.length,1);
  assert.equal(b.alerts[0].id,a.alerts[0].id);
  const c = merge(b,[U],[E]);
  assert.equal(c.alerts.length,1);
  assert.equal(c.alerts[0].id,a.alerts[0].id);
  assert.equal(c.alerts[0].earthquake.verificationStatus,'dual_source');
  assert.equal(c.alerts[0].earthquake.usgsEventId,'usgs-1');
  assert.equal(c.alerts[0].earthquake.emscEventId,'emsc-1');
  assert.equal(merge(c,[U],[E]).alerts.length,1);
  assert.equal(merge(c,[],[]).alerts.length,1);

  // USGS arrives first. EMSC is linked to the original USGS event.
  const d = merge(empty(),[U],[]);
  assert.equal(d.alerts.length,1);
  const e = merge(d,[U],[E]);
  assert.equal(e.alerts.length,1);
  assert.equal(e.alerts[0].id,d.alerts[0].id);
  assert.equal(e.alerts[0].earthquake.verificationStatus,'dual_source');

  // A geographically or temporally distinct aftershock must stay separate.
  const far = merge(a,[usgs('aftershock',T+180000,18.7357,-70.1627)],[]);
  assert.equal(far.alerts.length,2);

  // An unrelated EMSC record must never overwrite a USGS report.
  assert.equal(merge(d,[U],[event('different',T+240000)]).alerts.length,2);

  // Ambiguous USGS candidates: do not merge one EMSC into two earthquakes.
  const twoUsgs = merge(empty(),
    [usgs('usgs-1'),usgs('usgs-2',T+30000,18.7357,-70.1627,4.6)], [E]);
  assert.equal(twoUsgs.alerts.length,3);

  // One source's data must remain present when the other source vanishes.
  const uOnly = merge(d,[],[]);
  assert.equal(uOnly.alerts.length,1);
  assert.equal(uOnly.alerts[0].earthquake.source,'USGS');

  // No transport or hosting calls are made during the tests.
}
checks();
console.log('OK: EMSC/USGS feed persistence, either arrival order, association, aftershocks');
