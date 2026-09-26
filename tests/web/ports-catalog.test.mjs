import test from 'node:test';
import assert from 'node:assert/strict';
import {located,portCoordinates,portSearchPath,portListMarkup,portDetailMarkup,sourceMarkup,locatedCollection,mapBounds,latestTask,countryDisplay,countryOptionsMarkup,portQualityMarkup} from '../../web/public/console/ports-domain.js';

const port={id:'un:KRPUS',name:'Busan',aliases:['부산'],country_code:'KR',country_name:'Korea',unlocode:'KRPUS',wpi_id:61400,lat:35.1,lon:129.05,kind:'maritime',coordinate_source:'wpi',sources:['wpi']};
test('Korean country labels keep canonical country codes and official fallback',()=>{
 assert.equal(countryDisplay('KR','Korea'),'대한민국');
 assert.equal(countryDisplay('XZ','Offshore source region'),'Offshore source region');
 const countries=[{code:'KR',name:'Korea',count:77},{code:'NL',name:'Netherlands',count:167}];
 const html=countryOptionsMarkup(countries);
 assert(html.indexOf('네덜란드')<html.indexOf('대한민국'));
 assert.match(html,/value="KR">대한민국 · KR · 77/);
 assert.equal(countries[0].code,'KR');
 assert.match(portDetailMarkup(port,null),/대한민국 \/ Korea/);
});
test('both sides of an unresolved coordinate disagreement and deleted codes stay visible',()=>{
 const canonical=portQualityMarkup({...port,coordinate_conflict:{distance_km:6047.891,other_port_ids:['wpi:1']}});
 const wpi=portQualityMarkup({...port,merge_status:'coordinate_disagreement',source_coordinate_distance_km:6047.891});
 for(const html of [canonical,wpi]){assert.match(html,/원천 좌표 불일치/);assert.match(html,/6,047.9 km/);assert.match(html,/위치를 확인한 뒤/);}
 assert.match(portQualityMarkup({...port,unlocode_status:'deleted'}),/삭제 상태의 UN\/LOCODE/);
 for(const status of ['pending','not_listed'])assert.match(portQualityMarkup({...port,unlocode_status:status}),/현재 UN 목록에서 코드 확인 필요/);
 const list=portListMarkup([{...port,coordinate_conflict:{distance_km:6047.891},unlocode_status:'deleted'}],null);
 assert.match(list,/ports-row-quality/);assert.match(list,/원천 좌표 불일치/);assert.match(list,/삭제 상태 코드/);
 assert.match(portListMarkup([{...port,unlocode_status:'not_listed'}],null),/UN 코드 확인 필요/);
 assert(!portListMarkup([{...port,unlocode_status:'listed'}],null).includes('ports-row-quality'));
 assert.equal(portQualityMarkup({...port,source_coordinate_distance_km:2}), '');
});
test('worldwide queries omit the optional country while preserving Korean search and pagination',()=>{
 const all=new URL(portSearchPath({q:' 부산 ',offset:30}),'https://local.invalid');
 assert.equal(all.searchParams.has('country'),false);
 assert.equal(all.searchParams.get('q'),'부산');
 assert.equal(all.searchParams.get('offset'),'30');
 assert.equal(new URL(portSearchPath({country:'KR'}),'https://local.invalid').searchParams.get('country'),'KR');
});
test('missing, invalid and zero coordinates stay distinct',()=>{
 assert.equal(located({...port,lat:0,lon:0}),true);
 for(const coords of [{lat:null,lon:2},{lat:'',lon:2},{lat:91,lon:2},{lat:2,lon:181},{lat:NaN,lon:2}])assert.equal(located(coords),false);
 assert.match(portCoordinates({lat:-23.5,lon:-46.6}),/23\.5000°S 46\.6000°W/);
 const html=portDetailMarkup({...port,lat:null},null);
 assert.equal((html.match(/ disabled/g)||[]).length,4);
 assert.match(html,/위치를 임의로 추정하지 않습니다/);
});
test('external source text, IDs and links cannot become executable markup',()=>{
 const bad={...port,id:'" onclick="attack()',name:'<img src=x onerror=attack()>',aliases:['<script>attack()</script>']};
 const list=portListMarkup([bad],null),detail=portDetailMarkup(bad,{sources:[]});
 assert(!list.includes('<img'));assert(list.includes('&lt;img'));
 assert(!detail.includes('<script>'));assert(detail.includes('&lt;script&gt;'));
 const source=sourceMarkup({title:'<img>',url:'javascript:attack()',edition:'<script>'});
 assert(!source.includes('href='));assert(!source.includes('<img>'));
 assert.match(sourceMarkup({title:'NGA',url:'https://msi.nga.mil/Publications/WPI'}),/rel="noopener noreferrer"/);
});
test('detail keeps a reference position separate from a navigable harbor approach',()=>{
 const html=portDetailMarkup(port,{sources:[{id:'wpi',title:'NGA WPI',edition:'2026',url:'https://msi.nga.mil/Publications/WPI',sha256:'abcdef'}]});
 assert.match(html,/항만 입구·선석·검증된 항로점이 아니/);
 assert.match(html,/NGA WPI/);assert.match(html,/abcdef/);
 assert.match(html,/data-port-role="arrival"/);
 assert.equal((html.match(/ disabled/g)||[]).length,0);
});
test('map drops malformed records and keeps only located points',()=>{
 const collection=locatedCollection({features:[
  {geometry:{type:'Point',coordinates:[0,0]},properties:{id:'zero',name:'Null Island'}},
  {geometry:{type:'Point',coordinates:[181,0]},properties:{id:'bad'}},
  {geometry:{type:'Point',coordinates:[0,null]},properties:{id:'missing'}},
  {geometry:{type:'LineString',coordinates:[0,0]},properties:{id:'line'}},
 ]});
 assert.equal(collection.features.length,1);assert.deepEqual(collection.features[0].geometry.coordinates,[0,0]);
 assert.deepEqual(mapBounds(collection),[[0,0],[0,0]]);
 const dateline=locatedCollection({features:[179,-179].map(lon=>({geometry:{type:'Point',coordinates:[lon,10]},properties:{id:String(lon)}}))});
 assert.equal(mapBounds(dateline),null);
});
test('late search success and failure cannot replace a newer result',async()=>{
 const gate=latestTask(),out=[];
 let oldResolve,oldReject;
 const old=gate.run(()=>new Promise(resolve=>oldResolve=resolve),v=>out.push(v),e=>out.push(e.message));
 await gate.run(()=>Promise.resolve('new'),v=>out.push(v),e=>out.push(e.message));
 oldResolve('old');assert.equal(await old,false);assert.deepEqual(out,['new']);
 const stale=gate.run(()=>new Promise((resolve,reject)=>oldReject=reject),v=>out.push(v),e=>out.push(e.message));
 gate.invalidate();oldReject(new Error('stale failure'));
 assert.equal(await stale,false);assert.deepEqual(out,['new']);
 await gate.run(()=>Promise.reject(new Error('current failure')),v=>out.push(v),e=>out.push(e.message));
 assert.deepEqual(out,['new','current failure']);
});
