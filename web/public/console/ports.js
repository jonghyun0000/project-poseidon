import {esc} from './domain.js';
import {PORT_PAGE_SIZE,located,portSearchPath,portListMarkup,portDetailMarkup,portMetaMarkup,sourceMarkup,locatedCollection,mapBounds,latestTask,countryOptionsMarkup} from './ports-domain.js';

export function initPorts({api,onUsePort}){
 const $=id=>document.getElementById(id);
 const S={started:false,meta:null,items:[],total:0,offset:0,selected:null,selectedId:null,map:null,mapReady:false,geo:null,timer:null,mapCountry:null};
 const requests={meta:latestTask(),list:latestTask(),detail:latestTask(),geo:latestTask()};
 const count=v=>Number(v).toLocaleString('ko-KR');
 const filters=()=>({q:$('ports-query').value.trim(),country:$('ports-country').value,offset:S.offset});
 const setStatus=(text,error=false)=>{$('ports-search-status').textContent=text;$('ports-search-status').classList.toggle('inline-error',error);};
 function setEmptyDetail(){
  S.selected=null;S.selectedId=null;requests.detail.invalidate();
  $('ports-detail').innerHTML='<div class="section-label">PORT REFERENCE</div><div class="ports-detail-empty"><svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="12" r="4"/><path d="M24 16v24M16 22h16M9 29c0 8 7 13 15 13s15-5 15-13M9 29l-2 7M9 29l7 3M39 29l2 7M39 29l-7 3"/></svg><h2>항구를 선택하세요</h2><p>목록이나 지도의 항구를 선택하면<br>좌표·식별자·자료 원천을 확인할 수 있습니다.</p></div><p class="ports-reference-note">이 카탈로그는 항구를 찾는 기준 자료입니다. 항만 접근로와 선석 정보는 별도 확인이 필요합니다.</p>';
  drawSelected();
 }
 function renderList(){
  $('ports-list').innerHTML=portListMarkup(S.items,S.selectedId);
  $('ports-list').querySelectorAll('[data-port-id]').forEach(button=>button.onclick=()=>selectPort(button.dataset.portId));
  $('ports-prev').disabled=S.offset===0;
  $('ports-next').disabled=S.offset+PORT_PAGE_SIZE>=S.total;
  $('ports-page').textContent=S.total?`${count(S.offset+1)}–${count(Math.min(S.offset+S.items.length,S.total))} / ${count(S.total)}`:'0 / 0';
 }
 function search(reset=false){
  if(reset)S.offset=0;
  const query=filters();
  setStatus('항구 검색 중…');$('ports-list').setAttribute('aria-busy','true');
  $('ports-prev').disabled=true;$('ports-next').disabled=true;
  return requests.list.run(()=>api(portSearchPath(query)),data=>{
   S.items=Array.isArray(data.items)?data.items:[];S.total=Number(data.total)||0;
   $('ports-list').removeAttribute('aria-busy');$('ports-retry').hidden=true;renderList();
   setStatus(`${query.q?'“'+query.q+'” · ':''}${count(S.total)}건 검색됨`);
  },error=>{
   S.items=[];S.total=0;$('ports-list').removeAttribute('aria-busy');renderList();
   $('ports-list').innerHTML='<div class="ports-empty"><b>목록을 불러오지 못했습니다</b><span>연결 상태를 확인하고 다시 시도하세요.</span></div>';
   setStatus(error.message,true);$('ports-retry').hidden=false;
  });
 }
 function selectedGeo(){
  return {type:'FeatureCollection',features:located(S.selected)?[{type:'Feature',geometry:{type:'Point',coordinates:[Number(S.selected.lon),Number(S.selected.lat)]},properties:{}}]:[]};
 }
 function drawSelected(){if(S.mapReady)S.map.getSource('port-selected')?.setData(selectedGeo());}
 function focusPort(){
  if(!located(S.selected)||!S.map)return;
  S.map.flyTo({center:[Number(S.selected.lon),Number(S.selected.lat)],zoom:Math.max(S.map.getZoom(),8),duration:650});
 }
 function selectPort(id){
  S.selected=null;S.selectedId=id;renderList();drawSelected();
  $('ports-detail').innerHTML='<div class="section-label">PORT REFERENCE</div><p class="ports-loading" role="status">항구 자료 확인 중…</p>';
  return requests.detail.run(()=>api('/v1/ports/'+encodeURIComponent(id)),port=>{
   S.selected=port;S.selectedId=port.id;
   $('ports-detail').innerHTML=portDetailMarkup(port,S.meta);renderList();drawSelected();focusPort();
   const helmButton=document.createElement('button');helmButton.type='button';helmButton.className='outline';helmButton.textContent='선박 조종에서 항구 보기';helmButton.disabled=!located(port);helmButton.onclick=()=>onUsePort(port,'helm');$('ports-detail').append(helmButton);
   $('ports-detail-title').focus({preventScroll:window.innerWidth>800});
   $('ports-focus').onclick=focusPort;
   $('ports-detail').querySelectorAll('[data-port-role]').forEach(button=>button.onclick=async()=>{
    if(!located(S.selected))return;
    const selected=S.selected;
    try{await onUsePort(selected,button.dataset.portRole);}
    catch(error){if(S.selected?.id===selected.id)$('ports-use-error').textContent=error.message;}
   });
  },error=>{
   $('ports-detail').innerHTML=`<div class="section-label">PORT REFERENCE</div><p class="inline-error" role="alert">${esc(error.message)}</p><button type="button" id="ports-detail-retry" class="outline">항구 자료 다시 불러오기</button>`;
   $('ports-detail-retry').onclick=()=>selectPort(id);
  });
 }
 function drawGeo(){if(S.mapReady&&S.geo)S.map.getSource('port-catalog')?.setData(S.geo);}
 function fitWorld(){S.map?.fitBounds([[-180,-85],[180,85]],{padding:18,duration:400});}
 function fitCountry(){
  if(!S.mapReady)return;
  const bounds=S.mapCountry&&mapBounds(S.geo);
  if(bounds)S.map.fitBounds(bounds,{padding:45,maxZoom:8,duration:600});
  else fitWorld();
 }
 function loadGeo(){
  const country=$('ports-country').value;
  $('ports-map-count').textContent='항구 위치 불러오는 중';
  return requests.geo.run(()=>api('/v1/ports/geojson'+(country?'?'+new URLSearchParams({country}):'')),data=>{
   const changed=country!==S.mapCountry;
   S.geo=locatedCollection(data);S.mapCountry=country;drawGeo();
   if(changed&&!S.selected)fitCountry();
   const name=country?$('ports-country').selectedOptions[0].textContent.replace(/ · [\d,]+$/,''):'전 세계';
   $('ports-map-count').textContent=`${name} · 좌표 ${count(S.geo.features.length)}건`;
   $('ports-map-retry').hidden=true;
  },error=>{
   // A failed new filter cannot leave the old country's points labelled as the new one.
   S.geo={type:'FeatureCollection',features:[]};drawGeo();
   $('ports-map-count').textContent='항구 위치 조회 실패 · '+error.message;
   $('ports-map-retry').hidden=false;
  });
 }
 function initMap(){
  if(S.map)return;
  if(typeof maplibregl==='undefined'){$('ports-map-error').hidden=false;$('ports-map-error').textContent='지도를 불러오지 못했습니다. 항구 검색과 항로 선택은 사용할 수 있습니다.';return;}
  try{
   S.map=new maplibregl.Map({container:'ports-map',renderWorldCopies:false,center:[15,23],zoom:1.3,minZoom:0,attributionControl:true,style:{version:8,glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,maxzoom:19,attribution:'© OpenStreetMap contributors'}},layers:[{id:'background',type:'background',paint:{'background-color':'#d5e4ea'}},{id:'base',type:'raster',source:'base',paint:{'raster-saturation':-.85,'raster-contrast':-.12,'raster-opacity':.8}}]}});
   S.map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
   S.map.on('load',()=>{
    S.mapReady=true;
    S.map.addSource('port-catalog',{type:'geojson',data:S.geo||{type:'FeatureCollection',features:[]},cluster:true,clusterMaxZoom:11,clusterRadius:40});
    S.map.addLayer({id:'port-clusters',type:'circle',source:'port-catalog',filter:['has','point_count'],paint:{'circle-color':['step',['get','point_count'],'#529699',100,'#287b82',1000,'#195363'],'circle-radius':['step',['get','point_count'],17,100,22,1000,28],'circle-stroke-width':1.5,'circle-stroke-color':'#fff','circle-opacity':.9}});
    S.map.addLayer({id:'port-cluster-count',type:'symbol',source:'port-catalog',filter:['has','point_count'],layout:{'text-field':['get','point_count_abbreviated'],'text-font':['Open Sans Semibold'],'text-size':11},paint:{'text-color':'#fff'}});
    S.map.addLayer({id:'port-points',type:'circle',source:'port-catalog',filter:['!',['has','point_count']],paint:{'circle-color':'#147b80','circle-radius':['interpolate',['linear'],['zoom'],2,3.5,8,5.5],'circle-stroke-width':1.4,'circle-stroke-color':'#fff'}});
    S.map.addSource('port-selected',{type:'geojson',data:selectedGeo()});
    S.map.addLayer({id:'port-selection-ring',type:'circle',source:'port-selected',paint:{'circle-radius':12,'circle-color':'#fff','circle-opacity':.25,'circle-stroke-color':'#103c52','circle-stroke-width':2}});
    S.map.addLayer({id:'port-selection',type:'circle',source:'port-selected',paint:{'circle-radius':6,'circle-color':'#103c52','circle-stroke-color':'#fff','circle-stroke-width':2}});
    S.map.on('click','port-clusters',async event=>{
     const feature=event.features?.[0];if(!feature)return;
     try{const zoom=await S.map.getSource('port-catalog').getClusterExpansionZoom(feature.properties.cluster_id);S.map.easeTo({center:feature.geometry.coordinates,zoom});}catch{}
    });
    S.map.on('click','port-points',event=>{const id=event.features?.[0]?.properties?.id;if(id)selectPort(id);});
    for(const layer of ['port-clusters','port-points']){S.map.on('mouseenter',layer,()=>S.map.getCanvas().style.cursor='pointer');S.map.on('mouseleave',layer,()=>S.map.getCanvas().style.cursor='');}
    S.map.resize();if(located(S.selected))focusPort();else if(S.geo)fitCountry();
   });
   S.map.on('error',event=>{if(event.sourceId==='base'){$('ports-map-error').hidden=false;$('ports-map-error').textContent='배경 지도 수신이 지연되고 있습니다. 항구 목록에서 위치를 확인할 수 있습니다.';}});
  }catch(error){$('ports-map-error').hidden=false;$('ports-map-error').textContent='지도 초기화 실패 · '+error.message;}
 }
 function loadMeta(){
  return requests.meta.run(()=>api('/v1/ports/meta'),meta=>{
   if(meta.status==='unavailable')throw new Error('항구 카탈로그가 아직 준비되지 않았습니다.');
   S.meta=meta;$('ports-metrics').innerHTML=portMetaMarkup(meta);
   const previous=$('ports-country').value;
   $('ports-country').innerHTML=countryOptionsMarkup(meta.countries);
   $('ports-country').value=previous;
   $('ports-sources').innerHTML=(meta.sources||[]).map(sourceMarkup).join('');
   const limitations=Array.isArray(meta.limitations)?meta.limitations:[meta.limitations].filter(Boolean);
   $('ports-coverage-note').textContent=limitations.filter(x=>typeof x==='string').join(' ')||'공식 원천에 등록된 항구·항만 시설을 제공합니다. 소규모 항구·시설의 전수 수록을 보장하지 않습니다.';
  },error=>{$('ports-coverage-note').textContent='등록 범위를 확인하지 못했습니다. '+error.message;});
 }
 async function refresh(){
  S.started=true;clearTimeout(S.timer);
  await Promise.allSettled([loadMeta(),search(),loadGeo()]);
 }
 function activate(){initMap();S.map?.resize();if(!S.started)return refresh();}
 function changed(){
  clearTimeout(S.timer);requests.list.invalidate();S.offset=0;
  $('ports-prev').disabled=true;$('ports-next').disabled=true;
  setStatus('검색어 입력 중…');S.timer=setTimeout(()=>search(true),250);
 }
 $('ports-search-form').onsubmit=event=>{event.preventDefault();clearTimeout(S.timer);search(true);};
 $('ports-query').oninput=changed;
 $('ports-country').onchange=()=>{
  clearTimeout(S.timer);requests.list.invalidate();requests.geo.invalidate();setEmptyDetail();
  S.geo={type:'FeatureCollection',features:[]};drawGeo();search(true);loadGeo();
 };
 $('ports-clear').onclick=()=>{
  $('ports-query').value='';$('ports-country').value='';clearTimeout(S.timer);setEmptyDetail();search(true);loadGeo();$('ports-query').focus();
 };
 $('ports-prev').onclick=()=>{S.offset=Math.max(0,S.offset-PORT_PAGE_SIZE);search();$('ports-list').scrollTop=0;};
 $('ports-next').onclick=()=>{S.offset+=PORT_PAGE_SIZE;search();$('ports-list').scrollTop=0;};
 $('ports-retry').onclick=()=>search();
 $('ports-map-retry').onclick=()=>{initMap();loadGeo();};
 $('ports-world').onclick=fitWorld;
 $('ports-refresh').onclick=refresh;
 document.querySelectorAll('[data-port-query]').forEach(button=>button.onclick=()=>{
  $('ports-query').value=button.dataset.portQuery;clearTimeout(S.timer);search(true);
 });
 setEmptyDetail();
 return {activate,refresh};
}
