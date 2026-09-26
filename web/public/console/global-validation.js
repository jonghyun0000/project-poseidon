import {esc,finite,fmt,dateLabel} from './domain.js';

const list = value => Array.isArray(value) ? value : [];
const count = value => finite(value) && +value >= 0 ? Number(value).toLocaleString('ko-KR') : '—';
const metric = (value, digits=3) => fmt(value,digits);
const hasSamples = row => finite(row?.n) && +row.n > 0;
const ciText = value => Array.isArray(value) && value.length===2 && value.every(finite)
  ? '['+metric(value[0])+', '+metric(value[1])+'] m' : '산출되지 않음';
const status = row => '<span class="tag '+(hasSamples(row)?'':'pending')+'">'+(hasSamples(row)?'표본 존재':'표본 없음')+'</span>';
const tile = (label,value,note) => '<div class="metric-tile"><label>'+esc(label)+'</label><strong>'+esc(value)+'</strong><small>'+esc(note)+'</small></div>';

function errorCells(row){
  return '<td>'+metric(hasSamples(row)?row.rmse_m:null)+'</td><td>'+metric(hasSamples(row)?row.mae_m:null)+'</td><td>'+metric(hasSamples(row)?row.bias_m:null)+'</td><td>'+count(row.n)+'</td><td>'+count(hasSamples(row)?row.n_unique_observations:0)+'</td><td>'+count(row.n_stations)+'</td><td>'+status(row)+'</td>';
}

function scoreTable(rows,heading,key){
  const body=rows.map(row=>'<tr class="'+(hasSamples(row)?'':'gv-unscored')+'"><th scope="row">'+esc(key(row))+'</th>'+errorCells(row)+'</tr>').join('');
  return '<div class="table-wrap"><table><thead><tr><th scope="col">'+esc(heading)+'</th><th scope="col">RMSE m</th><th scope="col">MAE m</th><th scope="col">편향 m</th><th scope="col">대조 쌍</th><th scope="col">고유 관측</th><th scope="col">관측소</th><th scope="col">표본 상태</th></tr></thead><tbody>'+body+'</tbody></table></div>';
}

function leadRows(report){
  const rows=list(report.by_lead);
  const leads=[...new Set([...list(report.protocol?.leads_h),...rows.map(r=>r.lead_h)])].filter(finite).map(Number).sort((a,b)=>a-b);
  return leads.map(lead=>rows.find(r=>Number(r.lead_h)===lead)||{lead_h:lead,n:0,n_stations:0});
}

export function compactLeads(values){
  const leads=[...new Set(list(values).filter(finite).map(Number))].sort((a,b)=>a-b);
  const ranges=[];
  for(const lead of leads){
    const previous=ranges.at(-1);
    if(previous&&lead===previous[1]+6)previous[1]=lead;
    else ranges.push([lead,lead]);
  }
  return ranges.map(([first,last])=>'+'+first+(first===last?'':'–'+last)+' h').join(' · ');
}

function regionRows(report){
  const rows=[...list(report.by_region)];
  for(const label of list(report.coverage?.missing_regions)){
    if(!rows.some(r=>(r.label||r.region)===label))rows.push({region:label,label,n:0,n_stations:0});
  }
  return rows;
}

