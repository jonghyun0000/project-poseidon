import {esc,finite,fmt,dateLabel} from './domain.js';

const list=value=>Array.isArray(value)?value:[];
const count=value=>finite(value)&&+value>=0?Number(value).toLocaleString('ko-KR'):'—';
const scored=row=>finite(row?.n)&&+row.n>0;
const tile=(label,value,note)=>'<div class="metric-tile"><label>'+esc(label)+'</label><strong>'+esc(value)+'</strong><small>'+esc(note)+'</small></div>';

// Each source owns its own result. A failed or slow source cannot erase another.
export async function loadGlobalEvidence({api,isCurrent,onResult}){
  const endpoints={buoy:'/v1/global/validation',altimeter:'/v1/global/validation/altimeter'};
  return await Promise.allSettled(Object.entries(endpoints).map(async([kind,path])=>{
    let report;
    try{report=await api(path);}
    catch(error){report={state:'unavailable',message:'조회 실패: '+(error?.message||'응답을 받지 못했습니다.')};}
    if(isCurrent())onResult(kind,report);
  }));
}

function scoreTable(rows,heading,label){
  return '<div class="table-wrap"><table><thead><tr><th>'+esc(heading)+'</th><th>RMSE m</th><th>편향 m</th><th>대조 쌍</th><th>고유 구간</th><th>위성 통과</th><th>표본 상태</th></tr></thead><tbody>'
    +rows.map(row=>'<tr><th scope="row">'+esc(label(row))+'</th><td>'+fmt(scored(row)?row.rmse_m:null,3)+'</td><td>'+fmt(scored(row)?row.bias_m:null,3)+'</td><td>'+count(row.n)+'</td><td>'+count(scored(row)?row.n_unique_observations:0)+'</td><td>'+count(scored(row)?row.n_tracks:0)+'</td><td><span class="tag '+(scored(row)?'':'pending')+'">'+(scored(row)?'표본 존재':'표본 없음')+'</span></td></tr>').join('')+'</tbody></table></div>';
}

function regions(report){
  const rows=[...list(report.by_region)];
  for(const label of list(report.coverage?.missing_regions))if(!rows.some(row=>(row.label||row.region)===label))rows.push({label,n:0});
  return rows;
}

export function altimeterMap(points){
  const coordinates=new Map();
  for(const point of list(points)){
    if(!finite(point.lat)||Math.abs(+point.lat)>90||!finite(point.lon)||+point.lon< -180||+point.lon>360)continue;
    const lon=((Number(point.lon)+180)%360+360)%360-180;
    coordinates.set(point.block_id||(+point.lat).toFixed(6)+','+lon.toFixed(6),{lat:+point.lat,lon});
  }
  if(!coordinates.size)return '';
  const x=lon=>40+(lon+180)*2,y=lat=>24+(90-lat)*2;
  const latitude=[-60,-30,0,30,60].map(lat=>'<path d="M40 '+y(lat)+'H760"/><text x="30" y="'+(y(lat)+3)+'" text-anchor="end">'+(lat===0?'0°':Math.abs(lat)+'°'+(lat<0?'S':'N'))+'</text>').join('');
  const longitude=[-180,-120,-60,0,60,120,180].map(lon=>'<path d="M'+x(lon)+' 24V384"/><text x="'+x(lon)+'" y="402" text-anchor="middle">'+(lon===0?'0°':Math.abs(lon)+'°'+(lon<0?'W':'E'))+'</text>').join('');
  const dots=[...coordinates.values()].map(point=>'<circle cx="'+x(point.lon).toFixed(2)+'" cy="'+y(point.lat).toFixed(2)+'" r="1.8"><title>'+fmt(point.lat,3)+'°, '+fmt(point.lon,3)+'°</title></circle>').join('');
  return '<div class="ga-map"><div class="gv-section-title"><h3>위성 구간 좌표 분포</h3><span>'+count(coordinates.size)+'개 구간</span></div><svg class="gv-station-map" viewBox="0 0 800 416" role="img" aria-label="실제 제공된 '+coordinates.size+'개 위성 구간의 위도·경도 분포. 점 주변을 검증된 해역으로 표시하지 않습니다."><rect x="40" y="24" width="720" height="360" rx="2" class="gv-map-ocean"/><g class="gv-graticule">'+latitude+longitude+'</g><g class="ga-map-dots">'+dots+'</g></svg><p class="micro">점은 제공된 위성 구간 좌표입니다. 점 사이를 검증된 면적으로 채우지 않으며, 비어 있는 해역의 정확도를 나타내지 않습니다.</p></div>';
}

