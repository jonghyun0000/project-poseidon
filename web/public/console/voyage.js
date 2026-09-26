import {fmt,esc,dateLabel} from './domain.js';
import {normalizeLongitude,unwrapRoute,coordinateLabel,GLOBAL_ROUTES} from './global-domain.js';
import {initPerformanceForm} from './performance-form.js';
import {fuelLabel} from './performance-domain.js';
import {chartMarkup} from './charts.js';
import {initRouting} from './routing.js';
import {validateRoutingPlan} from './routing-domain.js';
import {EXAMPLE,parseWaypoints,requestFromForm,voyageCSV,validateImportedWaypoints,voyageTrackFeatures} from './voyage-domain.js';

import {applyPortToRoute,attachPortReferences,matchingPortReferences,importedPortReferences} from './voyage-ports-domain.js';

const statusText={covered:'파고 자료 확보',partial:'예보·해역 결측 있음',invalid_route:'육지 통과 검출'};
const pointStatus={ok:'조회됨',outside_domain:'예보 해역 밖',outside_forecast_time:'예보 시각 밖',mask_unavailable:'해양 마스크 없음',land_or_dry:'육지·천해',no_wave_energy:'유효 파랑 없음',provider_missing:'원천 결측·해빙·육지'};
const verdictText={in_range:'파고 검증 범위 안',sparse:'파고 표본 부족',out_of_range:'파고 검증 범위 밖',no_coverage:'검증 근거 부족'};

