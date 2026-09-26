import test from 'node:test';
import assert from 'node:assert/strict';
import {parseWaypoints} from '../../web/public/console/voyage-domain.js';
import {applyPortToRoute, attachPortReferences, importedPortReferences} from '../../web/public/console/voyage-ports-domain.js';

test('saved round trip retains repeated visits to one catalog port',()=>{
 const request={waypoints:[
  {lat:1,lon:2,port_id:'wpi:1',name:'Terminal A'},
  {lat:3,lon:4},
  {lat:1,lon:2,port_id:'wpi:1',name:'Terminal A'},
 ]};
 const points=request.waypoints.map(({lat,lon})=>({lat,lon}));
 const restored=attachPortReferences(points,importedPortReferences(request));
 assert.deepEqual(restored.map(p=>p.port_id??null),['wpi:1',null,'wpi:1']);
});

test('selecting arrival facility does not relabel a departure at the same representative coordinate',()=>{
 const a={id:'wpi:1',name:'Terminal A',lat:1,lon:2};
 const b={id:'wpi:2',name:'Terminal B',lat:1,lon:2};
 const departure=applyPortToRoute('8,9\n3,4\n6,7',[],a,'departure');
 const arrival=applyPortToRoute(departure.coordinates,departure.references,b,'arrival');
 const points=attachPortReferences(parseWaypoints(arrival.coordinates),arrival.references);
 assert.deepEqual(points.map(p=>p.port_id??null),['wpi:1',null,'wpi:2']);
});
