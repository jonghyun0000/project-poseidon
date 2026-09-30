import {esc} from './domain.js';
import {SHIPS,shipById,SHIP_CATALOG_VERSION} from './ships.js';
import {initialState,step,hullFeatures,trailGeo,sessionDocument,clamp,wrap} from './helm-domain.js?v=33.3';
import {coastWindow,coastCovers,harborObstacles,collision} from './helm-navigation.js?v=33.3';
import {countryOptionsMarkup,located,latestTask} from './ports-domain.js';
import {harborMapURL,harborFeatures} from './harbor-domain.js?v=32.1';

const empty=()=>({type:'FeatureCollection',features:[]});
export function initHelm({api}){
 const root=document.getElementById('helm-view');
 root.innerHTML=`
 <aside class="helm-fleet"><div class="section-label">REFERENCE FLEET · 12 TYPES</div><h1>선박 조종</h1><p class="helm-muted">실선·건조 시리즈 제원 기반<br>직접 조종하고 항적을 비교하세요.</p><div id="helm-fleet-list"></div></aside>
 <section class="helm-center">
  <header class="helm-ship-header"><div><span id="helm-type"></span><h2 id="helm-name"></h2></div><div id="helm-size"></div><button id="helm-source" type="button">제원 출처</button></header>
  <div class="helm-map-stage"><div id="helm-map" aria-label="선박 조종 지도"></div>
   <div class="helm-map-tools"><button id="helm-follow">선박 따라가기</button><button id="helm-camera">입체 보기</button><button id="helm-place">시작 위치 선택</button></div>
   <div class="helm-hud"><div><small>선수방위</small><strong id="helm-hdg">090.0°</strong></div><div><small>전후진 속력</small><strong id="helm-speed">0.0 kn</strong></div><div><small>회두율</small><strong id="helm-yaw">0.0 °/min</strong></div></div>
   <div id="helm-map-message" class="helm-map-message" role="status">부산 앞바다 · 지도 위 선박은 사용자 조종 모형입니다.</div>
   <div id="helm-tile-error" class="helm-tile-error" hidden>배경 지도 수신 실패 · 위치 판단에 사용할 수 없습니다.</div>
   <div class="helm-map-caption">선체 길이·폭: 실축척 / 상부 구조: 개념 모형</div>
  </div>
  <div class="helm-console">
   <div class="helm-control"><label for="helm-throttle">기관 명령 <output id="helm-throttle-label">중립</output></label><input id="helm-throttle" type="range" min="-50" max="100" step="5" value="0"><div class="helm-control-buttons"><button data-throttle="-30">후진</button><button data-throttle="0">중립</button><button data-throttle="35">미속</button><button data-throttle="100">전진</button></div></div>
   <div class="helm-control"><label for="helm-rudder">조타 명령 <output id="helm-rudder-label">0°</output></label><input id="helm-rudder" type="range" min="-35" max="35" value="0"><div class="helm-control-buttons"><button data-rudder="-35">좌현 35°</button><button data-rudder="0">타 중앙</button><button data-rudder="35">우현 35°</button></div></div>
   <div class="helm-transport"><button id="helm-run" class="helm-primary">조종 시작</button><label>시간 배속 <select id="helm-rate" aria-label="시간 배속"><option value="1">1×</option><option value="5">5×</option><option value="20">20×</option></select></label><button id="helm-reset">시작점으로 초기화</button><button id="helm-export">항해 기록 저장</button></div>
  </div>
  <div class="helm-session-line"><span id="helm-session">00:00 · 0.000 nm</span><span id="helm-position"></span><span>W/S 기관 · A/D 조타 · X 중립 · C 타 중앙 · Space 일시정지</span></div>
  <details class="helm-method"><summary>조종 모형의 적용 범위</summary><p>선박 길이·폭은 공개 제원입니다. 가속·감속·회두·후진 속력은 조작용 가정이며 실선 시운전으로 보정되지 않았습니다. 예인선의 아지무스 추진도 공통 조타 입력으로 단순화했습니다. Natural Earth 1:10m 육지와 불러온 OSM 부두·방파제의 형상 교차를 화면상 정지 조건으로 씁니다. 작은 섬·정확한 해안선·수심·항해 장애물·다른 선박은 검증되지 않았습니다. 파랑 힘·해류·접안·연료는 계산하지 않습니다. 일시정지는 시간 정지이며 실제 비상 정지가 아닙니다. 공식 항해용 해도가 아닙니다.</p><p id="helm-handling"></p><a id="helm-reference" target="_blank" rel="noopener noreferrer">공식 선박 제원 열기 ↗</a></details>
 </section>
 <aside class="helm-harbor"><div class="section-label">WORLD HARBORS</div><h2>국가별 실제 항구</h2><p id="helm-coverage" class="helm-muted">항구 카탈로그 확인 중</p>
  <label for="helm-country">국가·지역</label><select id="helm-country"><option value="">전 세계</option></select>
  <form id="helm-search"><label for="helm-query">항구명 또는 UN/LOCODE</label><div class="helm-search-row"><input id="helm-query" placeholder="부산, Singapore, NLRTM"><button>검색</button></div></form>
  <p id="helm-search-status" class="helm-muted" role="status"></p><div id="helm-port-list"></div><div class="helm-pages"><button id="helm-prev">이전</button><button id="helm-next">다음</button></div>
  <div id="helm-port-detail"><p class="helm-muted">항구 선택 → 지도에서 시작 위치 선택 → 조종 시작</p></div>
  <button id="helm-facilities" disabled>이 지도 구역의 실제 시설</button><p id="helm-facility-status" class="helm-muted" role="status">지도를 원하는 부두로 이동한 뒤 누르세요. 화면 중심 주변 OSM 부두·방파제·건물을 표시합니다.</p>
  <button id="helm-weather">선박 위치 파랑 예보 조회</button><p id="helm-weather-status" class="helm-muted">환경 예보는 참고 표시이며 조종 모형에 힘으로 적용되지 않습니다.</p>
 </aside>`;
 const $=id=>document.getElementById('helm-'+id);
 let vessel=SHIPS[0],state=initialState(),origin={...state},samples=[{...state}],port=null;
 let map=null,ready=false,running=false,active=false,placing=false,following=true,frame=null,last=0,lastDraw=0;
 let command={throttle:0,rudder:0},elapsedSample=0,offset=0,total=0,searchTimer,metaLoaded=false,facilityAbort=null;
 const searchGate=latestTask(),detailGate=latestTask(),weatherGate=latestTask();
 let facilityRevision=0,weatherRevision=0,runRevision=0,coastRevision=0,placeRevision=0,coast=null,facilityGeo=empty(),facilityShapes=[];
 function message(value){$('map-message').textContent=value;}
 function inputs(){
  $('throttle').value=command.throttle;$('rudder').value=command.rudder;
  $('throttle-label').textContent=command.throttle===0?'중립':`${command.throttle>0?'전진':'후진'} ${Math.abs(command.throttle)}%`;
  $('rudder-label').textContent=`${command.rudder<0?'좌현':command.rudder>0?'우현':''} ${Math.abs(command.rudder)}°`;
 }
 function logSample(){samples.push({...state,...command});if(samples.length>12000)samples.shift();}
 function render(){
  $('hdg').textContent=state.heading.toFixed(1)+'°';$('speed').textContent=state.speed.toFixed(2)+' kn';
  $('yaw').textContent=(state.yaw*60).toFixed(1)+' °/min';
  $('session').textContent=`${Math.floor(state.elapsed/60).toString().padStart(2,'0')}:${Math.floor(state.elapsed%60).toString().padStart(2,'0')} · ${(state.distance/1852).toFixed(3)} nm · 기록 ${samples.length.toLocaleString()}점`;
  $('position').textContent=`${state.lat.toFixed(5)}°, ${state.lon.toFixed(5)}°`;
  if(ready){map.getSource('helm-ship').setData(hullFeatures(state,vessel));map.getSource('helm-track').setData(trailGeo(samples));if(following)map.jumpTo({center:[state.lon,state.lat]});}
 }
 function pause(){
  runRevision++;
  running=false;cancelAnimationFrame(frame);frame=null;last=0;$('run').textContent=state.elapsed?'조종 계속':'조종 시작';$('run').classList.remove('running');
 }
 async function loadCoast(s){
  const revision=++coastRevision;
  const raw=await api('/v1/helm/coast?'+new URLSearchParams({lat:s.lat,lon:s.lon}));
  if(revision!==coastRevision)return false;
  coast=coastWindow(raw);facilityShapes=harborObstacles(facilityGeo,coast.center);
  return coastCovers(s,vessel,coast);
 }
 async function startRun(){
  if(placing){message('지도에서 새 시작 위치를 먼저 선택하거나 시작 위치 선택을 취소하세요.');return;}
  if(!ready){message('지도 준비 후 조종을 시작할 수 있습니다.');return;}
  const revision=++runRevision;
  if(!coastCovers(state,vessel,coast)){
   message('Natural Earth 해안선 형상을 불러오는 중 · 자료가 준비될 때까지 조종을 멈춥니다.');
   try{if(!await loadCoast(state))throw Error('해안선 형상 범위 밖');}
   catch(e){if(revision===runRevision)message('해안선 판정 불가 · 조종을 시작할 수 없습니다. '+e.message);return;}
  }
  if(revision!==runRevision||!active)return;
  const hit=collision(state,state,vessel,coast,facilityShapes);
  if(hit){message(hit==='land'?'선체가 지도 축척 육지와 겹칩니다. 수면에 새 시작 위치를 선택하세요.':hit==='facility'?'선체가 불러온 항만 시설과 겹칩니다. 시작 위치를 옮기세요.':'해안선 판정 범위 밖 · 시작할 수 없습니다.');return;}
  placing=false;$('place').classList.remove('selected');running=true;last=0;following=true;$('run').textContent='일시정지';$('run').classList.add('running');
  message('조종 중 · Natural Earth 1:10m 육지 판정 적용 · 공식 해도·수심 판정 아님');frame=requestAnimationFrame(tick);
 }
 function tick(now){
  if(!running||!active||document.hidden){pause();return;}
  const dt=last?Math.min((now-last)/1000,.25)*Number($('rate').value):0;last=now;
  let remaining=dt;
  while(remaining>1e-8){
   const slice=Math.min(.25,remaining),next=step(state,command,vessel,slice);remaining-=slice;
   const hit=collision(state,next,vessel,coast,facilityShapes);
   if(hit==='unknown'){
    pause();message('해안선 판정 구역을 이동 중입니다. 다음 구역 자료를 불러옵니다.');startRun();return;
   }
   if(hit){
    pause();state={...state,speed:0,yaw:0};command.throttle=0;inputs();logSample();render();
    message(hit==='land'?'지도 축척 육지와 선체가 교차하여 조종을 멈췄습니다. 공식 해도와 수심 확인이 필요합니다.':'불러온 부두·방파제·건물 형상과 교차하여 조종을 멈췄습니다.');return;
   }
   state=next;
  }
  if(Math.abs(state.lat)>80){pause();message('지도 조종 위도 한계(±80°)에 도달했습니다. 시작 위치를 다시 선택하세요.');}
  if(state.elapsed-elapsedSample>=1){logSample();elapsedSample=state.elapsed;}
  if(now-lastDraw>100){render();lastDraw=now;}
  if(running)frame=requestAnimationFrame(tick);
 }
 function toggleRun(){
  if(running){pause();logSample();render();message('조종 일시정지 · 시간도 멈췄습니다.');return;}
  startRun();
 }
 function reset(next=origin){
  pause();placeRevision++;placing=false;$('place').classList.remove('selected');state=initialState(next.lon,next.lat,next.heading);origin={...state};command={throttle:0,rudder:0};
  samples=[{...state,...command}];elapsedSample=0;following=true;weatherRevision++;weatherGate.invalidate();
  $('weather').disabled=false;$('weather-status').textContent='새 시작점 · 선박 위치 파랑 예보를 조회할 수 있습니다.';inputs();render();
 }
 function selectShip(id){
  vessel=shipById(id);reset(state);
  $('name').textContent=vessel.name;$('type').textContent=vessel.type+' · 조종 데모';
  $('size').textContent=`${vessel.length.toFixed(2)} m × ${vessel.beam.toFixed(2)} m`;
  $('handling').textContent=`조작용 설정: 전진 상한 ${vessel.handling.maxKn} kn, 응답 시간 ${vessel.handling.accelSeconds.toFixed(1)} s. 공개 제원 확인일 ${vessel.checked}.`;
  $('reference').href=vessel.source;$('reference').textContent=vessel.sourceTitle+' ↗';
  $('fleet-list').querySelectorAll('button').forEach(b=>{b.classList.toggle('selected',b.dataset.ship===id);b.setAttribute('aria-pressed',String(b.dataset.ship===id));});
  if(map)map.easeTo({center:[state.lon,state.lat],zoom:clamp(14+Math.log2(399/vessel.length),14,17.5),duration:450});
  message('선박 변경 · 현 위치에서 새 조종 기록을 시작합니다.');
 }
 function setPortMarker(){
  if(ready)map.getSource('helm-port').setData({type:'FeatureCollection',features:located(port)?[{type:'Feature',properties:{},geometry:{type:'Point',coordinates:[port.lon,port.lat]}}]:[]});
 }
 function usePort(p){
  detailGate.invalidate();pause();placeRevision++;port=p;following=false;placing=true;$('place').classList.add('selected');
  facilityRevision++;facilityAbort?.abort();facilityGeo=empty();facilityShapes=[];if(ready){map.getSource('helm-harbor').setData(empty());map.getSource('helm-sector').setData(empty());}
  $('facilities').disabled=!located(p);$('facility-status').textContent='이 항구의 시설 형상은 아직 불러오지 않았습니다.';
  $('port-detail').innerHTML=`<h3>${esc(p.name)}</h3><p>${esc(p.country_code)} · ${esc(p.unlocode||p.id)}</p><p>${located(p)?Number(p.lat).toFixed(5)+'°, '+Number(p.lon).toFixed(5)+'°':'좌표 없음'}</p><small>항구 대표점 · 선석/출발점 아님</small>`;
  if(located(p)){setPortMarker();map?.flyTo({center:[p.lon,p.lat],zoom:13,pitch:0,duration:600});message(p.name+' · 지도에서 조종 시작 위치를 선택하세요. 수심·접근 가능성은 미검증입니다.');}
 }
 function initMap(){
  if(map)return;
  if(typeof maplibregl==='undefined'){message('지도 라이브러리를 불러오지 못했습니다. 새로고침 후 다시 시도하세요.');return;}
  try{
   map=new maplibregl.Map({container:'helm-map',center:[state.lon,state.lat],zoom:14,pitch:40,maxPitch:60,minZoom:1,
    style:{version:8,sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,maxzoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'}},layers:[{id:'background',type:'background',paint:{'background-color':'#c5dce5'}},{id:'base',type:'raster',source:'base',paint:{'raster-saturation':-.5,'raster-opacity':.85}}]}});
   map.addControl(new maplibregl.NavigationControl(),'bottom-right');map.addControl(new maplibregl.ScaleControl({unit:'nautical'}),'bottom-left');
   map.on('load',()=>{
    ready=true;
    for(const name of ['harbor','track','ship','port','sector'])map.addSource('helm-'+name,{type:'geojson',data:empty()});
    map.addLayer({id:'harbor-sector',type:'line',source:'helm-sector',paint:{'line-color':'#a57235','line-width':1.5,'line-dasharray':[3,3]}});
    map.addLayer({id:'harbor-areas',type:'fill',source:'helm-harbor',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':['get','color'],'fill-opacity':.65}});
    map.addLayer({id:'harbor-lines',type:'line',source:'helm-harbor',paint:{'line-color':['get','color'],'line-width':2}});
    map.addLayer({id:'helm-track',type:'line',source:'helm-track',paint:{'line-color':'#137d8a','line-width':2.5,'line-dasharray':[2,1]}});
    map.addLayer({id:'helm-ship',type:'fill-extrusion',source:'helm-ship',paint:{'fill-extrusion-color':['get','color'],'fill-extrusion-height':['get','height'],'fill-extrusion-opacity':1}});
    map.addLayer({id:'helm-port',type:'circle',source:'helm-port',paint:{'circle-radius':8,'circle-color':'#b68237','circle-stroke-width':2,'circle-stroke-color':'#fff'}});
    render();setPortMarker();if(port)map.jumpTo({center:[port.lon,port.lat],zoom:13,pitch:0});map.resize();
   });
   map.on('dragstart',()=>{following=false;});
   map.on('click',async e=>{
    if(!placing||running)return;
    if(ready&&map.queryRenderedFeatures(e.point,{layers:['harbor-areas','harbor-lines']}).some(f=>f.properties.kind!=='dock')){
     message('등록된 건물·부두 위입니다. 지도에서 수면의 시작 위치를 선택하세요.');return;
    }
    if(Math.abs(e.lngLat.lat)>80){message('조종 시작 위치는 위도 ±80° 안에서 선택하세요.');return;}
    const candidate=initialState(wrap(e.lngLat.lng),e.lngLat.lat,90),revision=++placeRevision;
    message('선체 범위의 해안선 형상을 확인하는 중…');
    try{if(!await loadCoast(candidate))throw Error('해안선 형상 범위 밖');}
    catch(err){if(revision===placeRevision)message('해안선 판정 불가 · 다른 위치를 선택하거나 재시도하세요. '+err.message);return;}
    if(revision!==placeRevision||!placing)return;
    const hit=collision(candidate,candidate,vessel,coast,facilityShapes);
    if(hit){message(hit==='land'?'Natural Earth 육지와 선체가 겹칩니다. 수면을 선택하세요.':hit==='facility'?'불러온 시설과 선체가 겹칩니다. 수면을 선택하세요.':'해안선 판정 범위 밖입니다.');return;}
    reset(candidate);placing=false;$('place').classList.remove('selected');
    map.easeTo({center:[state.lon,state.lat],zoom:clamp(14+Math.log2(399/vessel.length),14,17.5),pitch:40,duration:450});
    message('시작 위치 지정 완료 · 지도 축척 육지 겹침 없음. 수심·작은 섬은 미검증입니다.');
   });
   map.on('error',e=>{if(e.sourceId==='base')$('tile-error').hidden=false;});
  }catch(e){message('지도 초기화 실패 · '+e.message);}
 }
 async function loadMeta(){
  if(metaLoaded)return;
  try{const m=await api('/v1/ports/meta');if(m.status!=='ready')throw Error('항구 카탈로그 미준비');metaLoaded=true;
   $('country').innerHTML=countryOptionsMarkup(m.countries);$('coverage').textContent=`${m.countries.length}개 국가·지역 · ${Number(m.total).toLocaleString()}개 위치·시설`;
  }catch(e){$('coverage').textContent='카탈로그 확인 실패 · '+e.message;}
 }
 function search(resetPage=true){
  if(resetPage)offset=0;
  const q=new URLSearchParams({q:$('query').value.trim(),limit:15,offset});if($('country').value)q.set('country',$('country').value);
  $('prev').disabled=true;$('next').disabled=true;$('search-status').textContent='검색 중…';
  return searchGate.run(()=>api('/v1/ports?'+q),r=>{
   total=r.total;$('search-status').textContent=`${total.toLocaleString()}건 · ${total?offset+1:0}–${Math.min(offset+15,total)}`;
   $('port-list').innerHTML=r.items.map(p=>`<button data-port="${esc(p.id)}"${located(p)?'':' disabled'}><b>${esc(p.name)}</b><small>${esc(p.country_code)} · ${esc(p.unlocode||'WPI')} ${located(p)?'':' · 좌표 없음'}</small></button>`).join('')||'<p class="helm-muted">검색 결과가 없습니다.</p>';
   $('port-list').querySelectorAll('button').forEach(b=>b.onclick=()=>detailGate.run(()=>api('/v1/ports/'+encodeURIComponent(b.dataset.port)),usePort,e=>message(e.message)));
   $('prev').disabled=offset===0;$('next').disabled=offset+15>=total;
  },e=>{$('search-status').textContent='조회 실패 · 검색을 눌러 다시 시도하세요. '+e.message;$('port-list').innerHTML='';});
 }
 async function facilities(){
  if(!located(port))return;
  facilityAbort?.abort();facilityAbort=new AbortController();const controller=facilityAbort,rev=++facilityRevision;
  const selected={...port},center=map?.getCenter()||{lat:Number(port.lat),lng:Number(port.lon)};
  const queryLat=center.lat,queryLon=wrap(center.lng),isReference=Math.abs(queryLat-Number(selected.lat))<.0001&&Math.abs(queryLon-Number(selected.lon))<.0001;
  $('facilities').disabled=true;$('facility-status').textContent='지도 중심 주변 0.01° 구역의 실제 시설 형상 조회 중…';
  const timeout=setTimeout(()=>controller.abort(),35000);
  try{
   let r=null;
   if(isReference&&/^[A-Z]{2}[A-Z0-9]{3}$/.test(selected.unlocode||'')){
    const cached=await fetch('/console-assets/harbors/'+selected.unlocode+'.json',{signal:controller.signal});
    if(cached.ok)r=cached;
   }
   if(!r)r=await fetch(harborMapURL(queryLat,queryLon),{signal:controller.signal});
   if(!r.ok)throw Error('원천 응답 '+r.status);
   const geo=harborFeatures(await r.json());if(rev!==facilityRevision)return;
   facilityGeo=geo;facilityShapes=coast?harborObstacles(geo,coast.center):[];
   if(ready){
    map.getSource('helm-harbor').setData(geo);
    const x=queryLon,y=queryLat;
    map.getSource('helm-sector').setData({type:'FeatureCollection',features:[{type:'Feature',properties:{},geometry:{type:'LineString',coordinates:[[x-.005,y-.005],[x+.005,y-.005],[x+.005,y+.005],[x-.005,y+.005],[x-.005,y-.005]]}}]});
   }
   $('facility-status').textContent=`OSM 형상 ${geo.features.length}개 · ${queryLat.toFixed(4)}, ${queryLon.toFixed(4)} 주변 0.01° 구역 · 확보 시각 ${geo.osm_base||new Date().toISOString()} · 미수록은 시설 없음이 아닙니다.`;
  }catch(e){if(rev===facilityRevision)$('facility-status').textContent='시설 형상 조회 실패 · 다시 시도할 수 있습니다. 배경 지도만 표시 중. '+(e.name==='AbortError'?'응답 시간 초과':e.message);}
  finally{clearTimeout(timeout);if(rev===facilityRevision)$('facilities').disabled=false;}
 }
 function weather(){
  const p={...state},when=new Date(),rev=++weatherRevision;$('weather').disabled=true;
  $('weather-status').textContent='현재 위치의 전 지구 예보 조회 중…';
  weatherGate.run(()=>api('/v1/global/point?'+new URLSearchParams({lat:p.lat,lon:p.lon})),r=>{
   if(rev!==weatherRevision)return;
   const items=r.items||[],time=when.getTime(),valid=items.filter(i=>Number.isFinite(Date.parse(i.valid_time)));
   if(!valid.length||time<Date.parse(valid[0].valid_time)||time>Date.parse(valid.at(-1).valid_time)){$('weather-status').textContent='현재 시각을 포함하는 예보가 없습니다.';}
   else {const i=valid.reduce((a,b)=>Math.abs(Date.parse(a.valid_time)-time)<Math.abs(Date.parse(b.valid_time)-time)?a:b);
    $('weather-status').textContent=`${p.lat.toFixed(4)}, ${p.lon.toFixed(4)} · ${i.valid_time} · 파고 ${Number.isFinite(i.q50)?i.q50.toFixed(2)+' m':'결측'} · ${i.status||'원천 값'} · NOAA ${r.cycle} (가장 가까운 예보 시각)`;}
   $('weather').disabled=false;
  },e=>{if(rev===weatherRevision){$('weather-status').textContent='예보 조회 실패 · '+e.message;$('weather').disabled=false;}});
 }
 $('fleet-list').innerHTML=SHIPS.map(s=>`<button data-ship="${s.id}"><span class="helm-ship-glyph" style="--ship-color:${s.color}"></span><span><b>${s.type}</b><small>${s.name}</small><em>${s.length.toFixed(1)} × ${s.beam.toFixed(1)} m</em></span></button>`).join('');
 $('fleet-list').querySelectorAll('button').forEach(b=>b.onclick=()=>selectShip(b.dataset.ship));
 $('source').onclick=()=>{root.querySelector('details').open=true;$('reference').focus();};
 $('throttle').oninput=e=>{command.throttle=Number(e.target.value);inputs();};
 $('rudder').oninput=e=>{command.rudder=Number(e.target.value);inputs();};
 root.querySelectorAll('[data-throttle]').forEach(b=>b.onclick=()=>{command.throttle=Number(b.dataset.throttle);inputs();});
 root.querySelectorAll('[data-rudder]').forEach(b=>b.onclick=()=>{command.rudder=Number(b.dataset.rudder);inputs();});
 $('run').onclick=toggleRun;$('reset').onclick=()=>{reset();message('시작 위치로 초기화했습니다.');};
 $('follow').onclick=()=>{following=true;map?.easeTo({center:[state.lon,state.lat],zoom:Math.max(14,map.getZoom()),duration:400});};
 $('camera').onclick=()=>{if(map){map.easeTo({pitch:map.getPitch()>10?0:50,duration:500});$('camera').textContent=map.getPitch()>10?'입체 보기':'위에서 보기';}};
 $('place').onclick=()=>{pause();placeRevision++;placing=!placing;following=false;$('place').classList.toggle('selected',placing);message(placing?'지도에서 시작 위치를 클릭하세요. 항만 접근·수심은 미검증입니다.':'시작 위치 선택 취소');};
 $('export').onclick=()=>{
  const doc=sessionDocument(vessel,origin,[...samples,{...state,...command}],port);doc.catalog_version=SHIP_CATALOG_VERSION;
  const url=URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='poseidon-helm-'+vessel.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 };
 $('search').onsubmit=e=>{e.preventDefault();clearTimeout(searchTimer);loadMeta();search();};
 $('query').oninput=()=>{searchGate.invalidate();clearTimeout(searchTimer);searchTimer=setTimeout(()=>search(),300);};
 $('country').onchange=()=>{clearTimeout(searchTimer);search();};
 $('prev').onclick=()=>{offset=Math.max(0,offset-15);search(false);};$('next').onclick=()=>{offset+=15;search(false);};
 $('facilities').onclick=facilities;$('weather').onclick=weather;
 document.addEventListener('keydown',e=>{
  if(!active||e.ctrlKey||e.metaKey||e.altKey||e.target.closest('input,select,textarea,[contenteditable]'))return;
  if(e.key===' '&&e.target.closest('button,a'))return;
  const key=e.key.toLowerCase();if(!['w','s','a','d','x','c',' '].includes(key))return;e.preventDefault();
  if(key==='w')command.throttle=clamp(command.throttle+5,-50,100);if(key==='s')command.throttle=clamp(command.throttle-5,-50,100);
  if(key==='a')command.rudder=clamp(command.rudder-5,-35,35);if(key==='d')command.rudder=clamp(command.rudder+5,-35,35);
  if(key==='x')command.throttle=0;if(key==='c')command.rudder=0;if(key===' '&&!e.repeat)toggleRun();inputs();
 });
 document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
 window.addEventListener('blur',pause);
 selectShip(vessel.id);message('부산 앞바다 · 선박을 고르고 조종을 시작하세요.');
 return {
  activate(){active=true;initMap();map?.resize();if(!metaLoaded){loadMeta();search();}},
  pause(){active=false;pause();},
  usePort,
 };
}