function downloads(report){
  const allowed=value=>typeof value==='string'&&/^\/v1\/global\/validation\/altimeter\/(?:report\.json|pairs\.csv)(?:\?[^\s<>]*)?$/.test(value);
  const links={report:report.downloads?.report||report.downloads?.json,pairs:report.downloads?.pairs||report.downloads?.csv};
  return [['report','위성 보고서 JSON'],['pairs','위성 대조 표본 CSV']].filter(([key])=>allowed(links[key])).map(([key,label])=>'<a class="report-button" href="'+esc(links[key])+'" download>'+label+' ↓</a>').join('');
}

const acquisitionLabels={
  files_listed:'목록에 있는 파일',headers_downloaded:'메타데이터 확보 파일',files_selected:'시간 범위로 선택한 파일',
  download_bytes:'수신 용량 · 바이트',eligible_unique_blocks:'대조 가능한 고유 구간',failures_count:'수신·처리 실패 기록',
  provider_missing_block_pairs:'원천 예보 결측으로 제외 · 구간·예보 대조 쌍',
  blocks_below_minimum_samples:'유효 관측 수 부족 · 궤도·시각 창 구간',
  rows_outside_all_time_windows:'모든 대조 시간 창 밖 · 원본 행',
  accepted:'품질 기준 통과',invalid_time:'유효하지 않은 시각',invalid_position:'유효하지 않은 좌표',
  duplicate_time:'중복 시각',missing_or_invalid_flags:'품질 플래그 결측·무효',quality_flag_rejected:'품질 플래그 기준 제외',
  missing_hs:'Hs 결측',negative_hs:'음수 Hs',hs_above_8:'Hs 8 m 초과',missing_rms:'RMS 결측',
  rms_outside_range:'RMS 범위 밖',missing_coast_distance:'해안 거리 결측',within_50km_of_coast:'해안 거리 50 km 미만',
  after_cutoff:'대조 시각 마감 이후',
  files_requested:'요청 파일',files_downloaded:'수신 파일',files_available:'확보 파일',
  tracks_requested:'요청한 위성 통과',tracks_downloaded:'수신한 위성 통과',tracks_available:'확보한 위성 통과',
  raw_observations:'원본 관측',raw_points:'원본 관측점',valid_observations:'품질 기준 통과 관측',valid_points:'품질 기준 통과 관측점',
  blocks:'집계한 구간',blocks_available:'확보한 구간',blocks_scored:'대조된 구간',
  forecast_frames_requested:'요청 예보 시각 파일',forecast_frames_available:'확보 예보 시각 파일',
  candidate_pairs:'대조 후보 쌍',qc_rejected:'품질 기준 제외',invalid_flags:'품질 플래그 제외',
  near_coast:'해안 거리 기준 제외',near_coast_points:'해안 거리 기준 제외 관측점',
  insufficient_block_samples:'구간 내 관측 수 부족',outside_sensor_range:'센서 파고 범위 밖',
  no_forecast_within_tolerance:'시간 허용차 내 예보 없음',provider_missing:'유효 해양 격자 부족',
};

function acquisition(report){
  const source=report.acquisition||{};
  const numbers=Object.entries(source).filter(([,value])=>finite(value));
  if(Array.isArray(source.failures))numbers.push(['failures_count',source.failures.length]);
  const groups=[['자료 확보',numbers],['제외 사유',Object.entries(source.exclusions||{})],
    ['관측 품질 분류 · 행마다 첫 사유 1개',Object.entries(source.row_qc_exclusive||source.qc_counts||source.observation_qc||{})],
    ['품질 규칙별 해당 행 · 같은 행 중복 집계 가능',Object.entries(source.row_qc_overlapping_rule_hits||{})]];
  return groups.filter(([,rows])=>rows.length).map(([title,rows])=>'<h3>'+title+'</h3><div class="table-wrap"><table><thead><tr><th>항목</th><th>기록 수</th></tr></thead><tbody>'+rows.map(([key,value])=>'<tr><th scope="row">'+esc(acquisitionLabels[key]||key)+'</th><td>'+count(value)+'</td></tr>').join('')+'</tbody></table></div>').join('');
}

