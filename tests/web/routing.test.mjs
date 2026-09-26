import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {AVOID_PASSAGES,MANDATORY_BLOCKED,portIssue,routingSearchPath,routingOptionsMarkup,routingRequest,validateRoutingPlan,routingResultMarkup} from '../../web/public/console/routing-domain.js';

const origin={id:'unlocode:KRPUS',name:'Busan',country_code:'KR',unlocode:'KRPUS',lat:35.1,lon:129.05};
const destination={id:'unlocode:SGSIN',name:'Singapore',country_code:'SG',unlocode:'SGSIN',lat:1.2,lon:103.8};
const request=routingRequest(origin,destination);
const plan=()=>({plan_id:'a'.repeat(64),status:'candidate',scope:'network_segment_only',request:{...request,avoid_passages:[]},constraints:{blocked_passages:[...MANDATORY_BLOCKED],used_passages:[]},created_at_utc:'2026-09-12T09:00:00Z',distance_nm:2200,
 waypoints:[{lat:34.5,lon:129.1},{lat:1.0,lon:103.8}],
 departure:{port:origin,attachment:{lat:34.5,lon:129.1,node_id:1},gap_nm:36.1,scope:'reference_gap_not_navigated'},
 arrival:{port:destination,attachment:{lat:1,lon:103.8,node_id:2},gap_nm:12,scope:'reference_gap_not_navigated'},
 network:{source_name:'Eurostat SeaRoute / MARNET',source_url:'https://github.com/eurostat/searoute',source_gpkg_last_change:'2021-09-08T13:51:23.453Z',source_commit_date:'2022-01-10T09:56:46Z'},limitations:['항만 접근은 검증하지 않습니다.']});

test('only selected, located, nonconflicting ports form a global route request',()=>{
 assert.deepEqual(routingRequest(origin,destination,['gibraltar','malacca','gibraltar']),{
  departure_port_id:origin.id,arrival_port_id:destination.id,avoid_passages:['gibraltar','malacca'],
 });
 for(const port of [null,{...origin,lat:null},{...origin,lat:'35.1'},{...origin,lat:NaN},{...origin,lon:181},{...origin,coordinate_status:'conflict'},{...origin,coordinate_conflict:{distance_km:30}},{...origin,merge_status:'coordinate_disagreement'}]){
  assert(portIssue(port));assert.throws(()=>routingRequest(port,destination));
 }
 assert.throws(()=>routingRequest(origin,origin),/서로 다르게/);
 assert.throws(()=>routingRequest(origin,destination,[],'regional'),/전 세계/);
 assert.throws(()=>routingRequest(origin,destination,['suez']),/회피/);
 assert.throws(()=>routingRequest(origin,destination,['unrecognized']),/회피/);
 assert.deepEqual(AVOID_PASSAGES.map(([id])=>id),['malacca','gibraltar','babelmandeb','dover','bering','magellan']);
});

test('port autocomplete queries encode Korean input and distinguish unusable results',()=>{
 const url=new URL(routingSearchPath(' 부산 '),'https://example.invalid');
 assert.equal(url.searchParams.get('q'),'부산');assert.equal(url.searchParams.get('limit'),'8');
 assert(!url.searchParams.has('country'));
 const markup=routingOptionsMarkup([origin,{...destination,coordinate_conflict:{distance_km:44}}],'departure',0);
 assert.match(markup,/대한민국/);assert.match(markup,/KRPUS/);assert.match(markup,/aria-selected="true"/);
 assert.match(markup,/aria-disabled="true"/);assert.match(markup,/원천 좌표가 불일치/);
 assert.match(routingOptionsMarkup([],'arrival'),/검색 결과가 없습니다/);
});

test('a route result must match both selected ports and explicitly exclude unverified gaps',()=>{
 assert.equal(validateRoutingPlan(plan(),request).plan_id,'a'.repeat(64));
 for(const change of [
  p=>p.status='blocked',p=>p.plan_id=null,p=>p.waypoints=[],p=>p.waypoints[0].lon=Infinity,
  p=>p.distance_nm=null,p=>p.distance_nm=0,p=>p.departure.port={...origin,id:destination.id},
  p=>p.arrival.attachment.lat=null,p=>p.departure.gap_nm=-1,p=>p.arrival.scope='navigated',
  p=>p.scope='full_port_voyage',p=>p.plan_id='not-content-addressed',p=>p.request.avoid_passages=['malacca'],
  p=>p.constraints.blocked_passages=[],p=>p.constraints.used_passages=['suez'],
 ]){const changed=plan();change(changed);assert.throws(()=>validateRoutingPlan(changed,request));}
});

test('route result labels both network connections and never presents the gaps as sailed mileage',()=>{
 const markup=routingResultMarkup(plan());
 assert.equal((markup.match(/<span>항로망 접속점/g)||[]).length,2);
 assert.match(markup,/36\.1 nm/);assert.match(markup,/12\.0 nm/);
 assert.match(markup,/항해 거리·시간 계산에서 제외/);
 assert.match(markup,/항로망 계산 결과 · 후보/);
 assert.match(markup,/Eurostat SeaRoute \/ MARNET/);assert.match(markup,/항로망 원천 2021-09-08/);
 assert.match(markup,/원천 저장소 판본 2022-01-10/);
 assert(!markup.includes('입항점'));
});

test('untrusted port and provider text remains inert in autocomplete and route summaries',()=>{
 const malicious={...origin,id:'" onclick="attack()',name:'<img onerror=attack()>',unlocode:'<script>'};
 const options=routingOptionsMarkup([malicious],'departure');
 assert(!options.includes('<img'));assert(options.includes('&lt;img'));
 const result=plan();result.departure.port=malicious;result.network.source_name='<script>attack()</script>';
 result.limitations=['<img onerror=attack()>'];
 const markup=routingResultMarkup(result);
 assert(!markup.includes('<script>'));assert(!markup.includes('<img'));
 assert(markup.includes('&lt;script&gt;'));
});

test('route controls are non-submit controls within the existing voyage form',async()=>{
 const html=await readFile(new URL('../../web/public/console/index.html',import.meta.url),'utf8');
 const section=html.slice(html.indexOf('<section id="routing-section"'),html.indexOf('<div class="section-head"><h2>웨이포인트'));
 assert(section.length>0);assert(!/<form\b/.test(section));
 assert.equal((section.match(/<button[^>]+type="button"/g)||[]).length,3);
 assert(!/\brequired\b/.test(section));
 assert.match(section,/role="combobox"/);assert.match(section,/aria-controls="routing-departure-results"/);
});