export function stationMap(stations){
  const valid=list(stations).filter(s=>hasSamples(s)&&finite(s.lat)&&Math.abs(+s.lat)<=90&&finite(s.lon)&&+s.lon>=-180&&+s.lon<=360);
  const x=lon=>40+((Number(lon)+180)%360+360)%360*2;
  const y=lat=>24+(90-Number(lat))*2;
  const latitude=[-60,-30,0,30,60].map(lat=>'<path d="M40 '+y(lat)+'H760"/><text x="30" y="'+(y(lat)+3)+'" text-anchor="end">'+(lat===0?'0°':Math.abs(lat)+'°'+(lat<0?'S':'N'))+'</text>').join('');
  const longitude=[-180,-120,-60,0,60,120,180].map(lon=>'<path d="M'+(40+(lon+180)*2)+' 24V384"/><text x="'+(40+(lon+180)*2)+'" y="402" text-anchor="middle">'+(lon===0?'0°':Math.abs(lon)+'°'+(lon<0?'W':'E'))+'</text>').join('');
  const dots=valid.map(s=>'<circle cx="'+x(s.lon).toFixed(2)+'" cy="'+y(s.lat).toFixed(2)+'" r="3.5"><title>'+esc(s.station_id)+' · '+esc(s.name||'')+' · '+count(s.n)+'쌍</title></circle>').join('');
  return '<svg class="gv-station-map" viewBox="0 0 800 416" role="img" aria-label="유효 대조 표본이 있는 '+valid.length+'개 관측소의 위도와 경도 분포. 빈 영역은 검증된 영역이 아닙니다."><rect x="40" y="24" width="720" height="360" rx="2" class="gv-map-ocean"/><g class="gv-graticule">'+latitude+longitude+'</g><g class="gv-map-labels"><text x="145" y="204">태평양</text><text x="340" y="204">대서양</text><text x="565" y="204">인도양</text><text x="695" y="204">태평양</text></g><g class="gv-map-dots">'+dots+'</g></svg>';
}

const exclusionLabels={
  forecast_targets_not_yet_valid:'대조 시각 마감 이후의 예보 시각 · 파일 단위',
  forecast_unavailable_pairs:'예보 파일 미확보 · 대조 시도',
  observation_download_failed_pairs:'관측 파일 수신 실패 · 대조 시도',
  no_observation_within_tolerance_pairs:'시간 허용차 내 관측 없음 · 대조 시도',
  deployment_unknown_or_transition_pairs:'배치 좌표 이력 불명 또는 전환일 · 대조 시도',
  land_screen_unavailable_pairs:'육지 검사 불가 · 대조 시도',
  on_land_pairs:'육지 판정 · 대조 시도',
  provider_mask_missing_pairs:'유효 해양 격자 부족 · 대조 시도',
  observation_download_failed:'관측 파일 수신 실패',station_download_failed:'관측소 파일 수신 실패',
  no_hs_observations:'유효 Hs 관측 없음',no_observation_within_tolerance:'시간 허용차 내 관측 없음',
  no_observation:'대조할 관측 없음',outside_tolerance:'관측 시각 허용차 초과',
  observation_out_of_range:'관측 물리 범위 밖',invalid_hs:'유효하지 않은 Hs',
  future_valid_time:'관측 기준 시각 이후',future_observation:'아직 도래하지 않은 관측 시각',
  forecast_unavailable:'예보 파일 미확보',forecast_download_failed:'예보 파일 수신 실패',
  provider_missing:'원천 예보 결측',land_detected:'육지 판정',no_wet_support:'유효 해양 격자 부족',
  duplicate_observation:'중복 관측',missing_coordinates:'관측소 좌표 없음',outside_model_domain:'모델 격자 밖',
};

const observationQCLabels={rows:'원본 관측 행',missing_hs:'Hs 결측 행',invalid_time:'유효하지 않은 시각',
  outside_gross_range:'Hs 물리 범위 밖 · 0–40 m 외',conflicting_timestamp_rows:'같은 시각의 상충 관측 행',
  identical_duplicate_rows:'같은 시각·같은 값의 중복 행',usable_rows:'사용 가능한 고유 관측 행'};

