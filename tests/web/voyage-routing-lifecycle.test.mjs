import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {initVoyage} from '../../web/public/console/voyage.js';
import {MANDATORY_BLOCKED,AVOID_PASSAGES} from '../../web/public/console/routing-domain.js';

const html=await readFile(new URL('../../web/public/console/index.html',import.meta.url),'utf8');
const inputIds=[...html.matchAll(/<(?:input|textarea|select)[^>]*\bid="([^"]+)"/g)].map(match=>match[1]);
const origin={id:'unlocode:KRPUS',name:'Busan',lat:35.1,lon:129.05};
const arrival={id:'unlocode:SGSIN',name:'Singapore',lat:1.2,lon:103.8};
function makePlan(){return {plan_id:'a'.repeat(64),status:'candidate',scope:'network_segment_only',distance_nm:2200,
 request:{departure_port_id:origin.id,arrival_port_id:arrival.id,avoid_passages:[]},
 constraints:{blocked_passages:[...MANDATORY_BLOCKED],used_passages:[]},created_at_utc:'2026-09-12T09:00:00Z',
 waypoints:[{lat:34.5,lon:129.1},{lat:1,lon:103.8}],
 departure:{port:origin,attachment:{lat:34.5,lon:129.1},gap_nm:36,scope:'reference_gap_not_navigated'},
 arrival:{port:arrival,attachment:{lat:1,lon:103.8},gap_nm:12,scope:'reference_gap_not_navigated'},
 network:{source_name:'Fixture network',source_gpkg_last_change:'2021-09-08T13:51:23.453Z'},limitations:[]};}

function harness(t,{saved=null,selectPorts=true}={}){
 const previous=Object.fromEntries(['document','localStorage','maplibregl','ResizeObserver'].map(key=>[key,globalThis[key]]));
 t.after(()=>{for(const [key,value] of Object.entries(previous)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}});
 const elements=new Map(),pending=[],storage=new Map(),markers=[];
 if(saved)storage.set('poseidon.voyage.form.global',JSON.stringify(saved));
 class Element {
  constructor(id){this.id=id;this.value='';this.checked=false;this.hidden=false;this.textContent='';this.innerHTML='';this.attributes={};this.listeners={};this.classList={toggle(){},add(){}};}
  setAttribute(key,value){this.attributes[key]=value;}
  removeAttribute(key){delete this.attributes[key];}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  replaceChildren(){this.innerHTML='';}
  append(){}
  querySelectorAll(selector){
   if(this.id==='voy-form'&&selector==='input,textarea,select')return inputIds.map(element);
   if(this.id==='routing-avoid')return selector==='input'?avoidInputs:selector==='input:checked'?avoidInputs.filter(input=>input.checked):[];
   return [];
  }
  closest(selector){return selector==='#routing-section'&&this.id.startsWith('routing-')?element('routing-section'):null;}
  focus(){}
 }
 const element=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
 const avoidInputs=AVOID_PASSAGES.map(([value])=>({value,checked:false}));
 let map;
 class FakeMap {
  constructor(){map=this;this.sources=new Map();this.events={};}
  on(event,listener){this.events[event]=listener;}
  addControl(){}
  addSource(id,entry){this.sources.set(id,{data:entry.data,setData(data){this.data=data;}});}
  getSource(id){return this.sources.get(id);}
  addLayer(){}
  getCanvas(){return {style:{}};}
  fitBounds(){}
  resize(){}
 }
 class Marker {
  constructor(){this.active=false;markers.push(this);}
  setLngLat(){return this;}
  setRotation(){return this;}
  addTo(){this.active=true;return this;}
  remove(){this.active=false;return this;}
 }
 globalThis.document={getElementById:element,createElement:tag=>new Element(tag)};
 globalThis.localStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)};
 globalThis.maplibregl={Map:FakeMap,Marker,NavigationControl:class{},LngLatBounds:class{extend(){return this;}}};
 globalThis.ResizeObserver=class{observe(){}};
 const voyage=initVoyage({api:(path,options)=>new Promise((resolve,reject)=>pending.push({path,options,resolve,reject})),getMeta:()=>null,getSource:()=> 'global'});
 voyage.activate();map.events.load();
 const changeCoordinates=value=>{element('voy-waypoints').value=value;for(const listener of element('voy-waypoints').listeners.input||[])listener({target:element('voy-waypoints')});};
 if(selectPorts){voyage.usePort(origin,'departure');voyage.usePort(arrival,'arrival');}
 return {voyage,element,pending,map,markers,storage,changeCoordinates};
}

