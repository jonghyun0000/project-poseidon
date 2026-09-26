import test from 'node:test';
import assert from 'node:assert/strict';
import {parseWaypoints,requestFromForm,voyageCSV} from '../../web/public/console/voyage-domain.js';

test('waypoint input rejects malformed, missing and out of range positions',()=>{
 assert.deepEqual(parseWaypoints('30, 135\n29 134'),[{lat:30,lon:135},{lat:29,lon:134}]);
 for(const input of ['30,135','30,135\nx,134','30,135\n91,0','30,135\n,0','30,135\n29,134,4'])assert.throws(()=>parseWaypoints(input));
});
test('scenario inputs retain explicit UTC and SOG assumptions',()=>{
 const r=requestFromForm({name:'test',coordinates:'30,135\n29,134',departure:'2026-09-06T18:00',speed:'14',threshold:'3',compare:true,cycle:'20260906T18'});
 assert.equal(r.departure_utc,'2026-09-06T18:00Z');
 assert.deepEqual(r.scenarios.map(s=>[s.speed_kn,s.departure_offset_h]),[[14,0],[14,6],[12,0]]);
 assert.throws(()=>requestFromForm({...r,departure:'',speed:14,threshold:3}));
});
test('CSV protects user text from formulas, preserves missingness and provenance',()=>{
 const r={analysis_id:'id',created_at_utc:'2026-09-12T00:00:00Z',cycle:'20260906T18',request:{hs_threshold_m:3},corrected:false,screening:{status:'unavailable'},scenarios:[{name:'=1+2',status:'partial',speed_kn:14,points:[{values:{hs:null,tp:null},status:'outside_forecast_time',missing:{}}]}]};
 const csv=voyageCSV(r);
 assert.match(csv,/"'=1\+2"/);
 assert.match(csv,/outside_forecast_time/);
 assert.match(csv,/"unavailable","false"/);
 assert.match(csv,/"","","","","",""/);
});