function acquisitionMarkup(acquisition={}){
  const entries=Object.entries(acquisition.exclusions||{});
  const qc=Object.entries(acquisition.observation_row_qc||{});
  return '<section class="report-section"><h2>자료 확보와 제외 기록</h2><div class="gv-acquisition">'
    +tile('탐색한 관측소',count(acquisition.stations_discovered)+'개소','등록 목록 기준')
    +tile('관측 파일 확보',count(acquisition.stations_downloaded)+'개소','유효 Hs 보유 '+count(acquisition.stations_with_hs)+'개소')
    +tile('예보 시각 파일',count(acquisition.forecast_frames_available)+' / '+count(acquisition.forecast_frames_requested),'확보 / 요청')
    +'</div><p>아래 항목은 서로 다른 처리 단계의 제외 기록입니다. 관측 행과 예보·관측 대조 시도는 집계 단위가 다르므로 합산하지 않습니다.</p>'
    +(entries.length?'<div class="table-wrap"><table><thead><tr><th>제외 사유</th><th>기록 수</th></tr></thead><tbody>'+entries.map(([reason,n])=>'<tr><th scope="row">'+esc(exclusionLabels[reason]||reason)+'</th><td>'+count(n)+'</td></tr>').join('')+'</tbody></table></div>':'<p class="micro">제외 기록이 제공되지 않았습니다.</p>')
    +(qc.length?'<details class="gv-qc"><summary>원본 관측 행 품질·중복 기록</summary><p>관측 파일 전체의 행 수입니다. 이번 검증 기간에 대조된 표본 수와 다르며 결측·시각 오류 사유는 중복될 수 있습니다.</p><div class="table-wrap"><table><thead><tr><th>관측 처리 항목</th><th>행 수</th></tr></thead><tbody>'+qc.map(([reason,n])=>'<tr><th scope="row">'+esc(observationQCLabels[reason]||reason)+'</th><td>'+count(n)+'</td></tr>').join('')+'</tbody></table></div></details>':'')+'</section>';
}

function downloadsMarkup(downloads={}){
  // Only the local validation export endpoints are allowed as download targets.
  const allowed=href=>typeof href==='string'&&/^\/v1\/global\/validation\/(?:report\.json|pairs\.csv)(?:\?[^\s<>]*)?$/.test(href);
  return [['report','검증 보고서 JSON'],['pairs','대조 표본 CSV']].filter(([key])=>allowed(downloads[key])).map(([key,label])=>'<a class="report-button" href="'+esc(downloads[key])+'" download>'+esc(label)+' ↓</a>').join('');
}

function protocolMarkup(report){
  const p=report.protocol||{},c=report.coverage||{};
  return '<section class="report-section"><h2>검증 조건과 재현 자료</h2><dl class="gv-protocol">'
    +[['프로토콜',p.id||'—'],['예보 기준 사이클',(p.cycle_start||'—')+' – '+(p.cycle_end||'—')+' UTC'],['채점 리드',list(p.leads_h).map(h=>'+'+h+' h').join(' · ')||'—'],['명목 행 시각 허용차','±'+count(p.tolerance_minutes)+'분'],['대조 유효 시각',dateLabel(c.valid_start)+' – '+dateLabel(c.valid_end)],['대조 시각 마감',dateLabel(p.cutoff_utc)],['보고서 생성',dateLabel(report.generated_at)],['실행 식별자',report.run_id||'—']].map(([label,value])=>'<dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd>').join('')
    +'</dl><p>허용차는 공개 관측 행에 기록된 명목 UTC 시각 기준입니다. 실제 파랑 취득 구간과 같다고 가정하지 않습니다. 대조 시각 마감은 자료 수신 시각이 아닙니다.</p><p>Hs만 같은 위치·유효 시각에서 대조합니다. PERPW 주 파주기는 부이의 첨두주기 DPD와 같은 변수가 아니므로 이 성적에 포함하지 않습니다.</p><div class="gv-downloads">'+downloadsMarkup(report.downloads)+'</div><div class="gv-sources"><a href="https://www.ndbc.noaa.gov/measdes.shtml" target="_blank" rel="noopener noreferrer">NDBC 관측 변수 정의 ↗</a><a href="https://www.nco.ncep.noaa.gov/pmb/products/wave/" target="_blank" rel="noopener noreferrer">NOAA GFS-Wave 원천 ↗</a></div></section>';
}

