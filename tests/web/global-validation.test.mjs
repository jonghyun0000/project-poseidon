import test from 'node:test';
import assert from 'node:assert/strict';
import {globalValidationMarkup,stationMap,compactLeads} from '../../web/public/console/global-validation.js';

function report(){
  return {schema_version:'global-validation-1.0',state:'partial',run_id:'pilot-20260912',model:'NOAA GFS-Wave',
    generated_at:'2026-09-11T18:00:00Z',
    protocol:{id:'ndbc-hs-v1',cycle_start:'20260902T00',cycle_end:'20260910T00',leads_h:[6,24,48,72,120],tolerance_minutes:30,cutoff_utc:'2026-09-11T17:00:00Z'},
    overall:{n:500,n_stations:20,n_cycles:9,n_unique_observations:300,n_valid_times:17,rmse_m:.3,mae_m:.2,bias_m:-.1,scatter_index:.12,observed_min_m:.1,observed_max_m:4.5,cycle_bootstrap_95:{rmse_m:[.2,.4],bias_m:[-.15,-.05]}},
    by_lead:[{lead_h:6,n:100,n_stations:20,rmse_m:.25,mae_m:.2,bias_m:-.1}],
    by_region:[{region:'north_pacific',label:'북태평양',n:500,n_stations:20,rmse_m:.3,mae_m:.2,bias_m:-.1}],
    by_hs:[{band:'4-6',label:'4–6 m',n:2,n_stations:1,rmse_m:.4,mae_m:.4,bias_m:.4},{band:'6+',label:'6 m 이상',n:0,n_stations:0,rmse_m:null,mae_m:null,bias_m:null}],
    stations:[{station_id:'NDBC:51001',name:'Hawaii',lat:24,lon:-162,region:'north_pacific',n:25,n_cycles:9,rmse_m:.3,bias_m:-.1}],
    coverage:{valid_start:'2026-09-02T06:00:00Z',valid_end:'2026-09-11T06:00:00Z',missing_regions:['남대서양','인도양'],unscored_leads_h:[120],global_ready:false},
    acquisition:{stations_discovered:40,stations_downloaded:35,stations_with_hs:20,forecast_frames_requested:45,forecast_frames_available:40,exclusions:{no_observation_within_tolerance:10}},
    caveats:['단기간의 일부 해역 표본입니다.'],
    downloads:{report:'/v1/global/validation/report.json',pairs:'/v1/global/validation/pairs.csv'},
  };
}

test('partial validation keeps coverage gaps and shared observations beside error scores',()=>{
  const html=globalValidationMarkup(report());
  assert.match(html,/일부 조건 검증/);
  assert.match(html,/0.300 m/);
  assert.match(html,/고유 관측 <b>300건/);
  assert.match(html,/대조 쌍 수는 독립 관측 수가 아닙니다/);
  assert.match(html,/\[0.200, 0.400\] m/);
  assert.match(html,/개별 지점 예보의 95% 오차 범위가 아닙니다/);
  assert.match(html,/남대서양<\/th><td>—<\/td><td>—<\/td><td>—<\/td><td>0<\/td>/);
  assert.match(html,/\+120 h<\/th><td>—/);
  assert.match(html,/6 m 이상<\/th><td>—/);
  assert.match(html,/시간 허용차 내 관측 없음/);
  assert.match(html,/대조 시각 마감/);
  assert.match(html,/pairs\.csv/);
});

test('unavailable and missing scores do not imply perfect accuracy',()=>{
  const unavailable=globalValidationMarkup({state:'unavailable',message:'자료 확보 중'});
  assert.match(unavailable,/채점 결과 미확보/);
  assert.match(unavailable,/자료 확보 중/);
  assert.doesNotMatch(unavailable,/class="metric-tile"/);
  const data=report();data.overall.rmse_m=null;data.overall.cycle_bootstrap_95=null;
  const html=globalValidationMarkup(data);
  assert.match(html,/Hs RMSE<\/label><strong>— m<\/strong>/);
  assert.match(html,/산출되지 않음/);
  assert.doesNotMatch(html,/NaN|undefined|Infinity/);
});

test('provider text is escaped and download links stay within validation exports',()=>{
  const data=report();data.model='<img src=x onerror=alert(1)>';
  data.stations[0].name='<script>alert(1)</script>';
  data.caveats=['<iframe src=bad>'];data.downloads.report='javascript:alert(1)';data.downloads.pairs='//example.com/pairs.csv';
  const html=globalValidationMarkup(data);
  assert.doesNotMatch(html,/<img|<script|<iframe|javascript:|\/\/example.com/);
  assert.match(html,/&lt;script&gt;/);
});

test('coverage map shows only stations with scored observations and valid coordinates',()=>{
  const html=stationMap([{station_id:'dateline',lat:0,lon:180,n:2},{station_id:'invalid',lat:91,lon:0,n:1},{station_id:'empty',lat:0,lon:0,n:0},{station_id:'west',lat:-30,lon:-120,n:1}]);
  assert.equal((html.match(/<circle /g)||[]).length,2);
  assert.match(html,/cx="40.00" cy="204.00"/);
  assert.match(html,/cx="160.00" cy="264.00"/);
  assert.match(html,/빈 영역은 검증된 영역이 아닙니다/);
  assert.doesNotMatch(html,/NaN|Infinity/);
});

test('all unscored forecast hours stay visible without expanding the scoring table',()=>{
  const data=report();
  data.coverage.unscored_leads_h=Array.from({length:44},(_,i)=>126+i*6);
  const html=globalValidationMarkup(data);
  assert.match(html,/\+126–384 h/);
  assert.doesNotMatch(html,/<th scope="row">\+126 h/);
  assert.equal(compactLeads([24,12,6,24,48,42]),'+6–12 h · +24 h · +42–48 h');
});
