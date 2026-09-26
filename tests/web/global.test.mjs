import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeLongitude,unwrapRoute,coordinateLabel,GLOBAL_ROUTES} from '../../web/public/console/global-domain.js';
import {parseWaypoints,requestFromForm,voyageCSV} from '../../web/public/console/voyage-domain.js';
import {chartMarkup} from '../../web/public/console/charts.js';

test('global longitude labels and dateline display use the short crossing',()=>{
 assert.equal(normalizeLongitude(195),-165);
 assert.match(coordinateLabel(-20,-165),/20.000°S 165.000°W/);
 const route=unwrapRoute([{lat:35,lon:179},{lat:35,lon:-179},{lat:35,lon:-170}]);
 assert.deepEqual(route.map(p=>p.plot_lon),[179,181,190]);
 assert.deepEqual(unwrapRoute([{lat:0,lon:-179},{lat:0,lon:179}]).map(p=>p.plot_lon),[-179,-181]);
 assert.equal(parseWaypoints('89,0\n90,10').length,2);
});
test('global request carries explicit source and signed Pacific waypoints',()=>{
 const e=GLOBAL_ROUTES.pacific;
 const r=requestFromForm({name:e.name,coordinates:e.coordinates,departure:'2026-09-11T06:00',speed:e.speed,threshold:3,source:'global'});
 assert.equal(r.source,'global');assert.ok(r.waypoints.some(p=>p.lon<0));
});
test('primary wave directions are points, not a line through 180 degrees',()=>{
 const svg=chartMarkup([{lead_h:0,values:{primary_direction:359}},{lead_h:6,values:{primary_direction:1}}],'primary_direction',0,3);
 assert.match(svg,/circle/);assert.doesNotMatch(svg,/stroke-linejoin/);
});
test('global CSV separates primary quantities and surface wind from regional Tp/10m wind',()=>{
 const csv=voyageCSV({source:'global',engine:{provider:'NOAA'},request:{},screening:{status:'no_land_detected'},scenarios:[{points:[{values:{hs:2,tp:null,primary_period:10,primary_direction:359},wind:{speed_ms:5,reference_level:'surface'}}]}]});
 assert.match(csv,/primary_period_s/);assert.match(csv,/surface_wind_ms/);assert.match(csv,/"global","NOAA","10","359"/);
});