export function initVoyage({api,getMeta,getSource=()=>'regional'}){
 const $=id=>document.getElementById(id);
 const V={map:null,ready:false,report:null,scenario:0,cursor:0,timer:null,ship:null,markers:[],adding:false,dirty:false,requestId:0,cycle:null,busy:false,ports:[],plan:null,manual:null,planLoad:0,gapMarkers:[]};
 const performance=initPerformanceForm(dirty);
 const routing=initRouting({api,getSource,onPlan:loadRoutePlan,onClear:releaseRoutePlan});
 function pause(){if(V.timer)clearInterval(V.timer);V.timer=null;$('voy-play').textContent='재생';}
 function saveForm(){if(V.restoring)return;try{localStorage.setItem(getSource()==='global'?'poseidon.voyage.form.global':'poseidon.voyage.form',JSON.stringify({name:$('voy-name').value,coordinates:V.plan?V.manual?.coordinates||EXAMPLE:$('voy-waypoints').value,route_plan_id:V.plan?.plan_id||null,manual:V.manual,departure:$('voy-departure').value,speed:$('voy-speed').value,threshold:$('voy-threshold').value,compare:$('voy-compare').checked,deadline:$('voy-deadline').value,performance:performance.raw(),ports:V.ports}));}catch{}}
 function dirty(options={}){if(!options.keepPending)V.planLoad++;renderPortContext();pause();V.dirty=true;V.requestId++;$('voy-error').textContent='';$('voy-play').disabled=true;$('voy-position').disabled=true;$('voy-csv').disabled=true;$('voy-json').disabled=true;if(V.report||V.busy)$('voy-result-message').textContent='입력이 변경되었습니다. 표시 중인 이전 결과를 갱신하려면 다시 분석하세요.';$('voy-comparison').querySelectorAll('[data-scenario]').forEach(b=>b.disabled=true);if(!options.noSave)saveForm();drawDraft();}
 function routePoints(){return V.plan?V.plan.waypoints:parseWaypoints($('voy-waypoints').value);}
 function routeControls(){
  $('voy-waypoints').readOnly=!!V.plan;$('voy-map-add').disabled=!!V.plan;
  $('voy-point-limit').textContent=V.plan?`${V.plan.waypoints.length}개 원형 경유점 · 자동 항로 고정`:'2–20개 지점';
  $('voy-departure-label').textContent=V.plan?'항로망 진입 시각 · UTC':'출항 시각 · UTC';
  $('voy-deadline').disabled=!!V.plan;
 }
 function loadRoutePlan(plan){
  validateRoutingPlan(plan,plan.request);routing.restore(plan);V.planLoad++;
  if(!V.plan&&!V.manual)V.manual={coordinates:$('voy-waypoints').value,name:$('voy-name').value,ports:V.ports};
  V.plan=plan;V.ports=[];V.adding=false;$('voy-map-add').setAttribute('aria-pressed','false');$('voy-map-add').textContent='지도에서 추가';
  if(V.map)V.map.getCanvas().style.cursor='';
  $('voy-waypoints').value=plan.waypoints.map(p=>`${p.lat}, ${p.lon}`).join('\n');
  $('voy-name').value=`${plan.departure.port.name}–${plan.arrival.port.name} 해상 구간`.slice(0,80);
  routeControls();dirty();fit(plan.waypoints);drawPortGaps(plan);
  $('voy-result-message').textContent='자동 해상 항로 후보입니다. 항구–접속점의 점선 구간은 미검증이며 거리·시간·연료에서 제외합니다.';
 }
 function releaseRoutePlan(){
  V.planLoad++;if(!V.plan)return;V.plan=null;
  if(V.manual){$('voy-waypoints').value=V.manual.coordinates;$('voy-name').value=V.manual.name;V.ports=V.manual.ports||[];}
  V.manual=null;routeControls();dirty();drawPortGaps(null);$('voy-result-message').textContent='자동 항로를 해제했습니다. 수동 좌표를 확인하고 다시 분석하세요.';
 }
 function drawPortGaps(plan){
  if(!V.ready)return;
  const features=plan?['departure','arrival'].map(role=>{const e=plan[role],path=unwrapRoute([e.port,e.attachment]);return{type:'Feature',properties:{},geometry:{type:'LineString',coordinates:path.map(p=>[p.plot_lon,p.lat])}};}):[];
  const data={type:'FeatureCollection',features};
  if(V.map.getSource('voy-port-gaps'))V.map.getSource('voy-port-gaps').setData(data);
  else{V.map.addSource('voy-port-gaps',{type:'geojson',data});V.map.addLayer({id:'voy-port-gaps',type:'line',source:'voy-port-gaps',paint:{'line-color':'#a57743','line-width':2,'line-dasharray':[2,2]}});}
  V.gapMarkers.forEach(m=>m.remove());V.gapMarkers=[];
  if(plan)for(const [role,label]of[['departure','출발항'],['arrival','도착항']]){const p=plan[role].port,el=document.createElement('div');el.className='voy-port-ref';el.textContent=label;el.title=p.name+' · 미검증 연결';V.gapMarkers.push(new maplibregl.Marker({element:el}).setLngLat([p.lon,p.lat]).addTo(V.map));}
 }
 function renderPortContext(){
  const host=$('voy-port-context');if(!host)return;
  let points;try{points=parseWaypoints($('voy-waypoints').value);}catch{points=null;}
  if(points)V.ports=matchingPortReferences(points,V.ports);
  host.replaceChildren();
  if(!V.ports.length){host.hidden=true;return;}
  host.hidden=false;
  const title=document.createElement('strong');title.textContent='항구 대표 위치 연결';host.append(title);
  for(const port of V.ports){const line=document.createElement('div');line.textContent=`${port.name} · ${port.unlocode||port.id}`;host.append(line);}
  const note=document.createElement('p');note.className='micro';note.textContent='입항점·선석 좌표가 아닙니다. 중간 경유지를 확인하세요. 좌표를 수정하면 해당 항구 연결이 해제됩니다.';host.append(note);
 }
 function usePort(port,role){
  if(role==='via')routing.clear();else routing.setPort(port,role);
  const next=applyPortToRoute($('voy-waypoints').value,V.ports,port,role);
  $('voy-waypoints').value=next.coordinates;V.ports=next.references;dirty();
  $('voy-result-message').textContent=`${port.name}을(를) ${ {departure:'출발지',arrival:'도착지',via:'중간 경유지'}[role]}에 넣었습니다. 기존 중간 지점을 확인하고 항로를 다시 분석하세요.`;
  try{fit(parseWaypoints(next.coordinates));}catch{}
 }
 function download(text,type,extension){const blob=new Blob([text],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`poseidon_voyage_${V.report.cycle}_${V.report.analysis_id.slice(0,8)}.${extension}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 function source(data){if(!V.ready)return;if(V.map.getSource('voy-route'))V.map.getSource('voy-route').setData(data);else{V.map.addSource('voy-route',{type:'geojson',data,tolerance:0});V.map.addLayer({id:'voy-route-line',type:'line',source:'voy-route',paint:{'line-width':4,'line-color':['get','color'],'line-opacity':.95}});}}
 function markers(points){if(!V.ready)return;let track=unwrapRoute(points);if(V.plan||V.report?.route_plan&&!V.dirty)track=[track[0],track.at(-1)];V.markers.forEach(m=>m.remove());V.markers=[];track.forEach((p,i)=>{const el=document.createElement('div');el.className='voy-waypoint';el.textContent=i+1;V.markers.push(new maplibregl.Marker({element:el}).setLngLat([p.plot_lon,p.lat]).addTo(V.map));});}
 function fit(points){if(!V.ready||!points.length)return;const b=new maplibregl.LngLatBounds();unwrapRoute(points).forEach(p=>b.extend([p.plot_lon,p.lat]));V.map.fitBounds(b,{padding:55,maxZoom:7,duration:400});}
 function drawDraft(){
  if(!V.ready||V.report&&!V.dirty)return;
  $('voy-vessel-time').textContent=V.plan?'항로망 구간 후보':'분석 전';
  $('voy-vessel-position').textContent=V.plan?'항구 연결 구간 제외':'입력 좌표 확인';
  try{const points=routePoints();source({type:'FeatureCollection',features:[{type:'Feature',properties:{color:'#8293a0'},geometry:{type:'LineString',coordinates:unwrapRoute(points).map(p=>[p.plot_lon,p.lat])}}]});markers(points);V.ship?.remove();drawPortGaps(V.plan);}
  catch{source({type:'FeatureCollection',features:[]});V.markers.forEach(m=>m.remove());V.markers=[];V.ship?.remove();drawPortGaps(null);}
 }
 function drawResult(){
  if(!V.ready||!V.report)return;
  const r=V.report,s=r.scenarios[V.scenario],threshold=r.request.hs_threshold_m;
  const features=voyageTrackFeatures(s.points,threshold);
  r.screening.points.forEach(p=>features.push({type:'Feature',properties:{color:'#b8423d'},geometry:{type:'LineString',coordinates:[[p.lon-.025,p.lat-.025],[p.lon+.025,p.lat+.025]]}}));
  source({type:'FeatureCollection',features});markers(r.request.waypoints);drawPortGaps(r.route_plan||null);drawCursor();
 }
 function mapInit(){
  if(V.map){V.map.resize();return;}
  if(typeof maplibregl==='undefined'){$('voy-result-message').textContent='지도 라이브러리를 불러오지 못했습니다. 좌표 입력으로 분석은 가능합니다.';return;}
  V.map=new maplibregl.Map({container:'voy-map',center:getSource()==='global'?[180,30]:[128.5,31.5],zoom:getSource()==='global'?1.8:4,style:{version:8,sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},layers:[{id:'base',type:'raster',source:'base',paint:{'raster-saturation':-.85,'raster-opacity':.85}}]}});
  V.map.addControl(new maplibregl.NavigationControl({showCompass:false}));
  V.map.on('load',()=>{V.ready=true;if(V.report&&!V.dirty){drawResult();fit(V.report.request.waypoints);}else{drawDraft();try{fit(routePoints());}catch{}}});
  V.map.on('click',e=>{if(!V.adding)return;routing.clear();const current=$('voy-waypoints').value.trim();if(current.split('\n').filter(v=>v.trim()).length>=20){$('voy-error').textContent='웨이포인트는 최대 20개입니다.';return;}$('voy-waypoints').value=current+(current?'\n':'')+`${e.lngLat.lat.toFixed(4)}, ${normalizeLongitude(e.lngLat.lng).toFixed(4)}`;dirty();});
  V.map.on('error',()=>{$('voy-error').textContent='일부 배경 지도 자료를 불러오지 못했습니다. 수치 분석 결과와 구분해 확인하세요.';});
  new ResizeObserver(()=>V.map.resize()).observe($('voy-map'));
 }
 function drawCursor(){
  if(!V.report)return;const s=V.report.scenarios[V.scenario],p=unwrapRoute(s.points)[V.cursor],global=V.report.source==='global';
  $('voy-position').max=s.points.length-1;$('voy-position').value=V.cursor;$('voy-progress').textContent=`${V.cursor+1} / ${s.points.length}`;
  $('voy-vessel-time').textContent=dateLabel(p.valid_time);
  $('voy-vessel-position').textContent=`${coordinateLabel(p.lat,p.lon)} · ${fmt(p.distance_nm,1)} nm`;
  $('voy-now').innerHTML=[['항해 경과',fmt(p.elapsed_h,1)+' h'],['유의파고',fmt(p.values.hs)+' m'],[global?'주 파주기':'첨두주기',fmt(global?p.values.primary_period:p.values.tp,1)+' s'],[global?'주 파향':'첨두파향',fmt(global?p.values.primary_direction:p.values.dirp,0)+'°'],['침로',fmt(p.course_deg,0)+'°'],[global?'해상 풍속':'10 m 풍속',fmt(p.wind?.speed_ms,1)+' m/s'],['이동 상대풍',fmt(p.wind?.apparent_speed_ms,1)+' m/s'],['상태',pointStatus[p.status]||p.status]].map(([k,v])=>`<div><small>${esc(k)}</small><b>${esc(v)}</b></div>`).join('');
  if(V.ready){if(!V.ship){const el=document.createElement('div');el.className='voy-ship';el.textContent='▲';V.ship=new maplibregl.Marker({element:el,rotationAlignment:'map'});}V.ship.setLngLat([p.plot_lon,p.lat]).setRotation(p.course_deg).addTo(V.map);}
  $('voy-chart').innerHTML=chartMarkup(s.points.map(p=>({lead_h:p.elapsed_h,q50:p.values.hs})), 'hs',V.cursor,V.report.request.hs_threshold_m);
 }
 function selectScenario(index){pause();V.scenario=index;V.cursor=0;renderReport();drawResult();}
 function renderReport(){
  const r=V.report,s=r.scenarios[V.scenario],global=r.source==='global',auto=!!r.route_plan;
  $('voy-result-message').classList.toggle('attention',s.status!=='covered'||!r.forecast_period.covers_now);
  const screen={land_detected:'육지 통과가 검출되었습니다. 웨이포인트를 수정하세요.',no_land_detected:'지형 표본에서 육지 통과 미검출 · 항행 가능 여부는 미검증.',incomplete:'지형 자료 범위 밖 구간이 있습니다.',unavailable:'육지 통과 검사를 수행하지 못했습니다.'}[r.screening.status];
  $('voy-result-message').textContent=`${auto?'항로망 구간 · 항구 연결 제외 · ':''}${r.forecast_period.covers_now?'예보 분석':'과거 예보 재현'} · ${screen}${s.status==='partial'?' 예보가 없는 구간은 결측입니다.':''}${r.request.performance_profile?.source_kind==='synthetic_example'?' 연료 숫자는 가상 기능 시험용입니다.':''}`;
  $('voy-comparison').innerHTML=`<div class="voy-section-title"><h2>${auto?'진입 시각':'출항'}·속력 시나리오 비교</h2><span>항로 ${fmt(r.distance_nm,1)} nm</span></div><div class="voy-scenarios">${r.scenarios.map((v,i)=>`<button class="voy-scenario ${i===V.scenario?'selected':''}" data-scenario="${i}" ${V.dirty?'disabled':''}><div class="scenario-title"><b>${esc(v.name)}</b><span>${fmt(v.speed_kn,1)} kn</span></div><p class="micro">${auto?'항로망 진입':'출항'} ${esc(dateLabel(v.departure_utc))}</p><strong>${fmt(v.summary.duration_h,1)}<small> h</small></strong><p>${auto?'접속점 종료 예정':'도착 예정'} ${esc(dateLabel(v.arrival_utc))}</p><dl><dt>조회 지점 최대 파고</dt><dd>${fmt(v.summary.max_hs_m)} m</dd><dt>${fmt(r.request.hs_threshold_m,1)} m 초과 시간</dt><dd>${fmt(v.summary.above_threshold_h,1)} h</dd><dt>파고 자료 커버리지</dt><dd>${fmt(v.summary.coverage_pct,1)}%</dd><dt>조회 지점 최대 풍속</dt><dd>${fmt(v.summary.max_wind_ms,1)} m/s</dd><dt>바람 자료 커버리지</dt><dd>${fmt(v.summary.wind_coverage_pct,1)}%</dd></dl><div class="scenario-fuel"><span class="fuel-origin ${v.fuel?.source_kind==='synthetic_example'?'synthetic':''}">${esc(fuelLabel(v.fuel))}</span><dl><dt>해상 연료 기준값</dt><dd>${fmt(v.fuel?.fuel_t,2)} t</dd><dt>연소 CO₂ 기준값</dt><dd>${fmt(v.fuel?.co2_t,2)} t</dd><dt>도착 마감</dt><dd>${v.arrival_constraint?.status==='not_applicable'?'항만 도착 판정 제외':v.arrival_constraint?.status==='not_configured'?'미설정':v.arrival_constraint?.status==='invalid_route'?'육지 통과 · 판정 제외':v.arrival_constraint?.status==='on_time'?fmt(v.arrival_constraint.margin_h,1)+' h 여유':fmt(Math.abs(v.arrival_constraint?.margin_h),1)+' h 초과'}</dd></dl></div><div class="tag ${v.status==='covered'?'':'pending'}">${statusText[v.status]}</div></button>`).join('')}</div><p class="micro">초과 시간은 값이 있는 구간만 선형 보간해 합산합니다. 100% 미만인 결과끼리는 전체 항로의 노출 시간을 비교할 수 없습니다. 연료는 정온·무해류 조건의 해상 항해 기준값이며 출항 전 대기는 제외합니다.</p>`;
  $('voy-comparison').querySelectorAll('[data-scenario]').forEach(b=>b.onclick=()=>selectScenario(+b.dataset.scenario));
  $('voy-detail').hidden=false;$('voy-table-panel').hidden=false;$('voy-scenario-label').textContent=s.name;
  $('voy-table').innerHTML=`<table><thead><tr><th>구간</th><th>UTC 통과 시각</th><th>누적 nm</th><th>위도 / 경도</th><th>침로 °</th><th>Hs m</th><th>${global?'주 파주기':'Tp'} s</th><th>${global?'주 파향':'첨두파향'} °</th><th>${global?'해상':'10 m'} 풍속 m/s</th><th>상대풍 m/s</th><th>바람 상태</th><th>자료 상태</th><th>파고 검증 범위</th></tr></thead><tbody>${s.points.map(p=>`<tr><td>${p.leg}</td><td>${esc(dateLabel(p.valid_time))}</td><td>${fmt(p.distance_nm,1)}</td><td>${fmt(p.lat,3)} / ${fmt(p.lon,3)}</td><td>${fmt(p.course_deg,1)}</td><td>${fmt(p.values.hs,3)}</td><td>${fmt(global?p.values.primary_period:p.values.tp,2)}</td><td>${fmt(global?p.values.primary_direction:p.values.dirp,1)}</td><td>${fmt(p.wind?.speed_ms,2)}</td><td>${fmt(p.wind?.apparent_speed_ms,2)}</td><td>${p.wind?.status==='ok'?'조회됨':'결측'}</td><td>${pointStatus[p.status]}</td><td>${esc(verdictText[p.applicability?.verdict]||'—')}</td></tr>`).join('')}</tbody></table>`;
  $('voy-methods').innerHTML=`<b>분석 근거</b><p>예보 기준 ${esc(r.cycle)} UTC · 생산 ${esc(dateLabel(r.produced_at))}${global?' · 원천 수신 '+esc(dateLabel(r.retrieved_at)):''} · 분석 ${esc(dateLabel(r.created_at_utc))}<br>${global?'파고: NOAA 원천, 유효 해양 셀 정규화 공간·시간 보간 · 주 파주기/파향: PERPW/DIRPW의 가까운 예보 시각·해양 셀 선택':'파고: 물리 원값, 해양 정규화 공간 보간 + 시각 보간 · 첨두주기/파향: 가까운 예보 시각의 해양 셀 선택'}<br>이동: WGS84 측지선 · 일정 대지속력 · 육지 검사: ${esc(r.screening.source||'미확보')} / 최대 1 nm 간격</p><p>바람: ${esc(r.environment?.wind?.source||'미연결')} · 기준 ${esc(r.environment?.wind?.cycle||'—')} UTC · ${esc(r.environment?.wind?.status||'not_available')}<br>연료 곡선: ${esc(r.request.performance_profile?.name||'미입력')} · 출처 ${esc(r.request.performance_profile?.source||'—')}<br>적용 조건: ${esc(r.request.performance_profile?.load_condition||'—')} · ${esc(fuelLabel(s.fuel))}<br>도착 마감: ${esc(dateLabel(r.request.arrival_deadline_utc))}<br>연소 CO₂ 계수: ${fmt(s.fuel?.co2_factor,3)} t-CO₂/t-fuel · IMO MEPC.364(79) §2.2.1</p><p>${r.limitations.map(esc).join('<br>')}</p><small>분석 ID ${esc(r.analysis_id)} · 입력·구간 값·계산 방법은 ‘분석 저장’ 파일에 포함됩니다.</small>`;
  if(auto){const note=document.createElement('p');note.textContent=`해상 항로망 구간만 계산 · ${r.route_plan.network.source_name} · 원천 ${r.route_plan.network.source_gpkg_last_change} · 계획 ${r.route_plan.plan_id} · 접속 간격 ${fmt(r.route_plan.departure.gap_nm,1)} / ${fmt(r.route_plan.arrival.gap_nm,1)} nm 제외`;$('voy-methods').append(note);}
  if(r.port_references?.length){const note=document.createElement('p');note.textContent='항구 목록 근거 · '+r.port_references.map(p=>`${p.name} (${p.unlocode||p.port_id}) · ${p.coordinate_source||'좌표 출처 미상'} · 목록 ${p.catalog_id}`).join(' / ');$('voy-methods').append(note);}
  $('voy-csv').disabled=V.dirty;$('voy-json').disabled=V.dirty;
  $('voy-play').disabled=V.dirty||s.status==='invalid_route';$('voy-position').disabled=V.dirty;
  drawCursor();
 }
 async function run(){
  if(V.busy)return;pause();let payload;
  try{const meta=getMeta();if(!meta)throw new Error('예보 정보를 먼저 불러와야 합니다. 상단 새로고침으로 다시 시도하세요.');payload=requestFromForm({name:$('voy-name').value,coordinates:V.plan?[V.plan.waypoints[0],V.plan.waypoints.at(-1)].map(p=>`${p.lat}, ${p.lon}`).join('\n'):$('voy-waypoints').value,departure:$('voy-departure').value,speed:$('voy-speed').value,threshold:$('voy-threshold').value,compare:$('voy-compare').checked,cycle:meta.cycle,source:getSource(),deadline:V.plan?'':$('voy-deadline').value,performanceProfile:performance.profile()});if(V.plan){payload.waypoints=V.plan.waypoints;payload.route_plan_id=V.plan.plan_id;payload.arrival_deadline_utc=null;payload.scenarios.forEach(s=>{if(s.name==='6시간 후 출항')s.name='6시간 후 진입';});}else payload.waypoints=attachPortReferences(payload.waypoints,V.ports);}
  catch(e){$('voy-error').textContent=e.message;return;}
  dirty();V.busy=true;const id=++V.requestId;$('voy-run').disabled=true;$('voy-run').textContent='항로와 예보 계산 중';$('voy-error').textContent='';$('voy-result-message').textContent='항로·이동 시각별 파랑과 바람·입력된 연료 곡선을 계산하고 있습니다.';$('voy-csv').disabled=true;$('voy-json').disabled=true;$('voy-play').disabled=true;
  try{const report=await api('/v1/voyage/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(id!==V.requestId)return;V.report=report;V.scenario=0;V.cursor=0;V.dirty=false;renderReport();drawResult();fit(report.scenarios[0].points);saveForm();}
  catch(e){if(id===V.requestId){$('voy-error').textContent=e.message;$('voy-result-message').textContent='분석 실패. 입력값과 예보 가용성을 확인한 뒤 다시 실행하세요.';}}
  finally{V.busy=false;$('voy-run').disabled=false;$('voy-run').textContent='항로 분석 실행';}
 }
 function setMeta(meta){
  $('voy-cycle').textContent=`${getSource()==='global'?'NOAA 전 지구':'Poseidon 동아시아'} · 예보 기준 ${meta.cycle} UTC`;$('voy-level').textContent=getSource()==='global'?'WGS84 · GLOBAL':'WGS84 · L1';
  $('voy-context').textContent=`${dateLabel(meta.valid_times[0])} – ${dateLabel(meta.valid_times.at(-1))}${meta.covers_now?'':' · 과거 예보'}`;
  if(!$('voy-departure').value)$('voy-departure').value=meta.valid_times[0].slice(0,16);
  const key=`${meta.source||getSource()}:${meta.cycle}`;if(V.cycle&&V.cycle!==key)dirty();V.cycle=key;
 }
 $('voy-form').onsubmit=e=>{e.preventDefault();run();};
 $('routing-section').addEventListener('input',()=>{V.planLoad++;});
 $('routing-section').addEventListener('change',()=>{V.planLoad++;});
 $('routing-calculate').addEventListener('click',()=>{V.planLoad++;});
 $('voy-form').querySelectorAll('input,textarea,select').forEach(e=>{if(!e.closest('#routing-section'))e.addEventListener('input',event=>{if(e.id==='voy-waypoints')routing.clear();dirty(event);});});
 function restoreExample(){routing.clear();V.plan=null;V.manual=null;V.planLoad++;routeControls();V.ports=[];const global=getSource()==='global',example=GLOBAL_ROUTES[$('voy-template').value]||GLOBAL_ROUTES.pacific;$('voy-name').value=global?example.name:'대한해협–동중국해 해상 구간';$('voy-waypoints').value=global?example.coordinates:EXAMPLE;$('voy-speed').value=global?example.speed:14;$('voy-threshold').value=3;$('voy-compare').checked=true;$('voy-deadline').value='';performance.restore({enabled:false});const meta=getMeta();$('voy-departure').value=meta?meta.valid_times[0].slice(0,16):'';dirty();try{fit(parseWaypoints($('voy-waypoints').value));}catch{}}
 $('voy-example').onclick=restoreExample;$('voy-template').onchange=restoreExample;
 $('voy-cycle-start').onclick=()=>{const m=getMeta();if(m){$('voy-departure').value=m.valid_times[0].slice(0,16);dirty();}};
 $('voy-map-add').onclick=()=>{V.adding=!V.adding;$('voy-map-add').setAttribute('aria-pressed',V.adding);$('voy-map-add').textContent=V.adding?'지도 추가 중 · 종료':'지도에서 추가';if(V.map)V.map.getCanvas().style.cursor=V.adding?'crosshair':'';};
 $('voy-play').onclick=()=>{if(V.timer){pause();return;}const s=V.report?.scenarios[V.scenario];if(!s||V.dirty||s.status==='invalid_route')return;if(V.cursor>=s.points.length-1)V.cursor=0;$('voy-play').textContent='일시정지';V.timer=setInterval(()=>{V.cursor++;if(V.cursor>=s.points.length-1){V.cursor=s.points.length-1;pause();}drawCursor();},450);};
 $('voy-position').oninput=e=>{pause();V.cursor=+e.target.value;drawCursor();};
 $('voy-csv').onclick=()=>{if(V.report&&!V.dirty)download(voyageCSV(V.report),'text/csv;charset=utf-8','csv');};
 $('voy-json').onclick=()=>{if(V.report&&!V.dirty)download(JSON.stringify(V.report,null,2),'application/json','json');};
 $('voy-import').onchange=async e=>{
  const ticket=++V.planLoad,source=getSource();let applying=false;
  try{
   const file=e.target.files[0];if(!file)return;
   if(file.size>10*1024*1024)throw new Error('분석 파일은 10 MB 이하만 불러올 수 있습니다.');
   const d=JSON.parse(await file.text()),r=d.request||d;
   if(!Array.isArray(r.waypoints)||!r.scenarios?.length)throw new Error('Poseidon 분석 저장 파일 형식이 아닙니다.');
   if((r.source||d.source||'regional')!==source)throw new Error('저장 파일의 예보 원천이 현재 선택과 다릅니다. 상단에서 전 세계/동아시아 범위를 맞춘 뒤 불러오세요.');
   let plan=null;
   if(r.route_plan_id){
    if(!/^[a-f0-9]{64}$/.test(r.route_plan_id))throw new Error('저장 항로 계획 ID가 올바르지 않습니다.');
    plan=await api('/v1/routing/plans/'+r.route_plan_id);validateRoutingPlan(plan,plan.request);
   }
   if(ticket!==V.planLoad||source!==getSource())return;
   const coordinates=validateImportedWaypoints(r,plan),departure=new Date(r.departure_utc).toISOString().slice(0,16),deadline=r.arrival_deadline_utc?new Date(r.arrival_deadline_utc).toISOString().slice(0,16):'';
   if(r.performance_profile&&(!Array.isArray(r.performance_profile.curve)||r.performance_profile.curve.some(p=>!p)))throw new Error('저장 파일의 선박 성능 곡선 형식이 올바르지 않습니다.');
   applying=true;routing.clear();
   if(!plan){V.plan=null;V.manual=null;V.ports=importedPortReferences(r);$('voy-waypoints').value=coordinates;routeControls();}
   $('voy-name').value=String(r.name||'불러온 분석').slice(0,80);$('voy-departure').value=departure;
   $('voy-speed').value=r.scenarios[0].speed_kn;$('voy-threshold').value=r.hs_threshold_m;$('voy-compare').checked=r.scenarios.length>1;$('voy-deadline').value=plan?'':deadline;
   performance.loadProfile(r.performance_profile);
   if(plan)loadRoutePlan(plan);else dirty();
   $('voy-result-message').textContent=`입력값을 불러왔습니다. 현재 선택 예보 ${getMeta()?.cycle||'—'}로 다시 분석합니다. 비교 대안은 화면의 기준/6시간 지연/2 kn 감속으로 생성됩니다.`;
  }catch(error){if(applying||ticket===V.planLoad){$('voy-error').textContent=error.message;}}
  finally{e.target.value='';}
 };
 function restoreStored(){
  V.plan=null;V.manual=null;routeControls();
  $('voy-template-field').hidden=getSource()!=='global';
  let f;try{f=JSON.parse(localStorage.getItem(getSource()==='global'?'poseidon.voyage.form.global':'poseidon.voyage.form'));}catch{}
  if(!f){restoreExample();return;}
  V.ports=Array.isArray(f.ports)?f.ports:[];
  $('voy-name').value=f.name||'';$('voy-waypoints').value=f.coordinates||EXAMPLE;$('voy-departure').value=f.departure||'';$('voy-speed').value=f.speed||14;$('voy-threshold').value=f.threshold||3;$('voy-compare').checked=f.compare!==false;$('voy-deadline').value=f.deadline||'';V.restoring=true;try{performance.restore(f.performance||{enabled:false});}finally{V.restoring=false;}
  if(f.route_plan_id){const ticket=++V.planLoad,source=getSource();V.manual=f.manual||null;api('/v1/routing/plans/'+encodeURIComponent(f.route_plan_id)).then(plan=>{if(ticket===V.planLoad&&source===getSource())loadRoutePlan(plan);}).catch(e=>{if(ticket===V.planLoad)$('voy-error').textContent='저장 항로 복원 실패: '+e.message;});}
 }
 restoreStored();renderPortContext();
 return {usePort,activate(){mapInit();if(getMeta())setMeta(getMeta());},setMeta,pause,sourceChanged(){routing.sourceChanged({notify:false});V.planLoad++;pause();V.cycle=null;V.report=null;V.ship?.remove();$('voy-comparison').innerHTML='';$('voy-detail').hidden=true;$('voy-table-panel').hidden=true;$('voy-methods').innerHTML='';$('voy-now').innerHTML='';$('voy-progress').textContent='0 / 0';$('voy-result-message').textContent='예보 원천을 전환했습니다. 이 범위의 항로를 다시 분석하세요.';restoreStored();dirty({keepPending:true,noSave:true});}};
}