function stationsMarkup(stations,regions){
  const regionLabel=region=>list(regions).find(r=>r.region===region)?.label||region;
  const rows=[...list(stations)].sort((a,b)=>String(a.station_id).localeCompare(String(b.station_id)));
  return '<details class="report-section gv-stations"><summary>관측소별 대조 결과 <span>'+count(rows.filter(hasSamples).length)+'개소에 유효 표본</span></summary><div class="table-wrap"><table><thead><tr><th>관측소 · 이름</th><th>해역</th><th>마지막 대조 좌표 · 위도 / 경도</th><th>대조 쌍</th><th>사이클</th><th>RMSE m</th><th>편향 m</th></tr></thead><tbody>'
    +rows.map(s=>'<tr><th scope="row">'+esc(s.station_id)+'<small>'+esc(s.name||'')+'</small></th><td>'+esc(s.label||regionLabel(s.region)||'—')+'</td><td>'+fmt(hasSamples(s)?s.lat:null,3)+' / '+fmt(hasSamples(s)?s.lon:null,3)+'</td><td>'+count(s.n)+'</td><td>'+count(s.n_cycles)+'</td><td>'+metric(hasSamples(s)?s.rmse_m:null)+'</td><td>'+metric(hasSamples(s)?s.bias_m:null)+'</td></tr>').join('')+'</tbody></table></div></details>';
}

