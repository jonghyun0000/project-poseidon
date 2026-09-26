import test from 'node:test';
import assert from 'node:assert/strict';
import {globalAltimeterMarkup,loadGlobalEvidence,altimeterMap} from '../../web/public/console/global-altimeter.js';

const sample=()=>({schema_version:'global-altimeter-validation-1.0',state:'partial',run_id:'20260911T180000Z',generated_at:'2026-09-11T18:00:00Z',
  protocol:{id:'sentinel3a-open-ocean-hs-v1',cycle_start:'20260903T00',cycle_end:'20260910T00',cutoff_utc:'2026-09-11T16:00:00Z'},
  overall:{n:40,n_tracks:3,n_cycles:8,n_unique_observations:15,n_valid_times:12,rmse_m:.4,mae_m:.3,bias_m:-.1,observed_min_m:.5,observed_max_m:5.6},
  by_region:[{region:'indian',label:'인도양 구역',n:40,n_tracks:3,n_unique_observations:15,rmse_m:.4,bias_m:-.1}],
  by_lead:[{lead_h:6,n:20,n_tracks:3,n_unique_observations:15,rmse_m:.3,bias_m:-.1}],
  by_hs:[{band:'6+',label:'6 m 이상',n:0,n_tracks:0,n_unique_observations:0,rmse_m:null,bias_m:null}],
  coverage:{global_ready:false,missing_regions:['남극해 구역']},
  acquisition:{tracks_downloaded:3,raw_points:300,blocks:15,exclusions:{near_coast:40}},
  caveats:['센서 개발·동화 과정의 독립성은 확인되지 않았습니다.'],
  downloads:{report:'/v1/global/validation/altimeter/report.json?run_id=20260911T180000Z',pairs:'/v1/global/validation/altimeter/pairs.csv?run_id=20260911T180000Z'},
});

test('altimeter block counts stay distinct from buoy stations and pooled scores',()=>{
  const html=globalAltimeterMarkup(sample());
  assert.match(html,/위성 고도계 · Sentinel-3A 관측 대조/);
  assert.match(html,/부이 RMSE와 합산하지 않습니다/);
  assert.match(html,/고유 위성 구간 <b>15개/);
  assert.match(html,/3회 위성 통과 · 8예보 사이클/);
  assert.match(html,/0.400 m/);
  assert.match(html,/남극해 구역<\/th><td>—<\/td><td>—<\/td><td>0/);
  assert.match(html,/6–8 m · 8 m 초과 QC 제외<\/th><td>—/);
  assert.match(html,/8 m를 넘는 파랑의 정확도는 채점하지 않았습니다/);
  assert.match(html,/<details class="ga-details">/);
  assert.doesNotMatch(html,/95%|부트스트랩|NaN|undefined/);
});

test('unavailable satellite data has no numeric accuracy cards',()=>{
  const html=globalAltimeterMarkup({state:'unavailable',message:'다운로드 대기'});
  assert.match(html,/위성 관측 대조 결과 미확보/);
  assert.match(html,/다운로드 대기/);
  assert.doesNotMatch(html,/class="metric-tile"|0.000/);
});

test('satellite provenance text is escaped and only its own export routes are linked',()=>{
  const data=sample();data.run_id='<img src=x onerror=alert(1)>';data.caveats=['<script>bad</script>'];
  data.by_region[0].label='<iframe>bad</iframe>';
  data.downloads.report='/v1/global/validation/report.json';data.downloads.pairs='javascript:alert(1)';
  const html=globalAltimeterMarkup(data);
  assert.doesNotMatch(html,/<img|<script|<iframe|javascript:|href="\/v1\/global\/validation\/report/);
  assert.match(html,/&lt;script&gt;/);
  assert.match(globalAltimeterMarkup(sample()),/altimeter\/pairs.csv\?run_id=20260911T180000Z/);
});

test('a pending satellite request does not delay the buoy result and its failure remains separate',async()=>{
  let failSatellite;
  const pending=new Promise((_,reject)=>{failSatellite=reject;});
  const outcomes=[];
  const completed=loadGlobalEvidence({api:path=>path.endsWith('/altimeter')?pending:Promise.resolve({state:'partial',kind:'buoy'}),isCurrent:()=>true,onResult:(kind,data)=>outcomes.push({kind,data})});
  await Promise.resolve();
  assert.equal(outcomes.length,1);
  assert.equal(outcomes[0].kind,'buoy');
  failSatellite(new Error('위성 수신 실패'));
  await completed;
  assert.equal(outcomes.length,2);
  assert.equal(outcomes[1].kind,'altimeter');
  assert.equal(outcomes[1].data.state,'unavailable');
  assert.equal(outcomes[0].data.state,'partial');
});

test('responses from an earlier source selection cannot update the current view',async()=>{
  const updates=[];
  await loadGlobalEvidence({api:async()=>sample(),isCurrent:()=>false,onResult:(...args)=>updates.push(args)});
  assert.equal(updates.length,0);
});

test('satellite map uses real valid block coordinates without drawing invented coverage',()=>{
  assert.equal(altimeterMap([]),'');
  const html=altimeterMap([{block_id:'a',lat:-30,lon:190},{block_id:'a',lat:-30,lon:190},{lat:NaN,lon:20},{lat:91,lon:0},{lat:0,lon:0}]);
  assert.equal((html.match(/<circle /g)||[]).length,2);
  assert.match(html,/cx="60.00" cy="264.00"/);
  assert.match(html,/점 주변을 검증된 해역으로 표시하지 않습니다/);
  assert.doesNotMatch(html,/NaN|Infinity/);
});

test('condition notice computes the worst scored region and highest nonempty wave band',()=>{
  const data=sample();
  data.by_region=[{label:'무표본 해역',n:0,rmse_m:99,n_unique_observations:0},
    {label:'낮은 오차 해역',n:30,rmse_m:.2,n_unique_observations:12},
    {label:'높은 오차 해역',n:10,rmse_m:1.1234,n_unique_observations:7}];
  data.by_hs=[{band:'6_plus',label:'6 m 이상',n:0,rmse_m:88,n_unique_observations:0},
    {band:'4_6',label:'4–6 m',n:8,rmse_m:.8765,n_unique_observations:5},
    {band:'0_2',label:'0–2 m',n:32,rmse_m:1.5,n_unique_observations:15}];
  const notice=globalAltimeterMarkup(data).match(/<div class="ga-condition-summary">(.*?)<\/div>/s)[1];
  assert.match(notice,/높은 오차 해역 1.123 m/);
  assert.match(notice,/고유 구간 7개/);
  assert.match(notice,/4–6 m · RMSE 0.876 m/);
  assert.match(notice,/고유 구간 5개/);
  assert.doesNotMatch(notice,/무표본|6–8 m|0–2 m|통과|합격/);
  data.by_region=[];data.by_hs=[];
  assert.doesNotMatch(globalAltimeterMarkup(data),/ga-condition-summary/);
});