function protocolDescription(protocol){
  if(protocol.id!=='sentinel3a-open-ocean-hs-v1')return '<p>이 보고서의 자세한 관측 선별·구간 집계 조건은 원본 프로토콜에서 확인할 수 있습니다.</p>';
  const qc=protocol.qc||{},block=protocol.block||{};
  return '<p>위성 원천의 품질 플래그를 검사하고 해안에서 '+fmt(qc.minimum_coast_distance_km??50,0)+' km 이상 떨어진 관측을 사용합니다. 위성 진행 방향의 약 '+fmt(block.length_km??30,0)+' km 구간에서 유효 관측 '+count(block.minimum_valid_samples??3)+'개 이상을 평균하며, 예보 유효 시각과 ±'+count(protocol.tolerance_minutes??30)+'분 안에서 대조합니다. 예보와 관측은 같은 측정 좌표 집합에서 평균하고 예보 시간 보간은 하지 않습니다. 구간 평균은 순간 관측점이나 부이 관측과 공간 규모가 다릅니다.</p>';
}

function conditionSummary(report){
  const scoredError=row=>scored(row)&&finite(row.rmse_m);
  const worstRegion=list(report.by_region).filter(scoredError).sort((a,b)=>Number(b.rmse_m)-Number(a.rmse_m))[0];
  const lowerBound=row=>Number.parseFloat(row.band||row.label);
  const highestBand=list(report.by_hs).filter(row=>scoredError(row)&&Number.isFinite(lowerBound(row))).sort((a,b)=>lowerBound(b)-lowerBound(a))[0];
  const notes=[];
  if(worstRegion)notes.push('이 표본에서 해역별 최대 RMSE: <b>'+esc(worstRegion.label||worstRegion.region)+' '+fmt(worstRegion.rmse_m,3)+' m</b> (고유 구간 '+count(worstRegion.n_unique_observations)+'개)');
  if(highestBand){
    const label=report.protocol?.id==='sentinel3a-open-ocean-hs-v1'&&['6_plus','6+'].includes(highestBand.band)?'6–8 m':highestBand.label||highestBand.band;
    notes.push('채점된 가장 높은 파고대: <b>'+esc(label)+' · RMSE '+fmt(highestBand.rmse_m,3)+' m</b> (고유 구간 '+count(highestBand.n_unique_observations)+'개)');
  }
  return notes.length?'<div class="ga-condition-summary">'+notes.map(note=>'<p>'+note+'</p>').join('')+'</div>':'';
}

