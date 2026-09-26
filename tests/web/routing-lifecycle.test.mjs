import test from 'node:test';
import assert from 'node:assert/strict';
import {initRouting} from '../../web/public/console/routing.js';
import {MANDATORY_BLOCKED,AVOID_PASSAGES} from '../../web/public/console/routing-domain.js';

class Element {
 constructor(){this.value='';this.hidden=false;this.textContent='';this.innerHTML='';this.attributes={};this.listeners={};this.classList={toggle(){},add(){}};}
 setAttribute(key,value){this.attributes[key]=value;}
 removeAttribute(key){delete this.attributes[key];}
 replaceChildren(){this.innerHTML='';}
 querySelectorAll(selector){return selector==='input'?(this.inputs||[]):selector==='input:checked'?(this.inputs||[]).filter(input=>input.checked):[];}
 addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
 focus(){}
}
const origin={id:'unlocode:KRPUS',name:'Busan',lat:35.1,lon:129.05};
const arrival={id:'unlocode:SGSIN',name:'Singapore',lat:1.2,lon:103.8};
function makePlan(destination=arrival,avoid=[]){return {plan_id:'a'.repeat(64),status:'candidate',scope:'network_segment_only',distance_nm:2200,
 request:{departure_port_id:origin.id,arrival_port_id:destination.id,avoid_passages:avoid},constraints:{blocked_passages:[...MANDATORY_BLOCKED,...avoid],used_passages:[]},
 created_at_utc:'2026-09-12T09:00:00Z',waypoints:[{lat:34.5,lon:129.1},{lat:1,lon:103.8}],
 departure:{port:origin,attachment:{lat:34.5,lon:129.1},gap_nm:36,scope:'reference_gap_not_navigated'},
 arrival:{port:destination,attachment:{lat:1,lon:103.8},gap_nm:12,scope:'reference_gap_not_navigated'},
 network:{source:'test',version:'1'},limitations:[]};}

test('port edits and source changes suppress stale route success and failure',async()=>{
 const previous=globalThis.document,elements=new Map(),plans=[],clears=[],pending=[];
 const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 globalThis.document={getElementById:element};
 let source='global';
 try{
  const routing=initRouting({getSource:()=>source,onPlan:p=>plans.push(p),onClear:()=>clears.push(true),
   api:(path,options)=>new Promise((resolve,reject)=>pending.push({resolve,reject,path,request:JSON.parse(options.body)}))});
  routing.setPort(origin,'departure');routing.setPort(arrival,'arrival');
  assert.equal(element('routing-calculate').disabled,false);
  const first=element('routing-calculate').onclick();
  assert.equal(element('routing-calculate').disabled,true);
  const changed={...arrival,id:'unlocode:NLRTM',name:'Rotterdam'};
  routing.setPort(changed,'arrival');pending[0].resolve(makePlan());await first;
  assert.equal(plans.length,0);assert.equal(clears.length,1);
  assert.equal(element('routing-result').hidden,true);assert.equal(element('routing-calculate').disabled,false);
  const second=element('routing-calculate').onclick();
  source='regional';routing.sourceChanged();pending[1].reject(new Error('old failure'));await second;
  assert.equal(element('routing-error').textContent,'');assert.equal(element('routing-calculate').disabled,true);
  assert.equal(element('routing-regional-note').hidden,false);
  source='global';routing.sourceChanged();
  const third=element('routing-calculate').onclick();pending[2].resolve(makePlan(changed));await third;
  assert.equal(plans.length,1);assert.equal(plans[0].arrival.port.id,changed.id);
  assert.equal(element('routing-result').hidden,false);
  routing.clear();assert.equal(element('routing-result').hidden,true);assert.equal(element('routing-clear').hidden,true);
  assert.match(element('routing-arrival').value,/Rotterdam/);
 }finally{globalThis.document=previous;}
});

test('stored plans restore both port selectors and avoidance without callback recursion',async()=>{
 const previous=globalThis.document,elements=new Map(),plans=[],clears=[];
 const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 globalThis.document={getElementById:element};
 const inputs=AVOID_PASSAGES.map(([value])=>({value,checked:false}));element('routing-avoid').inputs=inputs;
 let source='global',routing,resolveOld;
 try{
  routing=initRouting({getSource:()=>source,onPlan:p=>{plans.push(p);routing.restore(p);},onClear:()=>clears.push(true),
   api:()=>new Promise(resolve=>resolveOld=resolve)});
  routing.setPort(origin,'departure');routing.setPort(arrival,'arrival');
  const pending=element('routing-calculate').onclick();
  const restored=makePlan(arrival,['magellan']);routing.restore(restored);
  assert.equal(plans.length,0);assert.equal(clears.length,0);assert.match(element('routing-departure').value,/Busan/);
  assert.match(element('routing-arrival').value,/Singapore/);assert.equal(element('routing-result').hidden,false);
  assert.deepEqual(inputs.filter(input=>input.checked).map(input=>input.value),['magellan']);
  resolveOld(makePlan());await pending;assert.equal(plans.length,0);assert.equal(element('routing-result').hidden,false);
  source='regional';routing.sourceChanged({notify:false});assert.equal(clears.length,0);assert.equal(element('routing-calculate').disabled,true);
  source='global';routing.sourceChanged({notify:false});
  const again=element('routing-calculate').onclick();resolveOld(restored);await again;
  assert.equal(plans.length,1);assert.equal(clears.length,0);assert.equal(element('routing-result').hidden,false);
  assert.equal(element('routing-clear').hidden,false);
 }finally{globalThis.document=previous;}
});

test('a failed plan can retry without a stale path being applied',async()=>{
 const previous=globalThis.document,elements=new Map(),plans=[];
 const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 globalThis.document={getElementById:element};
 let attempts=0;
 try{
  const routing=initRouting({onPlan:plan=>plans.push(plan),onClear(){},api:async()=>{
   if(++attempts===1)throw new Error('연결 가능한 항로 없음');return makePlan();
  }});
  routing.setPort(origin,'departure');routing.setPort(arrival,'arrival');
  await element('routing-calculate').onclick();
  assert.equal(element('routing-retry').hidden,false);assert.equal(element('routing-result').hidden,true);assert.equal(plans.length,0);
  await element('routing-retry').onclick();
  assert.equal(element('routing-retry').hidden,true);assert.equal(element('routing-error').textContent,'');assert.equal(plans.length,1);
 }finally{globalThis.document=previous;}
});