test('editing manual waypoints while automatic calculation is pending preserves the edit after its response',async t=>{
 const h=harness(t),plan=makePlan();
 const calculation=h.element('routing-calculate').onclick();
 assert.equal(h.pending.length,1);assert.equal(h.pending[0].path,'/v1/routing/plan');
 h.changeCoordinates('10,20\n11,21');
 h.pending[0].resolve(plan);await calculation;
 assert.equal(h.element('voy-waypoints').value,'10,20\n11,21');
 assert.equal(h.element('voy-waypoints').readOnly,false);
 assert.equal(h.element('routing-result').hidden,true);
 assert.equal(h.element('routing-clear').hidden,true);
 assert.equal(JSON.parse(h.storage.get('poseidon.voyage.form.global')).route_plan_id,null);
 assert.deepEqual(h.map.getSource('voy-route').data.features[0].geometry.coordinates,[[20,10],[21,11]]);
});

test('clearing an automatic route restores an invalid manual draft without leaving route lines or markers',async t=>{
 const h=harness(t),plan=makePlan();
 h.changeCoordinates('not coordinates');
 const calculation=h.element('routing-calculate').onclick();h.pending[0].resolve(plan);await calculation;
 assert.equal(h.element('voy-waypoints').readOnly,true);
 assert.equal(h.map.getSource('voy-route').data.features.length,1);
 assert.equal(h.map.getSource('voy-port-gaps').data.features.length,2);
 assert.equal(h.markers.filter(marker=>marker.active).length,4);
 h.element('routing-clear').onclick();
 assert.equal(h.element('voy-waypoints').value,'not coordinates');
 assert.equal(h.element('voy-waypoints').readOnly,false);
 assert.equal(h.element('routing-result').hidden,true);
 assert.deepEqual(h.map.getSource('voy-route').data.features,[]);
 assert.deepEqual(h.map.getSource('voy-port-gaps').data.features,[]);
 assert.equal(h.markers.filter(marker=>marker.active).length,0);
 assert.equal(JSON.parse(h.storage.get('poseidon.voyage.form.global')).route_plan_id,null);
});

test('editing a port picker while a saved plan is loading prevents that plan from overwriting the new input',async t=>{
 const plan=makePlan(),coordinates='35,129\n1,103';
 const h=harness(t,{saved:{name:'Saved draft',coordinates,route_plan_id:plan.plan_id},selectPorts:false});
 assert.equal(h.pending.length,1);assert.equal(h.pending[0].path,'/v1/routing/plans/'+plan.plan_id);
 const input=h.element('routing-arrival'),section=h.element('routing-section');
 input.value='Rotterdam';
 for(const listener of input.listeners.input||[])listener({target:input,currentTarget:input});
 for(const listener of section.listeners.input||[])listener({target:input,currentTarget:section});
 // Escape closes only the autocomplete request; it does not change the plan selection.
 for(const listener of input.listeners.keydown||[])listener({key:'Escape',preventDefault(){}});
 h.pending[0].resolve(plan);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(input.value,'Rotterdam');
 assert.equal(h.element('voy-waypoints').value,coordinates);
 assert.equal(h.element('voy-waypoints').readOnly,false);
 assert.notEqual(h.element('routing-result').hidden,false);
 assert.equal(h.map.getSource('voy-port-gaps').data.features.length,0);
});