export function globalAltimeterMarkup(report){
  const available=report&&report.state!=='unavailable'&&scored(report.overall);
  const heading='<div class="gv-section-title"><h2>위성 고도계 · Sentinel-3A 관측 대조</h2><span class="tag pending">'+(available?'일부 궤도·조건 대조':'결과 미확보')+'</span></div>';
  if(!available){
    return '<section class="report-section ga-section">'+heading+'<p class="ga-intro">위성 관측 대조 결과 미확보</p><p>'+esc(report?.message||'유효한 위성 구간과 예보의 대조 결과가 아직 없습니다.')+'</p><p class="micro">위성과 부이는 별도의 관측 원천입니다. 아래 부이 결과는 위성 자료의 수신 상태와 별개로 확인할 수 있습니다.</p></section>';
  }
  const o=report.overall,p=report.protocol||{};
  const byLead=list(report.by_lead),byHs=list(report.by_hs);
  const knownProtocol=p.id==='sentinel3a-open-ocean-hs-v1';
  const cycles=list(p.forecast_records).map(record=>record.cycle).filter(Boolean).sort();
  const cards=tile('위성 대조 Hs RMSE',fmt(o.rmse_m,3)+' m','위성 구간·예보 대조 쌍 가중')
    +tile('위성 대조 Hs MAE',fmt(o.mae_m,3)+' m','절대 오차 평균')
    +tile('위성 대조 Hs 편향',fmt(o.bias_m,3)+' m','예보 − 위성 관측')
    +tile('위성 대조 쌍',count(o.n)+'쌍',count(o.n_tracks)+'회 위성 통과 · '+count(o.n_cycles)+'예보 사이클');
  return '<section class="report-section ga-section">'+heading
    +'<p class="ga-provider">'+esc(report.model||'NOAA GFS-Wave')+' · '+esc(report.observation_provider||'Sentinel-3A 위성 고도계')+'</p>'
    +'<p class="ga-intro">해안에서 떨어진 원양 구간의 Hs 대조입니다. 부이 RMSE와 합산하지 않습니다.</p>'
    +'<div class="metric-grid ga-metrics">'+cards+'</div>'
    +conditionSummary(report)
    +'<div class="gv-sample-note"><span>고유 위성 구간 <b>'+count(o.n_unique_observations)+'개</b> · 예보 유효 시각 <b>'+count(o.n_valid_times)+'개</b></span><span>같은 위성 구간을 여러 예보 사이클과 대조할 수 있습니다. 위성 통과 횟수는 관측소 수가 아닙니다.</span></div>'
    +'<div class="ga-scope"><span>채점된 구간 평균 Hs <b>'+fmt(o.observed_min_m,2)+'–'+fmt(o.observed_max_m,2)+' m</b></span><span>생성 '+esc(dateLabel(report.generated_at))+'</span></div>'
    +altimeterMap(report.block_points||report.blockpoints||report.trackpoints)
    +'<h3>해역별 위성 대조</h3>'+scoreTable(regions(report),'해역',row=>row.label||row.region)
    +'<div class="notice">이번 위성 표본으로 전 지구·전 계절의 정확도가 확정되지는 않습니다. '+(knownProtocol?'센서 Hs 0–8 m 범위의 품질 기준을 적용한 파일럿으로, 8 m를 넘는 파랑의 정확도는 채점하지 않았습니다.':'센서와 관측 선별 범위는 이 보고서의 프로토콜에 한정됩니다.')+'</div>'
    +'<details class="ga-details"><summary>리드·파고대·품질 조건 및 재현 자료</summary>'
    +'<h3>예보 리드별 대조</h3>'+(byLead.length?scoreTable(byLead,'리드',row=>'+'+row.lead_h+' h'):'<p>리드별 결과가 제공되지 않았습니다.</p>')
    +'<h3>관측 구간 평균 파고대별 대조</h3>'+(byHs.length?scoreTable(byHs,'관측 Hs',row=>knownProtocol&&['6_plus','6+'].includes(row.band)?'6–8 m · 8 m 초과 QC 제외':row.label||row.band):'<p>파고대별 결과가 제공되지 않았습니다.</p>')
    +'<h3>이번 파일럿의 대조 조건</h3>'+protocolDescription(p)
    +'<dl class="gv-protocol">'+[['프로토콜',p.id||'—'],['예보 기준 사이클',(p.cycle_start||cycles[0]||'—')+' – '+(p.cycle_end||cycles.at(-1)||'—')+' UTC'],['대조 시각 마감',dateLabel(p.cutoff_utc)],['실행 식별자',report.run_id||'—']].map(([label,value])=>'<dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd>').join('')+'</dl>'
    +acquisition(report)+'<p class="micro">수집 파일·원본 관측점·30 km 구간·예보 대조 쌍은 집계 단위가 다릅니다. 위 기록을 합산하지 않습니다.</p>'
    +'<ul class="ga-caveats">'+list(report.caveats).map(note=>'<li>'+esc(note)+'</li>').join('')+'<li>이 자료는 외부 관측과의 대조이며 기관의 인증이나 상업 항해 승인을 의미하지 않습니다.</li></ul>'
    +'<div class="gv-downloads">'+downloads(report)+'</div></details></section>';
}
