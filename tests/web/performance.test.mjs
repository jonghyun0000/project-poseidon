import test from 'node:test';
import assert from 'node:assert/strict';
import {profileFromFields,SYNTHETIC_PROFILE,fuelLabel} from '../../web/public/console/performance-domain.js';
import {requestFromForm,voyageCSV} from '../../web/public/console/voyage-domain.js';

const fields={enabled:true,name:'Fixture',source:'Synthetic test data',kind:'synthetic_example',load:'Test only',fuel:'hfo',zeroCurrent:true,curve:'10, 12\n20, 36'};
test('fuel is opt-in and zero-current assumption is explicit',()=>{
 assert.equal(profileFromFields({enabled:false}),null);
 assert.throws(()=>profileFromFields({...fields,zeroCurrent:false}),/해류/);
 assert.equal(profileFromFields(fields).curve[1].fuel_t_day,36);
 assert.equal(SYNTHETIC_PROFILE.source_kind,'synthetic_example');
 assert.match(fuelLabel({status:'synthetic_example'}),/가상/);
 assert.equal(fuelLabel({status:'not_configured'}),'성능 자료 미입력');
});
test('curve validation rejects missing provenance, duplicate speeds and bad units',()=>{
 for(const edit of [{source:''},{load:''},{curve:'10,12'},{curve:'20,36\n10,12'},{curve:'10,12\n10,36'},
                    {curve:'10,12\n20,-1'},{curve:'10,12\n20,Infinity'},{curve:'10,12\n20,36,3'}]){
  assert.throws(()=>profileFromFields({...fields,...edit}));
 }
});
test('request and CSV preserve deadline, synthetic provenance, nulls and units',()=>{
 const request=requestFromForm({name:'Test',coordinates:'30,135\n29,135',departure:'2026-09-06T18:00',speed:14,threshold:3,compare:false,cycle:'20260906T18',deadline:'2026-09-07T00:00',performanceProfile:profileFromFields(fields)});
 assert.equal(request.arrival_deadline_utc,'2026-09-07T00:00Z');
 assert.equal(request.performance_profile.source_kind,'synthetic_example');
 const csv=voyageCSV({analysis_id:'a',request,screening:{status:'no_land_detected'},environment:{wind:{cycle:'20260906T18'}},scenarios:[{
  name:'Test',fuel:{status:'synthetic_example',source_kind:'synthetic_example',source:'=unsafe, "quoted"',fuel_t:12,co2_t:37.368,scope:'at-sea only'},
  arrival_constraint:{status:'late',deadline_utc:request.arrival_deadline_utc,margin_h:-1},
  points:[{values:{hs:null},wind:{status:'not_available',speed_ms:null}}]}]});
 assert.match(csv,/fuel_t_total/);assert.match(csv,/combustion_co2_t_total/);
 assert.match(csv,/synthetic_example/);assert.match(csv,/"'=unsafe, ""quoted"""/);
 assert.match(csv,/"not_available","","",""/);
 assert.match(csv,/"late","2026-09-07T00:00Z","-1"/);
});
