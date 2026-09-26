import test from 'node:test';
import assert from 'node:assert/strict';
import {applyPortToRoute,attachPortReferences,importedPortReferences} from '../../web/public/console/voyage-ports-domain.js';
import {voyageCSV} from '../../web/public/console/voyage-domain.js';

const port={id:'unlocode:KRPUS',name:'Busan',unlocode:'KRPUS',lat:35.1,lon:129.04};
test('departure and arrival replace endpoints while via preserves them',()=>{
 assert.equal(applyPortToRoute('30, 135\n31, 136\n32, 137',[],port,'departure').coordinates,'35.1, 129.04\n31, 136\n32, 137');
 assert.equal(applyPortToRoute('30, 135\n32, 137',[],port,'arrival').coordinates,'30, 135\n35.1, 129.04');
 assert.equal(applyPortToRoute('30, 135\n32, 137',[],port,'via').coordinates,'30, 135\n35.1, 129.04\n32, 137');
 assert.equal(applyPortToRoute('',[],port,'departure').coordinates,'35.1, 129.04');
 assert.throws(()=>applyPortToRoute('35.1,129.04\n32,137',[],port,'arrival'),/같은/);
 assert.throws(()=>applyPortToRoute('bad input',[],port,'departure'));
 assert.throws(()=>applyPortToRoute('30,135\n31,136',[],{...port,lat:null},'departure'));
 const twenty=Array.from({length:20},(_,i)=>`${i},135`).join('\n');
 assert.throws(()=>applyPortToRoute(twenty,[],port,'via'),/20/);
});
test('edited coordinates lose port identity; reordered coordinates retain it; ambiguity is not guessed',()=>{
 const points=[{lat:35.1,lon:129.04},{lat:30,lon:135}];
 assert.equal(attachPortReferences(points,[port])[0].port_id,'unlocode:KRPUS');
 assert.equal(attachPortReferences([{lat:35.101,lon:129.04}], [port])[0].port_id,undefined);
 assert.equal(attachPortReferences(points.toReversed(),[port])[1].port_id,port.id);
 assert.equal(attachPortReferences(points,[port,{...port,id:'wpi:1'}])[0].port_id,undefined);
 assert.deepEqual(attachPortReferences(points,null),points);
});
test('saved report imports port IDs and CSV ties waypoint identities to catalog checksum',()=>{
 const request={waypoints:[{lat:port.lat,lon:port.lon,port_id:port.id,name:port.name}],hs_threshold_m:3};
 assert.equal(importedPortReferences(request)[0].id,port.id);
 const report={request,port_references:[{waypoint_index:0,port_id:port.id,catalog_sha256:'abc'}],screening:{},scenarios:[{points:[{values:{}}]}]};
 assert.match(voyageCSV(report),/waypoint_port_ids/);
 assert.match(voyageCSV(report),/"0:unlocode:KRPUS","abc"/);
});
