import {esc,finite} from './domain.js';
import {coordinateLabel} from './global-domain.js';

export const PORT_PAGE_SIZE=30;
export const PORT_REFERENCE_NOTE='항구 대표 좌표입니다. 항만 입구·선석·검증된 항로점이 아니므로, 해상 접근점과 중간 웨이포인트를 확인해야 합니다.';
export function located(port){return !!port&&finite(port.lat)&&finite(port.lon)&&Math.abs(Number(port.lat))<=90&&Math.abs(Number(port.lon))<=180;}
export function portCoordinates(port){return located(port)?coordinateLabel(Number(port.lat),Number(port.lon),4):port?.coordinate_status==='conflict'?'좌표 충돌 · 확인 필요':'좌표 미제공';}
export function portKind(port){return ({maritime:'해항',inland:'내륙항',port:'항구·항만 시설'})[port?.kind]||'항구·항만 시설';}
export function safeSourceURL(value){try{const url=new URL(value);return ['https:','http:'].includes(url.protocol)?url.href:null;}catch{return null;}}
export function countryDisplay(code,official=''){
 try{const name=new Intl.DisplayNames(['ko'],{type:'region',fallback:'none'}).of(code);return name||official||code;}
 catch{return official||code;}
}
export function countryOptionsMarkup(countries){
 return '<option value="">전 세계 모든 국가·지역</option>'+[...(countries||[])].sort((a,b)=>countryDisplay(a.code,a.name).localeCompare(countryDisplay(b.code,b.name),'ko')).map(c=>`<option value="${esc(c.code)}">${esc(countryDisplay(c.code,c.name))} · ${esc(c.code)} · ${Number(c.count).toLocaleString('ko-KR')}</option>`).join('');
}
export function portListQualityMarkup(port){
 const labels=[];
 if(port.coordinate_conflict||port.merge_status==='coordinate_disagreement')labels.push('원천 좌표 불일치');
 if(port.unlocode_status==='deleted')labels.push('삭제 상태 코드');
 if(['pending','not_listed'].includes(port.unlocode_status))labels.push('UN 코드 확인 필요');
 return labels.length?`<span class="ports-row-quality">${labels.map(label=>'<span>'+label+'</span>').join('')}</span>`:'';
}
export function portQualityMarkup(port){
 const distance=port.coordinate_conflict?.distance_km??port.source_coordinate_distance_km;
 const notes=[];
 if(port.coordinate_conflict||port.merge_status==='coordinate_disagreement')notes.push(`<b>원천 좌표 불일치${finite(distance)?' · '+Number(distance).toLocaleString('ko-KR',{maximumFractionDigits:1})+' km':''}</b><span>같은 UN/LOCODE의 원천별 대표 위치가 다릅니다. 위치를 확인한 뒤 항로에 사용하세요.</span>`);
 if(port.unlocode_status==='deleted')notes.push('<b>삭제 상태의 UN/LOCODE</b><span>WPI 시설 기록은 유지되지만 이 코드는 UN/LOCODE 원천에서 삭제 상태입니다.</span>');
 if(['pending','not_listed'].includes(port.unlocode_status))notes.push('<b>현재 UN 목록에서 코드 확인 필요</b><span>WPI가 제공한 코드를 보존했습니다. 현재 UN/LOCODE 목록에서 유효한 등록 코드로 확인되지 않았습니다.</span>');
 return notes.length?`<div class="ports-quality-note">${notes.map(n=>'<p>'+n+'</p>').join('')}</div>`:'';
}
const number=value=>Number.isFinite(Number(value))?Number(value).toLocaleString('ko-KR'):'—';
export function portSearchPath({q='',country='',offset=0}={}){
 const query=new URLSearchParams({q:q.trim(),limit:String(PORT_PAGE_SIZE),offset:String(Math.max(0,offset))});
 if(country)query.set('country',country);
 return '/v1/ports?'+query;
}
export function portListMarkup(items,selectedId){
 if(!items?.length)return '<div class="ports-empty"><b>검색 결과가 없습니다</b><span>다른 이름이나 UN/LOCODE로 검색해 보세요.</span></div>';
 return items.map(port=>`<button type="button" class="ports-row${port.id===selectedId?' selected':''}" data-port-id="${esc(port.id)}" aria-pressed="${port.id===selectedId}"><span class="ports-row-top"><b>${esc(port.name)}</b><span>${esc(port.unlocode||port.country_code||'—')}</span></span><span class="ports-row-country">${esc(countryDisplay(port.country_code,port.country_name))} · ${esc(portKind(port))}</span><span class="ports-row-coords">${esc(portCoordinates(port))}${located(port)?'':' · 지도 표시 불가'}</span>${portListQualityMarkup(port)}</button>`).join('');
}
function sourceInfo(entry,meta){
 const id=typeof entry==='string'?entry:entry?.id||entry?.source_id||entry?.source;
 const source=meta?.sources?.find(s=>s.id===id);
 return {...source,...(typeof entry==='object'&&entry?entry:{}),id};
}
export function sourceMarkup(source){
 const url=safeSourceURL(source.url||source.source_url),title=source.title||source.name||source.id||'자료 원천';
 const fetched=String(source.retrieved_at||'').replace('T',' ').slice(0,19);
 return `<div class="ports-source"><b>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)} ↗</a>`:esc(title)}</b><span>${esc(source.edition||source.version||'원천 판본 미기재')}${fetched?' · 수신 '+esc(fetched)+' UTC':''}</span>${source.sha256?`<code title="원천 SHA-256">${esc(source.sha256)}</code>`:''}</div>`;
}
export function portDetailMarkup(port,meta){
 const hasLocation=located(port);
 const aliases=(port.aliases||[]).filter(a=>typeof a==='string'&&a!==port.name);
 const missingNote=port.coordinate_status==='conflict'?'원천 좌표가 서로 달라 위치를 확정할 수 없습니다. 좌표를 임의로 선택하거나 항로점으로 추가하지 않습니다.':'원천에 좌표가 없어 항로점으로 추가할 수 없습니다. 위치를 임의로 추정하지 않습니다.';
 const provenance=(port.sources||[]).map(source=>sourceMarkup(sourceInfo(source,meta))).join('');
 const coordinateSource=typeof port.coordinate_source==='object'?port.coordinate_source?.title||port.coordinate_source?.id:port.coordinate_source;
 const coordinateTitle=meta?.sources?.find(s=>s.id===coordinateSource)?.title||coordinateSource;
 const localCountry=countryDisplay(port.country_code,port.country_name);
 const country=localCountry+(port.country_name&&localCountry!==port.country_name?' / '+port.country_name:'');
 return `<div class="section-label">PORT REFERENCE</div><h2 id="ports-detail-title" tabindex="-1">${esc(port.name)}</h2><p class="ports-detail-country">${esc(country)} · ${esc(portKind(port))}</p>${aliases.length?`<p class="ports-aliases">검색 별칭 · ${aliases.map(esc).join(' · ')}</p>`:''}${portQualityMarkup(port)}<dl class="ports-identifiers"><dt>UN/LOCODE</dt><dd>${esc(port.unlocode||'미제공')}</dd><dt>WPI 번호</dt><dd>${esc(port.wpi_id??'미제공')}</dd><dt>대표 좌표</dt><dd>${esc(portCoordinates(port))}</dd><dt>좌표 원천</dt><dd>${esc(coordinateTitle||'미제공')}</dd></dl><button type="button" id="ports-focus" class="outline"${hasLocation?'':' disabled'}>지도에서 항구 위치 보기</button><section class="ports-route-section"><h3>항로 시뮬레이션에 연결</h3><div class="ports-use-actions"><button type="button" data-port-role="departure"${hasLocation?'':' disabled'}>출발항으로</button><button type="button" data-port-role="arrival"${hasLocation?'':' disabled'}>도착항으로</button><button type="button" data-port-role="via"${hasLocation?'':' disabled'}>경유항으로</button></div><p class="ports-reference-note">${hasLocation?PORT_REFERENCE_NOTE:missingNote}</p><p id="ports-use-error" class="inline-error" role="alert"></p></section><section class="ports-detail-sources"><h3>등록 근거</h3>${provenance||'<p class="micro">원천 정보가 제공되지 않았습니다.</p>'}</section>`;
}
export function portMetaMarkup(meta){
 return `<div><span>항만 위치·시설</span><strong>${number(meta.total)}</strong></div><div><span>좌표 보유</span><strong>${number(meta.located)}</strong></div><div><span>국가·지역</span><strong>${number(meta.countries?.length)}</strong></div>`;
}
export function locatedCollection(data){
 return {type:'FeatureCollection',features:(data?.features||[]).filter(f=>f.geometry?.type==='Point'&&located({lon:f.geometry.coordinates?.[0],lat:f.geometry.coordinates?.[1]})&&f.properties?.id).map(f=>({type:'Feature',geometry:{type:'Point',coordinates:f.geometry.coordinates.slice(0,2).map(Number)},properties:{id:String(f.properties.id),name:String(f.properties.name||''),country_code:String(f.properties.country_code||''),unlocode:String(f.properties.unlocode||'')}}))};
}
// Every search/detail/map channel owns a gate. Superseded success AND error are ignored.
export function mapBounds(collection){
 const coords=collection?.features?.map(f=>f.geometry.coordinates)||[];
 if(!coords.length)return null;
 const lons=coords.map(p=>p[0]),lats=coords.map(p=>Math.max(-85,Math.min(85,p[1])));
 const west=Math.min(...lons),east=Math.max(...lons);
 return east-west>180?null:[[west,Math.min(...lats)],[east,Math.max(...lats)]];
}
export function latestTask(){
 let revision=0;
 return {
  invalidate(){revision++;},
  async run(task,success,failure){
   const current=++revision;
   try{const result=await task();if(current!==revision)return false;success(result);return true;}
   catch(error){if(current!==revision)return false;failure(error);return true;}
  },
 };
}
