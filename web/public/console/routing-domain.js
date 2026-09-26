import {esc,finite,fmt,dateLabel} from './domain.js';
import {coordinateLabel} from './global-domain.js';
import {countryDisplay,safeSourceURL} from './ports-domain.js';

export const AVOID_PASSAGES=[['malacca','말라카 해협'],['gibraltar','지브롤터 해협'],['babelmandeb','바브엘만데브 해협'],['dover','도버 해협'],['bering','베링 해협'],['magellan','마젤란 해협']];
export const MANDATORY_BLOCKED=['suez','panama','kiel','corinth','northwest','northeast'];
export const ROUTING_SCOPE_NOTE='항구 대표점과 항로망 접속점 사이의 구간은 미검증이며, 항해 거리·시간 계산에서 제외됩니다.';
export const CLOSED_PASSAGES_NOTE='수에즈·파나마·킬·코린트 운하와 북서·북동 극지 항로는 항상 제외합니다.';
const coordinate=p=>p&&typeof p.lat==='number'&&typeof p.lon==='number'&&Number.isFinite(p.lat)&&Number.isFinite(p.lon)&&Math.abs(p.lat)<=90&&Math.abs(p.lon)<=180;

export function portIssue(port){
 if(!port||typeof port.id!=='string'||!port.id)return '검색 결과에서 항구를 선택하세요.';
 if(port.coordinate_conflict||port.coordinate_status==='conflict'||port.merge_status==='coordinate_disagreement')return '원천 좌표가 불일치하는 항구는 자동 항로를 계산할 수 없습니다.';
 if(!coordinate(port))return '좌표가 없는 항구는 자동 항로를 계산할 수 없습니다.';
 return '';
}
export function routingSearchPath(query){
 return '/v1/ports?'+new URLSearchParams({q:query.trim(),limit:'8'});
}
export function routingPortLabel(port){return port?`${port.name||port.id}${port.unlocode?' · '+port.unlocode:''}`:'';}
export function routingOptionsMarkup(items,role,active=-1){
 if(!items.length)return '<div class="routing-search-empty" role="status">검색 결과가 없습니다. 다른 이름이나 UN/LOCODE를 입력하세요.</div>';
 return items.map((port,index)=>{
  const issue=portIssue(port);
  return `<button type="button" role="option" id="routing-${esc(role)}-option-${index}" data-routing-option="${index}" aria-selected="${index===active}"${issue?' aria-disabled="true"':''}><b>${esc(port.name||port.id)}</b><span>${esc(countryDisplay(port.country_code,port.country_name))} · ${esc(port.unlocode||port.country_code||port.id)}</span>${issue?`<small>${esc(issue)}</small>`:''}</button>`;
 }).join('');
}
export function routingRequest(departure,arrival,avoid=[],source='global'){
 if(source!=='global')throw new Error('자동 항로 계산은 전 세계 예보 범위에서 사용할 수 있습니다.');
 for(const [name,port] of [['출발항',departure],['도착항',arrival]]){const issue=portIssue(port);if(issue)throw new Error(`${name}: ${issue}`);}
 if(departure.id===arrival.id)throw new Error('출발항과 도착항을 서로 다르게 선택하세요.');
 const allowed=new Set(AVOID_PASSAGES.map(([id])=>id));
 if(!Array.isArray(avoid)||avoid.some(value=>!allowed.has(value)))throw new Error('회피할 해협을 다시 선택하세요.');
 return {departure_port_id:departure.id,arrival_port_id:arrival.id,avoid_passages:[...new Set(avoid)].sort()};
}
export function validateRoutingPlan(plan,request){
 if(!plan||plan.status!=='candidate'||typeof plan.plan_id!=='string'||!/^[a-f0-9]{64}$/.test(plan.plan_id))throw new Error('사용할 수 있는 항로 후보가 반환되지 않았습니다.');
 if(plan.scope!=='network_segment_only')throw new Error('항로망 구간의 계산 범위를 확인할 수 없습니다.');
 const actual=plan.request;
 if(!request||!actual||!Array.isArray(actual.avoid_passages)||!Array.isArray(request.avoid_passages))throw new Error('항로의 요청 조건을 확인할 수 없습니다.');
 const normalized=values=>JSON.stringify([...new Set(values)].sort());
 if(normalized(actual.avoid_passages)!==normalized(request.avoid_passages))throw new Error('회피 조건과 계산 결과가 다릅니다. 다시 계산하세요.');
 const blocked=plan.constraints?.blocked_passages,used=plan.constraints?.used_passages;
 if(!Array.isArray(blocked)||!Array.isArray(used)||[...MANDATORY_BLOCKED,...request.avoid_passages].some(value=>!blocked.includes(value))||used.some(value=>blocked.includes(value)))throw new Error('제외 통로 조건에 맞는 항로인지 확인할 수 없습니다.');
 if(!Array.isArray(plan.waypoints)||plan.waypoints.length<2||!plan.waypoints.every(coordinate)||!finite(plan.distance_nm)||Number(plan.distance_nm)<=0)throw new Error('항로 좌표와 거리를 확인할 수 없습니다. 다시 계산하세요.');
 for(const role of ['departure','arrival']){
  const end=plan[role];
  if(end?.port?.id!==request[role+'_port_id']||actual[role+'_port_id']!==request[role+'_port_id'])throw new Error('선택 항구와 계산 결과가 다릅니다. 항로를 다시 계산하세요.');
  if(!coordinate(end.attachment)||!finite(end.gap_nm)||Number(end.gap_nm)<0||end.scope!=='reference_gap_not_navigated')throw new Error('항구와 항로망 사이의 미검증 구간을 확인할 수 없습니다.');
 }
 return plan;
}
export function routingResultMarkup(plan){
 const connection=(role,label)=>{const end=plan[role];return `<div class="routing-connection"><b>${esc(label)} · ${esc(end.port.name)}</b><span>항로망 접속점 ${esc(coordinateLabel(end.attachment.lat,end.attachment.lon,3))}</span><small>항구 대표점과의 간격 <strong>${esc(fmt(end.gap_nm,1))} nm</strong> · 미검증</small></div>`;};
 const source=plan.network?.source_name||(typeof plan.network?.source==='string'?plan.network.source:plan.network?.source?.name)||'해상 항로망';
 const sourceURL=safeSourceURL(plan.network?.source_url),dataDate=String(plan.network?.source_gpkg_last_change||'').slice(0,10);
 const avoid=(plan.request?.avoid_passages||[]).map(id=>AVOID_PASSAGES.find(([key])=>key===id)?.[1]||id);
 return `<div class="routing-result-heading"><span>항로망 계산 결과 · 후보</span><strong>${esc(fmt(plan.distance_nm,1))}<small> nm</small></strong>${dataDate?`<p class="micro">항로망 원천 ${esc(dataDate)} · 현재 통항 미검증</p>`:''}</div>${connection('departure','출발')}${connection('arrival','도착')}<p class="routing-scope">${ROUTING_SCOPE_NOTE}</p><details class="routing-method"><summary>계산 근거와 한계</summary><p>${sourceURL?`<a href="${esc(sourceURL)}" target="_blank" rel="noopener noreferrer">${esc(source)} ↗</a>`:esc(source)}${plan.network?.version?' · '+esc(plan.network.version):''}${dataDate?'<br>원천 자료 시점 '+esc(dataDate):''}${plan.network?.source_commit_date?'<br>원천 저장소 판본 '+esc(String(plan.network.source_commit_date).slice(0,10)):''}<br>계산 ${esc(dateLabel(plan.created_at_utc))}</p>${avoid.length?`<p>추가 회피: ${avoid.map(esc).join(' · ')}</p>`:''}${(plan.limitations||[]).filter(v=>typeof v==='string').map(value=>`<p>${esc(value)}</p>`).join('')}<small>계획 ID ${esc(plan.plan_id)}</small></details>`;
}
