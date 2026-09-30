import test from 'node:test';
import assert from 'node:assert/strict';
import {voyageTrackFeatures} from '../../web/public/console/voyage-domain.js';

const below='#207f87',above='#b97247',missing='#8795a3';
const point=(lon,hs,lat=30)=>({lat,lon,values:{hs}});

test('1191 low-wave positions form one feature with every original vertex',()=>{
 const points=Array.from({length:1191},(_,i)=>point((129+i*113/1190+180)%360-180,2,35+20*Math.sin(i*Math.PI/1190)));
 const original=structuredClone(points),features=voyageTrackFeatures(points,3);
 assert.equal(features.length,1);
 assert.equal(features[0].type,'Feature');assert.equal(features[0].geometry.type,'LineString');
 assert.equal(features[0].properties.color,below);
 const coordinates=features[0].geometry.coordinates;
 assert.equal(coordinates.length,1191);
 for(let i=0;i<points.length;i++){
  assert.equal(coordinates[i][1],points[i].lat);
  assert(Math.abs(coordinates[i][0]-(129+i*113/1190))<1e-10);
 }
 assert.deepEqual(points,original);
});

test('color changes retain their shared endpoint without dropping or joining intervals',()=>{
 const points=[1,2,4,5,1,null,null,1,2].map((hs,i)=>point(130+i,hs));
 const features=voyageTrackFeatures(points,3);
 assert.deepEqual(features.map(f=>f.properties.color),[below,above,missing,below]);
 assert.deepEqual(features.map(f=>f.geometry.coordinates.length),[2,4,4,2]);
 for(let i=1;i<features.length;i++)assert.deepEqual(features[i-1].geometry.coordinates.at(-1),features[i].geometry.coordinates[0]);
 const restored=features.flatMap((f,i)=>i?f.geometry.coordinates.slice(1):f.geometry.coordinates);
 assert.deepEqual(restored,points.map(p=>[p.lon,p.lat]));
});

test('missing observations merge into one grey run and never acquire a low-wave color',()=>{
 const points=[null,null,undefined,NaN,Infinity,null].map((hs,i)=>point(140+i,hs));
 const features=voyageTrackFeatures(points,3);
 assert.equal(features.length,1);assert.equal(features[0].properties.color,missing);
 assert.deepEqual(features[0].geometry.coordinates,points.map(p=>[p.lon,p.lat]));
});
test('regional near-zero wave energy is drawn as missing, while global keeps explicit values',()=>{
 const points=[0.005,0.006,2].map((hs,i)=>point(140+i,hs));
 assert.deepEqual(voyageTrackFeatures(points,3).map(f=>f.properties.color),[missing]);
 assert.deepEqual(voyageTrackFeatures(points,3,{source:'global'}).map(f=>f.properties.color),[below]);
});

test('dateline unwrapping remains continuous across feature color boundaries',()=>{
 for(const longitudes of [[179,179.8,-179.8,-179],[-179,-179.8,179.8,179]]){
  const points=longitudes.map((lon,i)=>point(lon,[1,1,4,null][i]));
  const features=voyageTrackFeatures(points,3);
  assert.deepEqual(features.map(f=>f.properties.color),[below,above,missing]);
  for(let i=0;i<features.length;i++){
   const coordinates=features[i].geometry.coordinates;
   assert(Math.abs(coordinates[1][0]-coordinates[0][0])<1);
   assert(coordinates.every(([lon])=>Math.abs(lon)>=179));
   if(i)assert.deepEqual(features[i-1].geometry.coordinates.at(-1),coordinates[0]);
  }
 }
});

test('threshold equality stays below and repeated native vertices are preserved',()=>{
 const points=[point(130,3),point(130,3),point(131,2)];
 const features=voyageTrackFeatures(points,3);
 assert.equal(features.length,1);assert.equal(features[0].properties.color,below);
 assert.deepEqual(features[0].geometry.coordinates,[[130,30],[130,30],[131,30]]);
});

test('empty and single-position tracks do not invent line geometry',()=>{
 for(const points of [[],[point(130,1)],null,undefined])assert.deepEqual(voyageTrackFeatures(points,3),[]);
});
