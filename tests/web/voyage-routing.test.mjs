import test from 'node:test';
import assert from 'node:assert/strict';
import {validateImportedWaypoints,voyageCSV} from '../../web/public/console/voyage-domain.js';

const planId='a'.repeat(64);
function savedRoute(count=40){
 const waypoints=Array.from({length:count},(_,i)=>({lat:20+i/100,lon:130+i/100}));
 return {request:{source:'global',route_plan_id:planId,waypoints:structuredClone(waypoints)},plan:{plan_id:planId,waypoints}};
}

for(const count of [40,67])test(`saved automatic route retains all ${count} native vertices on import`,()=>{
 const {request,plan}=savedRoute(count),before=structuredClone({request,plan});
 const coordinates=validateImportedWaypoints(request,plan);
 assert.equal(coordinates.split('\n').length,count);
 assert.deepEqual(coordinates.split('\n').map(line=>line.split(', ').map(Number)),request.waypoints.map(p=>[p.lat,p.lon]));
 assert.deepEqual({request,plan},before);
});

test('manual import retains the 20 point limit and cannot bypass missing plan lookup',()=>{
 for(const count of [2,20]){
  const {request}=savedRoute(count);delete request.route_plan_id;
  assert.equal(validateImportedWaypoints(request).split('\n').length,count);
 }
 for(const count of [21,40,67]){
  const {request}=savedRoute(count);delete request.route_plan_id;
  assert.throws(()=>validateImportedWaypoints(request),/20/);
 }
 assert.throws(()=>validateImportedWaypoints(savedRoute().request),/원본 항로 계획/);
});

test('saved route rejects reordered, changed, or differently sized geometry',()=>{
 for(const change of [
  r=>r.waypoints.reverse(),
  r=>r.waypoints[1].lat+=0.001,
  r=>r.waypoints[1].lon-=0.001,
  r=>r.waypoints.pop(),
  r=>r.waypoints.push({...r.waypoints.at(-1)}),
  r=>r.waypoints=[],
 ]){
  const {request,plan}=savedRoute();change(request);
  assert.throws(()=>validateImportedWaypoints(request,plan));
 }
 const {request,plan}=savedRoute();plan.waypoints.reverse();
 assert.throws(()=>validateImportedWaypoints(request,plan));
});

test('saved route rejects nonnumeric coordinates and malformed points without coercion',()=>{
 for(const value of ['20',null,undefined,NaN,Infinity,-Infinity,true]){
  for(const axis of ['lat','lon']){
   const {request,plan}=savedRoute();request.waypoints[0][axis]=value;
   assert.throws(()=>validateImportedWaypoints(request,plan));
  }
 }
 for(const points of [null,undefined,'20,130',{},[null],[false]]){
  const {request,plan}=savedRoute();request.waypoints=points;
  assert.throws(()=>validateImportedWaypoints(request,plan));
 }
});

test('saved route rejects port identity, source and plan identity mismatches',()=>{
 for(const value of ['unlocode:KRPUS','',0,false]){
  const {request,plan}=savedRoute();request.waypoints[0].port_id=value;
  assert.throws(()=>validateImportedWaypoints(request,plan));
 }
 for(const source of ['regional',undefined,null,'GLOBAL']){
  const {request,plan}=savedRoute();request.source=source;
  assert.throws(()=>validateImportedWaypoints(request,plan));
 }
 for(const id of ['b'.repeat(64),undefined,null,'']){
  const {request,plan}=savedRoute();request.route_plan_id=id;
  assert.throws(()=>validateImportedWaypoints(request,plan));
 }
});

// Parse quoted CSV independently so column assertions do not rely on the
// exporter implementation or fragile comma splitting of escaped JSON cells.
function csvRows(text){
 const rows=[];let row=[],field='',quoted=false;
 text=text.replace(/^\ufeff/,'');
 for(let i=0;i<text.length;i++){
  const c=text[i];
  if(c==='"'){
   if(quoted&&text[i+1]==='"'){field+='"';i++;}else quoted=!quoted;
  }else if(c===','&&!quoted){row.push(field);field='';}
  else if((c==='\n'||c==='\r')&&!quoted){
   if(c==='\r'&&text[i+1]==='\n')i++;
   row.push(field);rows.push(row);row=[];field='';
  }else field+=c;
 }
 assert.equal(quoted,false);
 row.push(field);rows.push(row);
 return rows;
}

function report(){
 return {analysis_id:'independent-route-review',created_at_utc:'2026-09-12T09:00:00Z',cycle:'20260912T00',
  source:'global',scope:'network_segment_only',route_scope:'obsolete-field-must-not-win',corrected:false,
  engine:{provider:'NOAA'},request:{hs_threshold_m:3},screening:{status:'no_land_detected'},
  route_plan:{plan_id:planId,network:{graph_sha256:'b'.repeat(64)},screening:{policy:'route-land-geodesic-2nm-v1'},
   departure:{gap_nm:12.345},arrival:{gap_nm:67.89}},
  scenarios:[{name:'=SUM(1,2)',status:'partial',speed_kn:16,departure_utc:'2026-09-12T09:00:00Z',
   arrival_utc:'2026-09-12T16:30:00Z',arrival_constraint:{status:'not_applicable'},points:[
    {lat:-30,lon:179.9,distance_nm:0,elapsed_h:0,values:{hs:2.5},missing:{tp:'missing, "provider"'},status:'ok'},
    {lat:-30,lon:-179.9,distance_nm:120,elapsed_h:7.5,values:{hs:null},missing:{hs:'outside_forecast_time'},status:'outside_forecast_time'},
   ]}]};
}

test('automatic voyage CSV maps route provenance and both gaps to their named columns',()=>{
 const csv=voyageCSV(report()),[header,...rows]=csvRows(csv);
 assert.equal(csv.charCodeAt(0),0xfeff);
 assert.equal(rows.length,2);
 assert.equal(new Set(header).size,header.length);
 const expected={route_plan_id:planId,route_scope:'network_segment_only',route_network_sha256:'b'.repeat(64),
  route_screening_policy:'route-land-geodesic-2nm-v1',departure_reference_gap_nm:'12.345',arrival_reference_gap_nm:'67.89'};
 for(const row of rows){
  assert.equal(row.length,header.length);
  const values=Object.fromEntries(header.map((name,i)=>[name,row[i]]));
  for(const [name,value] of Object.entries(expected))assert.equal(values[name],value,name);
  assert.equal(values.source,'global');assert.equal(values.arrival_status,'not_applicable');
  assert.equal(values.scenario,"'=SUM(1,2)");
 }
 assert.deepEqual(JSON.parse(rows[0][header.indexOf('missing_variables')]),{tp:'missing, "provider"'});
 assert.equal(rows[1][header.indexOf('hs_physics_m')],'');
 assert.equal(rows[1][header.indexOf('distance_nm')],'120');
});

test('manual voyage CSV leaves automatic-route provenance empty',()=>{
 const manual=report();delete manual.route_plan;delete manual.scope;delete manual.route_scope;
 const [header,...rows]=csvRows(voyageCSV(manual));
 for(const row of rows)for(const name of ['route_plan_id','route_scope','route_network_sha256','route_screening_policy','departure_reference_gap_nm','arrival_reference_gap_nm']){
  assert.equal(row[header.indexOf(name)],'',name);
 }
});