export function globalValidationMarkup(report){
  if(!report||report.state==='unavailable'||!hasSamples(report.overall)){
    return '<section class="report-section gv-heading"><div><h2>전 지구 원천 · 관측 대조</h2><span class="tag pending">채점 결과 미확보</span></div><p>'+esc(report?.message||'아직 유효한 예보·관측 대조 결과가 없습니다. 결과가 생성되면 해역과 리드별 오차 및 표본 범위를 여기서 확인할 수 있습니다.')+'</p><p>예보가 제공되는 범위와 정확도가 검증된 범위는 다릅니다. 자료 미확보를 0 m 오차로 표시하지 않습니다.</p></section>';
  }
  const o=report.overall,c=report.coverage||{},ci=o.cycle_bootstrap_95;
  const regions=regionRows(report),missingRegions=list(c.missing_regions);
  const uncovered=list(c.unscored_leads_h);
  const cards=tile('Hs RMSE',metric(o.rmse_m)+' m','관측 대조 쌍 가중 · 리드·해역 혼합')
    +tile('Hs MAE',metric(o.mae_m)+' m','절대 오차 평균')
    +tile('Hs 편향',metric(o.bias_m)+' m','예보 − 관측 · 양수는 과대 예측')
    +tile('대조 쌍',count(o.n)+'쌍',count(o.n_stations)+'개소 · '+count(o.n_cycles)+'사이클');
  return '<section class="report-section gv-heading"><div><h2>전 지구 원천 · 관측 대조</h2><span class="tag pending">일부 조건 검증</span></div><p>'+esc(report.model||'NOAA GFS-Wave')+' · 유의파고 Hs · Poseidon 보정 미적용</p><div class="notice">전 지구 정확도 확정 전입니다. 아래 성적은 확보한 부이·기간·파고 조건에 해당하며, 선택한 항로 또는 모든 해역의 오차를 나타내지 않습니다.</div></section>'
    +'<div class="metric-grid gv-metrics">'+cards+'</div>'
    +'<div class="gv-sample-note"><span>고유 관측 <b>'+count(o.n_unique_observations)+'건</b> · 유효 시각 <b>'+count(o.n_valid_times)+'개</b></span><span>같은 관측이 여러 예보 사이클·리드와 대조될 수 있습니다. 대조 쌍 수는 독립 관측 수가 아닙니다.</span></div>'
    +'<section class="report-section"><h2>오차 추정의 범위</h2><div class="gv-uncertainty"><div><small>사이클 군집 부트스트랩 95% 구간 · RMSE</small><b>'+esc(ciText(ci?.rmse_m))+'</b></div><div><small>사이클 군집 부트스트랩 95% 구간 · 편향</small><b>'+esc(ciText(ci?.bias_m))+'</b></div><div><small>채점된 관측 파고</small><b>'+metric(o.observed_min_m,2)+' – '+metric(o.observed_max_m,2)+' m</b></div><div><small>산포 지수 SI · 편향 제거 RMSE / 관측 평균</small><b>'+metric(o.scatter_index)+'</b></div><div><small>편향 제거 RMSE</small><b>'+metric(o.centered_rmse_m)+' m</b></div><div><small>관측소별 RMSE의 동일 가중 평균</small><b>'+metric(o.station_balanced_mean_rmse_m)+' m</b></div></div><p class="micro">이 구간은 검증 통계의 변동성입니다. 개별 지점 예보의 95% 오차 범위가 아닙니다. 인접 사이클과 같은 기상 사건의 의존성이 남을 수 있습니다. 관측소별 동일 가중 평균은 각 관측소의 RMSE를 한 번씩 평균한 보조 통계로, 전체 대조 쌍 가중 RMSE와 집계 방식이 다릅니다.</p></section>'
    +'<section class="report-section"><div class="gv-section-title"><h2>검증 표본의 지리적 분포</h2><span>'+count(o.n_stations)+'개소</span></div>'+stationMap(report.stations)+'<p class="micro">점은 유효 대조 표본이 있는 부이의 마지막 대조 시점 배치 좌표입니다. 주변 해역까지 검증되었다는 의미는 아닙니다. 등거리 경위도 좌표도이며 해도와 수심을 제공하지 않습니다.</p>'
    +(missingRegions.length?'<div class="gv-gaps"><b>표본 없는 해역</b><span>'+missingRegions.map(esc).join(' · ')+'</span></div>':'<div class="gv-gaps"><b>공간 대표성</b><span>해역별 표본이 있어도 전 해역·계절·기상 조건의 검증을 뜻하지 않습니다.</span></div>')
    +scoreTable(regions,'해역',r=>r.label||r.region)+'</section>'
    +'<section class="report-section"><h2>예보 리드별 오차</h2><p>리드 0의 분석장은 전체 성적에서 제외합니다. 서로 다른 리드의 결과는 표본과 기상 조건이 다를 수 있습니다.</p>'
    +(uncovered.length?'<div class="gv-gaps"><b>미채점 리드</b><span>'+esc(compactLeads(uncovered))+' (6 h 간격)'+'</span></div>':'')
    +scoreTable(leadRows(report),'리드',r=>'+'+r.lead_h+' h')+'</section>'
    +'<section class="report-section"><h2>관측 파고대별 오차</h2><p>고파고 구간은 관측값으로 분류합니다. 낮은 파고에서 얻은 전체 평균을 고파랑 조건의 성적으로 확대하지 않습니다.</p>'
    +(list(report.by_hs).length?scoreTable(report.by_hs,'관측 Hs',r=>r.label||r.band):'<div class="notice">파고대별 결과가 제공되지 않았습니다. 고파고 정확도를 판단할 수 없습니다.</div>')+'</section>'
    +stationsMarkup(report.stations,regions)+acquisitionMarkup(report.acquisition)
    +'<section class="report-section gv-caveats"><h2>해석에 남는 제한</h2><ul>'+list(report.caveats).map(note=>'<li>'+esc(note)+'</li>').join('')+'<li>이 보고서는 관측 대조 결과이며 독립 기관의 인증이나 상업 항해 투입 승인을 의미하지 않습니다.</li></ul></section>'
    +protocolMarkup(report);
}
