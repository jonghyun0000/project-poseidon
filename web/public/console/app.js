import {finite,fmt,esc,validPoint,dateLabel,csvData,sampleValue,summarizeForecast} from './domain.js';
import {chartMarkup} from './charts.js';
import {initVoyage} from './voyage.js';
import {initPorts} from './ports.js?v=helm-32';
import {initHelm} from './helm.js?v=33.3';
import {normalizeLongitude,OCEAN_VIEWS} from './global-domain.js';
import {globalValidationMarkup} from './global-validation.js';
import {globalAltimeterMarkup,loadGlobalEvidence} from './global-altimeter.js';
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search);
const S={source:params.get('source')==='regional'||params.has('cycle')&&!params.has('source')?'regional':'global',meta:null,points:[],selected:null,lead:Math.max(0,Number(params.get('lead'))||0),tz:'UTC',variable:'hs',threshold:3,map:null,mapReady:false,markers:[],stations:[],stationMarkers:[],observations:null,observationError:null,cycles:null,skill:null,status:null,showWave:true,showStations:false,pointRequest:0,refreshing:false,view:'analysis',pinned:/^\d{8}T\d{2}$/.test(params.get('cycle')||'')?params.get('cycle'):null};
const isGlobal=()=>S.source==='global',periodKey=()=>isGlobal()?'primary_period':'tp',directionKey=()=>isGlobal()?'primary_direction':'dirp';
$('source-select').value=S.source;
function readStored(key,fallback){try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}}
function save(){try{localStorage.setItem('poseidon.console.points',JSON.stringify(S.points.map(({lat,lon,name,stationId})=>({lat,lon,name,stationId}))));}catch{}syncURL();}
function syncURL(){const q=new URLSearchParams();q.set('source',S.source);if(S.view!=='analysis')q.set('view',S.view);if(S.pinned)q.set('cycle',S.pinned);q.set('lead',S.lead);S.points.forEach(p=>q.append('pt',`${p.lat.toFixed(3)},${p.lon.toFixed(3)}`));history.replaceState(null,'','?'+q);}
async function api(path,options={}){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);try{const r=await fetch(path,{...options,signal:controller.signal});if(!r.ok){let message=`HTTP ${r.status}`;try{const d=await r.json();message=typeof d.detail==='string'?d.detail:message;}catch{}throw new Error(message);}return await r.json();}catch(e){throw new Error(e.name==='AbortError'?'요청 시간 초과. 다시 시도해 주세요.':e.message);}finally{clearTimeout(timer);}}
function apiMessage(message){return ({'land or dry point':'육지 또는 파랑 값이 없는 지점입니다. 해상 지점을 선택하세요.','point outside forecast domain':'예보 해역 밖입니다. 해역 전체에서 범위를 확인하세요.'})[message]||message;}
function currentPoint(){return S.points.find(p=>p.id===S.selected)||null;}
function currentItem(){return currentPoint()?.data?.items?.[S.lead]||null;}
function coords(p){return `${Math.abs(p.lat).toFixed(3)}°${p.lat<0?'S':'N'}  ${Math.abs(p.lon).toFixed(3)}°${p.lon<0?'W':'E'}`;}
function renderPoints(){
 $('point-count').textContent=`${S.points.length} / 8`;
 $('point-list').innerHTML=S.points.map((p,i)=>{const value=sampleValue(p.data?.items?.[S.lead],'hs',{source:S.source});return `<div class="point-card ${p.id===S.selected?'active':''}"><button class="remove-point" data-remove="${p.id}" aria-label="${esc(p.name)} 삭제">×</button><button data-point="${p.id}" style="padding:0;text-align:left;width:100%"><div class="point-title"><span class="point-marker">${String(i+1).padStart(2,'0')}</span>${esc(p.name)}</div><div class="coords">${esc(coords(p))}</div><div class="mini-value"><b>${fmt(value,1)} <small>m</small></b><small>${p.loading?'조회 중':p.error?'조회 실패':p.data?'Hs · 예측':'미조회'}</small></div></button></div>`;}).join('');
 $('point-list').querySelectorAll('[data-point]').forEach(el=>el.onclick=()=>selectPoint(el.dataset.point));
 $('point-list').querySelectorAll('[data-remove]').forEach(el=>el.onclick=()=>{const id=el.dataset.remove;S.points=S.points.filter(p=>p.id!==id);if(S.selected===id)S.selected=S.points[0]?.id||null;save();render();drawMarkers();if(currentPoint()&&!currentPoint().data)loadPoint(currentPoint());});
}
function renderHeader(){
 if(!S.meta)return;const d=summarizeForecast(S.meta);
 $('forecast-state').textContent=isGlobal()&&d.current&&S.meta.freshness==='realtime'?'최근 확보 예보':d.label;
 $('forecast-period').textContent=`${isGlobal()?'NOAA 전 지구':'Poseidon 동아시아'} · 기준 ${S.meta.cycle} UTC · ${isGlobal()?'수신 '+dateLabel(S.meta.retrieved_at):'생산 '+dateLabel(S.meta.produced_at)} · ${S.meta.leads_h.at(-1).toFixed(0)}시간 예보`;
 $('source-note').textContent=isGlobal()?`0.25° · 6시간 간격 · 해빙·원천 결측은 빈칸${S.meta.refresh?.state==='downloading'?' · 새 사이클 수신 중 '+S.meta.refresh.completed+'/'+S.meta.refresh.total:S.meta.refresh?.state==='failed'?' · 새 예보 수신 실패 · 이전 완료 예보 유지':''}`:'Poseidon 지역 모델 · 동아시아 검증 자료 적용';
 $('map-domain').textContent=isGlobal()?'전 세계':'동아시아';$('map-resolution').textContent=isGlobal()?'NOAA · 0.25°':'L1 · 0.25°';$('point-level').textContent=isGlobal()?'GLOBAL':'L1';
 $('period-title').textContent=isGlobal()?'주 파주기 PERPW':'첨두주기 Tp';$('period-note').textContent=isGlobal()?'s · NOAA 원천':'s · 부분 검증';$('direction-title').textContent=isGlobal()?'주 파향 DIRPW':'첨두파향';$('direction-note').textContent=isGlobal()?'° · NOAA 원천':'° · 오는 방향';
 $('source-chain').innerHTML=isGlobal()?'<span>NOAA GFS-Wave 전 지구 원천</span><i>↓</i><span>시공간 조회 · 별도 물리 재계산 없음</span>':'<span>NOAA GFS / GFS-Wave</span><i>↓</i><span>Poseidon 물리 예보</span><i>↓</i><span>지점 샘플링 · L1</span>';
 const tabs=$('chart-tabs').querySelectorAll('button');tabs[1].dataset.var=periodKey();tabs[2].dataset.var=directionKey();$('station-layer').disabled=isGlobal();
 $('corrector-note').textContent=isGlobal()?'NOAA 원천 · Poseidon 보정 미적용':S.status?.corrector?.deployed?'통계 보정 배포됨 · 적용 여부는 지점 응답 기준':'AI 보정 미적용 · 물리 원값';
 const lead=S.meta.leads_h[S.lead],time=S.meta.valid_times[S.lead];
 $('lead-label').textContent=`+${fmt(lead,0)} h`;$('map-lead').textContent=`+${fmt(lead,0)} h`;
 $('valid-label').textContent=dateLabel(time,S.tz);$('map-time').textContent=dateLabel(time,S.tz);
 $('lead').max=S.meta.leads_h.length-1;$('lead').value=S.lead;
 $('timeline-ticks').innerHTML=Array.from({length:7},(_,i)=>Math.round(S.meta.leads_h.at(-1)*i/6)).map(v=>`<span>+${v} h</span>`).join('');
 $('prev').disabled=S.lead<=0;$('next').disabled=S.lead>=S.meta.leads_h.length-1;
 const gamma=S.meta.hs_color_gamma||1,max=S.meta.hs_color_max||12;
 $('legend-ticks').innerHTML=[0,2,4,8,12].map(v=>`<span style="left:${100*(v/max)**gamma}%">${v===12?'12+':v}</span>`).join('');
}
function renderInspector(){
 const p=currentPoint(),it=currentItem();$('selected-name').textContent=p?.name||'지점을 선택하세요';$('selected-coords').textContent=p?coords(p):'관심 지점 또는 지도에서 선택';
 const hs=sampleValue(it,'hs',{source:S.source});$('hs-value').textContent=fmt(hs);$('tp-value').textContent=fmt(sampleValue(it,periodKey()),1);$('dir-value').textContent=fmt(sampleValue(it,directionKey()),0);
 const exceed=finite(hs)&&hs>S.threshold;
 $('threshold-state').textContent=p?.loading?'지점 자료 조회 중':p?.error?p.error:!it?'지점 미선택':!finite(hs)?'이 시각의 유효한 파랑 값 없음':exceed?`사용자 비교선 ${S.threshold} m 초과`:`사용자 비교선 ${S.threshold} m 이하`;
 $('threshold-state').style.color=exceed?'#a77d38':'';
 const a=it?.applicability;
 const notes={in_range:'파고 검증 범위 안',sparse:'파고 검증 표본 부족',out_of_range:'파고 검증 범위 밖',no_coverage:'인근 검증 근거 부족'};
 $('applicability').textContent=p?.loading?'근거 조회 중':p?.error?'판정 불가 · 조회 실패':a?(isGlobal()?'전 지구 원천 · 지점별 정확도 미확정':notes[a.verdict]||a.note):'지점 미선택';
 $('applicability').classList.toggle('attention',!!a&&a.verdict!=='in_range');
 const nearest=a?.nearest_station;
 const evidence=[['예보 기준',p?.data?.cycle?`${p.data.cycle} UTC`:'—'],['유효 시각',dateLabel(it?.valid_time,S.tz)],['공간 해상도',isGlobal()?'NOAA 전 지구 · 0.25°':'L1 · 0.25°'],['파고 검증 관측소',nearest?`${nearest.station_id} · ${fmt(nearest.distance_km,0)} km`:'—'],['보정',p?.data?(p.data.corrected?'적용':'미적용'):'—']];
 $('evidence').innerHTML=evidence.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
 const obs=p?.stationId?S.observations?.find(o=>o.station_id===p.stationId):null;
 $('observation').innerHTML=S.observationError?'<p>관측 조회 실패. 새로고침으로 다시 확인하세요.</p>':S.observations===null?'<p>관측 자료 조회 중</p>':obs?`<b>${esc(obs.station_id)} · ${fmt(obs.value)} m</b><br>${esc(dateLabel(obs.ts,S.tz))}<br><span class="micro">${esc(obs.provider?.toUpperCase())} · QC 통과 · 관측 ${fmt(obs.age_h,1)}시간 경과 · 예보 시각과 다를 수 있습니다.</span>`:p?.stationId?'<p>이 관측소의 최근 48시간 유효 관측이 없습니다.</p>':'<p>선택 좌표의 직접 관측이 없습니다.</p><p class="micro">직접 관측은 별도 자료입니다. 전 지구 예보의 정확도 검증을 대신하지 않습니다.</p>';
 $('export').disabled=!p?.data||!!p?.loading;
 const data=p?.data;const items=data?.items||[];
 $('chart').innerHTML=p?.loading?'<div class="empty">예측 시계열 불러오는 중</div>':p?.error?`<div class="empty">${esc(p.error)}</div>`:items.length?chartMarkup(items,S.variable,S.lead,S.threshold,{source:S.source}):'<div class="empty">지점을 선택하면 예측 추세를 확인할 수 있습니다.</div>';
 $('chart-location').textContent=p?.name||'';
 $('chart-note').textContent=S.variable===directionKey()?(isGlobal()?'NOAA 주 파향 DIRPW · 진북 기준. 각도 평균으로 연결하지 않습니다.':'파향은 진북 기준, 파가 오는 방향입니다. 각도 평균으로 연결하지 않습니다.'):S.variable===periodKey()?(isGlobal()?'NOAA 주 파주기 PERPW. Tp 또는 Tm02와 동일한 변수로 취급하지 않습니다.':'첨두주기 · 부분 검증. 예측값의 신뢰구간을 표시한 그래프가 아닙니다.'):'실선: 예측값 · 점선: 사용자 비교선 · 세로선: 선택 시각';
 if(it?.missing?.[S.variable]==='not_stored')$('chart-note').textContent='선택 사이클에 이 변수가 저장되어 있지 않습니다. 값이 있는 변수나 다른 사이클을 선택하세요.';
}
function render(){renderHeader();renderPoints();renderInspector();}
function drawMarkers(){if(!S.mapReady)return;S.markers.forEach(m=>m.remove());S.markers=[];S.points.forEach((p,i)=>{const el=document.createElement('button');el.className='map-point'+(p.id===S.selected?' active':'');el.textContent=i+1;el.setAttribute('aria-label',p.name);el.onclick=e=>{e.stopPropagation();selectPoint(p.id);};S.markers.push(new maplibregl.Marker({element:el}).setLngLat([p.lon,p.lat]).addTo(S.map));});}
function drawStations(){S.stationMarkers.forEach(m=>m.remove());S.stationMarkers=[];if(!S.mapReady||!S.showStations||isGlobal())return;S.stations.filter(s=>s.has_location).forEach(s=>{const el=document.createElement('button');el.className='station-marker';el.title=s.station_id;el.setAttribute('aria-label',s.station_id);el.onclick=e=>{e.stopPropagation();addPoint(s.lat,s.lon,s.station_id,s.station_id);};S.stationMarkers.push(new maplibregl.Marker({element:el}).setLngLat([s.lon,s.lat]).addTo(S.map));});}
function drawField(){
 if(!S.mapReady||!S.meta)return;
 const lead=S.meta.leads_h[S.lead],cycle=encodeURIComponent(S.meta.cycle),b=S.meta.bounds;
 const specs=isGlobal()?['west','east'].map(part=>({id:'waves-'+part,url:`/v1/global/hs.png?cycle=${cycle}&lead=${lead}&part=${part}`,b:{west:part==='west'?-179.999:0,east:part==='west'?0:179.999,south:-85,north:85}})):[{id:'waves',url:`/v1/field/hs.png?cycle=${cycle}&lead=${lead}&level=L1&upscale=3`,b}];
 for(const id of ['waves','waves-west','waves-east'])if(!specs.some(v=>v.id===id)){if(S.map.getLayer(id))S.map.removeLayer(id);if(S.map.getSource(id))S.map.removeSource(id);}
 for(const spec of specs){const {id,url,b}=spec,coordinates=[[b.west,b.north],[b.east,b.north],[b.east,b.south],[b.west,b.south]];if(S.map.getSource(id))S.map.getSource(id).updateImage({url,coordinates});else{S.map.addSource(id,{type:'image',url,coordinates});S.map.addLayer({id,type:'raster',source:id,paint:{'raster-opacity':.72,'raster-fade-duration':0}});}S.map.setLayoutProperty(id,'visibility',S.showWave?'visible':'none');}
}
function initMap(){
 if(typeof maplibregl==='undefined'){$('map-error').hidden=false;$('map-error').textContent='지도 라이브러리를 불러오지 못했습니다. 지점 입력과 시계열 분석은 사용할 수 있습니다.';return;}
 S.map=new maplibregl.Map({container:'map',center:isGlobal()?[0,20]:[129,33.5],zoom:isGlobal()?1.25:3.8,attributionControl:true,style:{version:8,sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,maxzoom:19,attribution:'© OpenStreetMap contributors'}},layers:[{id:'background',type:'background',paint:{'background-color':'#d5e4ea'}},{id:'base',type:'raster',source:'base',paint:{'raster-saturation':-.82,'raster-contrast':-.14,'raster-opacity':.85}}]}});
 S.map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
 S.map.on('load',()=>{S.mapReady=true;drawField();drawMarkers();drawStations();if(isGlobal())S.map.fitBounds([[-180,-65],[180,75]],{padding:12,duration:0});});
 S.map.on('click',e=>addPoint(e.lngLat.lat,e.lngLat.lng));
 S.map.on('error',e=>{$('map-error').hidden=false;$('map-error').textContent=e.sourceId?.startsWith('waves')?'파고 레이어 조회 실패. 수치 자료와 구분해 확인하세요.':'일부 지도 자료를 불러오지 못했습니다.';});
 new ResizeObserver(()=>S.map.resize()).observe($('map'));
}
async function addPoint(lat,lon,name,stationId){
 if(!validPoint(lat,lon)){$('point-error').textContent='유효한 위도·경도를 입력하세요.';return;}
 lat=+lat;lon=normalizeLongitude(lon);const existing=S.points.find(p=>Math.abs(p.lat-lat)<.001&&Math.abs(p.lon-lon)<.001);if(existing){selectPoint(existing.id);return;}
 if(S.points.length>=8){$('point-error').textContent='관심 지점은 최대 8개입니다. 사용하지 않는 지점을 삭제해 주세요.';return;}
 const p={id:crypto.randomUUID(),lat,lon,name:name||`지점 ${S.points.length+1}`,stationId,data:null,loading:false,error:null};S.points.push(p);S.selected=p.id;save();$('point-error').textContent='';drawMarkers();render();if(S.meta)await loadPoint(p);
}
function selectPoint(id){S.selected=id;const p=currentPoint();if(p){$('lat').value=p.lat.toFixed(3);$('lon').value=p.lon.toFixed(3);if(S.mapReady)S.map.easeTo({center:[p.lon,p.lat],duration:400});if(!p.data&&!p.loading)loadPoint(p);}drawMarkers();render();}
async function loadPoint(p){
 const cycle=S.meta?.cycle,source=S.source;if(!cycle)return;p.loading=true;p.error=null;render();
 try{const d=await api(isGlobal()?`/v1/global/point?lat=${p.lat}&lon=${p.lon}&cycle=${encodeURIComponent(cycle)}`:`/v1/forecast/point?lat=${p.lat}&lon=${p.lon}&cycle=${encodeURIComponent(cycle)}&level=L1&vars=hs,tp,tm01,tm02,dirm,dirp`);if(S.meta?.cycle!==cycle||S.source!==source)return;p.data=d;}
 catch(e){if(S.meta?.cycle!==cycle||S.source!==source)return;p.data=null;p.error=apiMessage(e.message);}
 finally{if(S.meta?.cycle===cycle&&S.source===source){p.loading=false;render();}}
}
function setLead(index){if(!S.meta)return;S.lead=Math.min(S.meta.leads_h.length-1,Math.max(0,Math.round(index)));syncURL();render();drawField();}
function setView(view){S.view=view;['analysis','voyage','helm','ports','validation','operations'].forEach(v=>{$(v+'-view').hidden=v!==view;document.querySelector(`[data-view="${v}"]`).classList.toggle('active',v===view);});voyage.pause();helm.pause();syncURL();if(view==='analysis')S.map?.resize();if(view==='voyage')voyage.activate();if(view==='helm')helm.activate();if(view==='ports')ports.activate();if(view==='validation')loadValidation();if(view==='operations')loadOperations();}
function metric(label,value,note){return `<div class="metric-tile"><label>${esc(label)}</label><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;}
let validationRequest=0;
async function loadValidation(){
 const request=++validationRequest,source=S.source;
 const current=()=>request===validationRequest&&source===S.source;
 if(isGlobal()){
  $('validation-body').innerHTML='<div id="global-altimeter-body"><div class="empty" role="status">위성 관측 대조 결과 불러오는 중</div></div><h2 class="ga-buoy-heading">부이 관측 대조 · NDBC 배포 자료</h2><div id="global-buoy-body"><div class="empty" role="status">부이 관측 대조 결과 불러오는 중</div></div>';
  await loadGlobalEvidence({api,isCurrent:current,onResult:(kind,report)=>{
   if(kind==='altimeter')$('global-altimeter-body').innerHTML=globalAltimeterMarkup(report);
   else $('global-buoy-body').innerHTML=globalValidationMarkup(report);
  }});
  return;
 }
 $('validation-body').innerHTML='<div class="empty">관측 대조 결과 불러오는 중</div>';
 try{const [hs,tp,dir]=await Promise.all(['hs','tp','dir'].map(v=>api(`/v1/skill?var=${v}&ci=false`)));if(!current())return;S.skill=hs;
 const cards=metric('파고 RMSE',fmt(hs.rmse)+' m','전체 표본 가중 · 리드/해역 혼합')+metric('검증 표본',(hs.n||0).toLocaleString()+'건',`${hs.n_stations||0}개소 · ${hs.n_cycles||0}사이클`)+metric('주기 RMSE',fmt(tp.rmse)+' s','첨두주기 · 부분 검증')+metric('파향 원형 RMSE',fmt(dir.rmse,1)+'°','첨두파향 · 부분 검증');
 $('validation-body').innerHTML=`<div class="metric-grid">${cards}</div><section class="report-section"><h2>적용 범위와 미검증 조건</h2><p>파고 관측 범위 ${fmt(hs.observed_range?.min)}–${fmt(hs.observed_range?.max)} m · 상위 5% 경계 ${fmt(hs.observed_range?.p95)} m · 최대 채점 리드 ${fmt(hs.observed_range?.lead_h_max,0)} h.</p><div class="notice">이 수치만으로 상업 항해 적합성을 판단할 수 없습니다. 고파랑·태풍 통과·계절·원양 검증이 부족합니다. GFS-Wave는 초기장과 경계 입력이므로 독립적인 비교 모델이 아닙니다.</div><p>평균주기 Tm02와 평균파향은 검증 성적이 없습니다. 파향의 파고대별 성적과 표본 수는 기존 분석 화면에서 확인할 수 있습니다.</p><a class="report-button" href="/legacy">상세 검증 분석 열기 ↗</a></section><section class="report-section"><h2>리드별 파고 오차</h2><div class="table-wrap"><table><thead><tr><th>리드</th><th>RMSE m</th><th>편향 m</th><th>표본</th><th>관측소</th><th>표본 상태</th></tr></thead><tbody>${(hs.by_lead||[]).map(r=>`<tr><td>+${fmt(r.lead_h,0)} h</td><td>${fmt(r.rmse,3)}</td><td>${fmt(r.bias,3)}</td><td>${r.n}</td><td>${r.n_stations}</td><td><span class="tag ${r.sparse?'pending':''}">${r.sparse?'적은 표본':'표본 존재'}</span></td></tr>`).join('')}</tbody></table></div></section>`;
 }catch(e){if(!current())return;$('validation-body').innerHTML=`<div class="report-section">조회 실패: ${esc(e.message)}</div>`;}
}
async function loadOperations(){
 if(isGlobal()){const d=await api('/v1/global/status');$('operations-body').innerHTML=`<section class="report-section"><h2>전 지구 예보 수신 상태</h2><p>NOAA GFS-Wave · 0.25° · 6시간 간격 · 최대 384시간</p><dl><dt>사이클</dt><dd>${esc(d.cycle||'—')}</dd><dt>수신 파일</dt><dd>${d.completed||0} / ${d.total||65}</dd><dt>상태</dt><dd>${esc(d.state)}</dd><dt>상태 갱신 UTC</dt><dd>${esc(dateLabel(d.updated_at))}</dd></dl><p>${esc(d.error||'전체 65개 시각을 확보한 사이클만 공개합니다. 수신 중에는 이전 완료 사이클을 유지합니다.')}</p><p>콘솔 사용 중 새 사이클을 확인합니다. Poseidon 지역 모델의 생산 루프와 별도로 관리합니다.</p></section>`;return;}
 $('operations-body').innerHTML='<div class="empty">사이클 이력 불러오는 중</div>';
 try{const d=await api('/v1/system/forecast_cycles?limit=500');if(isGlobal())return;S.cycles=d;const rows=d.cycles||[],missing=rows.filter(r=>r.has_forcing&&r.has_boundary&&!r.has_l1),published=rows.filter(r=>r.has_l1);
 $('operations-body').innerHTML=`<div class="metric-grid">${metric('목록 내 예보 확보',published.length+'개','L1 산출물이 등록된 사이클')}${metric('입력 확보 · 예보 대기',missing.length+'개','강제장과 경계 모두 등록됨')}${metric('최신 L1 기준',d.latest_l1_cycle||'—','UTC · 생산 시각과 구분')}${metric('최근 관측 수신',S.observationError?'조회 실패':S.observations===null?'조회 중':S.observations.length+'개소','최근 48시간 조회 · 파고 · QC 통과')}</div><section class="report-section"><h2>생산·복구 이력</h2><p>입력 확보는 산출물 등록 여부입니다. 파일 무결성과 시간 커버리지 검사는 생산 단계에서 수행합니다. 과거 계산 이력은 현재 프로세스 생존 증거가 아닙니다.</p><div class="table-wrap"><table><thead><tr><th>예보 기준 UTC</th><th>바람 입력</th><th>파랑 경계</th><th>L1 예보</th><th>등록된 실행 이력</th><th>청정 표본</th><th>분석</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.cycle)}</td><td>${r.has_forcing?'확보':'—'}</td><td>${r.has_boundary?'확보':'—'}</td><td><span class="tag ${r.has_l1?'':'pending'}">${r.has_l1?'예보 확보':'미생산'}</span></td><td>${esc((r.runs||[]).filter(v=>v.engine!=='ingest').map(v=>v.status).join(' · ')||'—')}</td><td>${r.n_samples_clean?.hs||0}</td><td>${r.has_l1?`<button class="report-button" data-cycle="${esc(r.cycle)}">열기</button>`:'—'}</td></tr>`).join('')}</tbody></table></div></section>`;
 document.querySelectorAll('[data-cycle]').forEach(b=>b.onclick=()=>changeCycle(b.dataset.cycle));
 }catch(e){$('operations-body').innerHTML=`<div class="report-section">조회 실패: ${esc(e.message)}</div>`;}
}
async function changeCycle(cycle){if(S.source!=='regional'){S.source='regional';$('source-select').value=S.source;voyage.sourceChanged();}S.pinned=cycle||null;S.meta=null;S.points.forEach(p=>{p.data=null;p.loading=false;});syncURL();setView('analysis');await refresh(true);}
async function refresh(force=false){
 if(S.refreshing)return;S.refreshing=true;$('source-select').disabled=true;$('refresh').disabled=true;
 try{const query=S.pinned?'?cycle='+encodeURIComponent(S.pinned):'';const source=S.source;const meta=await api((isGlobal()?'/v1/global/meta':'/v1/field/meta')+query);if(source!==S.source)return;meta.source=source;
 if(S.meta&&meta.cycle!==S.meta.cycle&&!force){$('new-cycle').hidden=false;$('new-cycle').textContent='새 예보로 전환';$('new-cycle').onclick=()=>{S.meta=null;S.pinned=null;refresh(true);};return;}
 const changed=S.meta?.cycle!==meta.cycle;S.meta=meta;voyage.setMeta(meta);S.lead=Math.min(Number.isFinite(S.lead)?Math.round(S.lead):0,meta.leads_h.length-1);if(changed)S.points.forEach(p=>{p.data=null;p.error=null;});$('new-cycle').hidden=!(S.pinned&&meta.cycle!==meta.latest_cycle);if(!$('new-cycle').hidden){$('new-cycle').textContent='최신 예보로 전환';$('new-cycle').onclick=()=>changeCycle(null);}render();drawField();
 // 실패한 보조 자료는 지점 예보를 막지 않는다.
 const jobs=[api('/v1/system/status').then(d=>{S.status=d;renderHeader();}),api('/v1/skill/stations?var=hs').then(d=>{S.stations=d.stations||[];drawStations();}),api('/v1/obs/latest?var=hs&hours=48').then(d=>{S.observations=d;S.observationError=null;renderInspector();}).catch(e=>{S.observationError=e.message;renderInspector();throw e;})];
 const pointJobs=Promise.all(S.points.map(loadPoint));
 const errors=await Promise.allSettled(jobs);const failed=errors.filter(v=>v.status==='rejected').length;
 await pointJobs;
 $('sync-time').textContent=`${dateLabel(new Date().toISOString())} 동기화${failed?` · 보조 자료 ${failed}개 조회 실패`:''}`;
 }catch(e){$('forecast-state').textContent='예보 조회 실패';$('forecast-period').textContent=e.message;}
 finally{S.refreshing=false;$('refresh').disabled=false;$('source-select').disabled=false;}
}
function showTable(){const p=currentPoint();if(!p?.data)return;$('data-table').innerHTML=`<p class="muted">${esc(coords(p))} · ${esc(p.data.cycle)} UTC · 예측값</p><table><thead><tr><th>유효 시각 UTC</th><th>리드 h</th><th>파고 m</th><th>${isGlobal()?'주 파주기':'첨두주기'} s</th><th>Tm02 s</th><th>${isGlobal()?'주 파향':'첨두파향'} °</th><th>파고 검증 범위</th></tr></thead><tbody>${p.data.items.map(i=>`<tr><td>${esc(dateLabel(i.valid_time))}</td><td>${fmt(i.lead_h,2)}</td><td>${fmt(sampleValue(i,'hs',{source:S.source}),3)}</td><td>${fmt(sampleValue(i,periodKey()),2)}</td><td>${fmt(sampleValue(i,'tm02'),2)}</td><td>${fmt(sampleValue(i,directionKey()),1)}</td><td>${esc(({in_range:'범위 안',sparse:'표본 부족',out_of_range:'범위 밖',no_coverage:'근거 부족'})[i.applicability?.verdict]||'—')}</td></tr>`).join('')}</tbody></table>`;$('data-dialog').showModal();}
const voyage=initVoyage({api,getMeta:()=>S.meta,getSource:()=>S.source});
const helm=initHelm({api});
const ports=initPorts({api,onUsePort:(port,role)=>{if(role==='helm'){setView('helm');helm.usePort(port);}else{voyage.usePort(port,role);setView('voyage');}}});
$('voy-open-ports').onclick=()=>setView('ports');
async function changeSource(){if(S.refreshing)return;S.source=$('source-select').value;S.pinned=null;S.meta=null;S.lead=0;S.variable='hs';S.showStations=false;S.points.forEach(p=>{p.data=null;p.loading=false;p.error=null;});$('forecast-state').textContent='예보 원천 전환 중';$('forecast-period').textContent='';renderPoints();renderInspector();for(const id of ['waves','waves-west','waves-east'])if(S.map?.getLayer(id))S.map.setLayoutProperty(id,'visibility','none');$('map-error').hidden=true;$('chart-tabs').querySelectorAll('button').forEach(b=>b.classList.toggle('selected',b.dataset.var==='hs'));voyage.sourceChanged();syncURL();drawStations();await refresh(true);if(S.mapReady)S.map.easeTo({center:isGlobal()?[0,20]:[129,33.5],zoom:isGlobal()?1.25:3.8,duration:500});if(S.view==='validation')loadValidation();if(S.view==='operations')loadOperations();}
$('source-select').onchange=changeSource;
$('ocean-view').onchange=()=>{if($('ocean-view').value==='world'){S.map?.fitBounds([[-180,-65],[180,75]],{padding:12,duration:500});return;}const [lon,lat,zoom]=OCEAN_VIEWS[$('ocean-view').value];S.map?.easeTo({center:[lon,lat],zoom,duration:700});};
$('point-form').onsubmit=e=>{e.preventDefault();addPoint($('lat').value,$('lon').value);};
$('lead').oninput=e=>setLead(+e.target.value);$('prev').onclick=()=>setLead(S.lead-1);$('next').onclick=()=>setLead(S.lead+1);
$('timezone').onchange=e=>{S.tz=e.target.value;render();};
$('threshold').onchange=e=>{if(finite(e.target.value)&&+e.target.value>0&&+e.target.value<=30){S.threshold=+e.target.value;renderInspector();}else e.target.value=S.threshold;};
$('wave-layer').onclick=()=>{S.showWave=!S.showWave;$('wave-layer').classList.toggle('selected',S.showWave);$('wave-layer').setAttribute('aria-pressed',S.showWave);drawField();};
$('station-layer').onclick=()=>{S.showStations=!S.showStations;$('station-layer').classList.toggle('selected',S.showStations);$('station-layer').setAttribute('aria-pressed',S.showStations);drawStations();};
$('fit-map').onclick=()=>{if(S.meta&&S.mapReady){const b=S.meta.bounds;S.map.fitBounds([[b.west,b.south],[b.east,b.north]],{padding:25,duration:500});}};
$('refresh').onclick=async()=>{if(S.view==='ports'){await ports.refresh();return;}await refresh();if(S.view==='validation')await loadValidation();if(S.view==='operations')await loadOperations();};document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));
$('chart-tabs').querySelectorAll('button').forEach(b=>b.onclick=()=>{S.variable=b.dataset.var;document.querySelectorAll('[data-var]').forEach(t=>t.classList.toggle('selected',t===b));renderInspector();});
$('chart').onclick=e=>{const p=currentPoint();if(!p?.data)return;const box=$('chart').getBoundingClientRect(),x=(e.clientX-box.left)/box.width*680,lh=Math.max(0,Math.min(1,(x-38)/630))*S.meta.leads_h.at(-1);setLead(S.meta.leads_h.reduce((best,v,i)=>Math.abs(v-lh)<Math.abs(S.meta.leads_h[best]-lh)?i:best,0));};
$('export').onclick=()=>{const p=currentPoint();if(!p?.data)return;const blob=new Blob([csvData(p,p.data,new Date().toISOString())],{type:'text/csv;charset=utf-8'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`poseidon_${p.data.cycle}_${p.lat.toFixed(3)}_${p.lon.toFixed(3)}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('show-table').onclick=showTable;$('close-dialog').onclick=()=>$('data-dialog').close();
document.addEventListener('keydown',e=>{if(e.target.matches('input,select,textarea')||S.view!=='analysis'||$('data-dialog').open)return;if(e.key==='ArrowLeft')setLead(S.lead-1);if(e.key==='ArrowRight')setLead(S.lead+1);});
function tick(){$('clock').textContent=new Date().toISOString().slice(11,19)+' UTC';}tick();setInterval(tick,1000);
const fromURL=params.getAll('pt').map(v=>v.split(',')).filter(([a,b])=>validPoint(a,b));
const stored=readStored('poseidon.console.points',[]);
const initial=fromURL.length?fromURL.map(([lat,lon])=>({...((Array.isArray(stored)?stored:[]).find(p=>Math.abs(p.lat-lat)<.001&&Math.abs(p.lon-lon)<.001)||{}),lat,lon})):Array.isArray(stored)&&stored.length?stored:[{lat:34.5,lon:129,name:'대한해협'}];
for(const p of initial.slice(0,8))if(validPoint(p.lat,p.lon))await addPoint(p.lat,p.lon,p.name,p.stationId);
initMap();if(['voyage','helm','ports','validation','operations'].includes(params.get('view')))setView(params.get('view'));await refresh();setInterval(()=>refresh(),300000);
