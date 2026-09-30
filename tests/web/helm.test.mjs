import test from 'node:test';
import assert from 'node:assert/strict';
import {SHIPS} from '../../web/public/console/ships.js';
import {initialState,step,destination,hullFeatures,trailGeo,sessionDocument} from '../../web/public/console/helm-domain.js';
import {harborFeatures,harborMapURL} from '../../web/public/console/harbor-domain.js';
import {readFileSync,readdirSync} from 'node:fs';
const vessel=SHIPS[0];
const simulate=(s,c,seconds)=>{for(let t=0;t<seconds;t++)s=step(s,c,vessel,1);return s;};
test('twelve distinct reference types retain official sources and uncalibrated status',()=>{
 assert.ok(SHIPS.length>=12);assert.equal(new Set(SHIPS.map(s=>s.type)).size,SHIPS.length);
 for(const s of SHIPS){assert.ok(s.length>s.beam&&s.beam>0);assert.match(s.source,/^https:\/\//);assert.match(s.handlingStatus,/not_sea_trial/);}
});
test('neutral resting vessel does not drift and time advances',()=>{
 const s=step(initialState(),{throttle:0,rudder:35},vessel,60);
 assert.equal(s.lon,129.135);assert.equal(s.lat,35.065);assert.equal(s.speed,0);assert.equal(s.heading,90);assert.ok(Math.abs(s.elapsed-60)<1e-9);
});
test('forward and reverse move in opposite directions; neutral does not instant-stop',()=>{
 const start=initialState(0,0,90),forward=simulate(start,{throttle:100,rudder:0},120);
 const reverse=simulate(start,{throttle:-50,rudder:0},120);
 assert.ok(forward.lon>0&&reverse.lon<0);assert.ok(forward.speed>0&&reverse.speed<0);
 const neutral=step(forward,{throttle:0,rudder:0},vessel,1);
 assert.ok(neutral.speed>0&&neutral.speed<forward.speed);
});
test('port and starboard commands turn symmetrically and rudder is limited',()=>{
 const s={...initialState(0,0,0),speed:10};
 const a=simulate(s,{throttle:50,rudder:-100},30),b=simulate(s,{throttle:50,rudder:100},30);
 assert.ok(a.lon<0&&b.lon>0);assert.ok(a.rudder>=-35&&b.rudder<=35);assert.ok(Math.abs(a.yaw+b.yaw)<1e-10);
});
test('large steps agree with bounded updates and malformed commands fail',()=>{
 const s=initialState(),c={throttle:80,rudder:20},a=step(s,c,vessel,30),b=simulate(s,c,30);
 assert.ok(Math.abs(a.lon-b.lon)<1e-9);assert.ok(Math.abs(a.heading-b.heading)<1e-9);
 assert.throws(()=>step(s,c,vessel,Infinity));assert.throws(()=>step(s,{...c,rudder:NaN},vessel,1));assert.throws(()=>initialState(0,90));
});
test('metre displacement and date-line wrapping are consistent',()=>{
 const [lon,lat]=destination(0,0,0,1852);
 assert.ok(Math.abs(lat-1/60)<.00002);assert.ok(Math.abs(lon)<1e-8);
 assert.ok(destination(179.999,0,90,1000)[0]<-179.9);
 const geo=trailGeo([{lon:179.8,lat:0},{lon:179.9,lat:0},{lon:-179.9,lat:0},{lon:-179.8,lat:0}]);
 assert.equal(geo.features.length,2);assert.equal(trailGeo([{lon:0,lat:0}]).features.length,0);
});
test('all hull geometry closes and retains local date-line continuity',()=>{
 for(const ship of SHIPS)for(const lon of [129,179.999,-179.999]){
  const geo=hullFeatures(initialState(lon,35,90),ship);
  for(const f of geo.features){const ring=f.geometry.coordinates[0];assert.deepEqual(ring[0],ring.at(-1));assert.ok(ring.every(p=>p.every(Number.isFinite)));assert.ok(Math.max(...ring.map(p=>p[0]))-Math.min(...ring.map(p=>p[0]))<.02);}
 }
});
test('export preserves assumptions, source and units',()=>{
 const d=sessionDocument(vessel,initialState(),[{...initialState(),throttle:50}],{id:'unlocode:KRPUS'});
 assert.equal(d.reference_ship.source,vessel.source);assert.equal(d.units.speed,'kn');assert.equal(d.samples[0].throttle,50);assert.ok(d.limitations.length>=4);
});
test('OSM converter does not close open ways or invent missing nodes',()=>{
 const raw={elements:[{type:'node',id:1,lat:0,lon:1},{type:'node',id:2,lat:0,lon:2},
 {type:'way',id:3,tags:{man_made:'pier'},nodes:[1,2]},
 {type:'way',id:4,tags:{building:'warehouse'},nodes:[1,99]},
 {type:'way',id:5,tags:{highway:'road'},nodes:[1,2]}]};
 const g=harborFeatures(raw);assert.equal(g.features.length,1);assert.equal(g.features[0].geometry.type,'LineString');assert.deepEqual(g.features[0].geometry.coordinates,[[1,0],[2,0]]);
 assert.throws(()=>harborFeatures({elements:[],remark:'timeout'}));assert.throws(()=>harborMapURL(NaN,0));
});
test('bundled harbor snapshots retain provenance and render source ways',()=>{
 const dir=new URL('../../web/public/console/harbors/',import.meta.url);
 const names=readdirSync(dir).filter(n=>n.endsWith('.json')&&n!=='manifest.json');
 assert.ok(new Set(names.map(n=>n.slice(0,2))).size>=12);
 for(const name of names){
  const raw=JSON.parse(readFileSync(new URL(name,dir),'utf8'));
  assert.match(raw.snapshot.raw_sha256,/^[a-f0-9]{64}$/);assert.match(raw.snapshot.source_url,/^https:\/\/api.openstreetmap.org\//);
  const geo=harborFeatures(raw);assert.equal(geo.features.length,raw.snapshot.features);
 }
});
