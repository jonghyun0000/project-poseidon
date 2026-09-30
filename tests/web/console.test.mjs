import test from 'node:test';
import assert from 'node:assert/strict';
import {finite,validPoint,dateLabel,csvData,sampleValue,summarizeForecast} from '../../web/public/console/domain.js';
import {chartMarkup} from '../../web/public/console/charts.js';

test('missing numeric input is never displayed as zero',()=>{
 for(const v of [null,undefined,'', ' ',NaN,Infinity,true]) assert.equal(finite(v),false);
 assert.equal(validPoint('',129),false);
 assert.equal(validPoint(91,129),false);
 assert.equal(validPoint('30','135'),true);
 assert.equal(sampleValue({q50:null},'hs'),null);
 assert.equal(sampleValue({q50:0,applicability:{reasons:['no_wave_energy']}},'hs'),null);
 assert.equal(sampleValue({q50:0.005},'hs'),null);
 assert.equal(sampleValue({q50:0.005},'hs',{source:'global'}),0.005);
});
test('UTC and KST labels have an unambiguous date across midnight',()=>{
 assert.equal(dateLabel('2026-09-06T18:00:00Z'),'2026-09-06 18:00 UTC');
 assert.equal(dateLabel('2026-09-06T18:00:00Z','Asia/Seoul'),'2026-09-07 03:00 KST');
 assert.equal(dateLabel(null),'—');
});
test('a newly produced old forecast is still historical',()=>{
 const m={produced_at:'2026-09-12T00:00:00Z',valid_times:['2026-09-06T18:00:00Z','2026-09-09T18:00:00Z'],freshness:'realtime'};
 assert.equal(summarizeForecast(m,Date.parse('2026-09-12T01:00:00Z')).current,false);
 assert.equal(summarizeForecast(m,Date.parse('2026-09-05T01:00:00Z')).current,false);
 assert.equal(summarizeForecast(m,Date.parse('2026-09-07T01:00:00Z')).current,true);
});
test('CSV retains UTC, nulls, correction state and engine provenance',()=>{
 const text=csvData({lat:30,lon:135},{cycle:'20260906T18',produced_at:'2026-09-07T01:00:00',level:'L1',corrected:false,engine:{advection:'uno2'},items:[{valid_time:'2026-09-07T03:00:00+09:00',lead_h:0,q50:2.6,physics_raw:2.6,values:{tp:null},missing:{tp:'not_stored'}}]},'2026-09-12T00:00:00Z');
 assert.match(text,/"2026-09-06T18:00:00.000Z"/);
 assert.match(text,/"2026-09-07T01:00:00.000Z"/);
 assert.match(text,/"2.6","2.6","","","","false"/);
 assert.match(text,/""advection"":""uno2""/);
 assert.match(text,/not_stored/);
});
test('direction is plotted as dots without a line through the angle wrap',()=>{
 const markup=chartMarkup([{lead_h:0,values:{dirp:359}},{lead_h:3,values:{dirp:1}}],'dirp',0,3);
 assert.equal((markup.match(/<circle /g)||[]).length,2);
 assert.doesNotMatch(markup,/stroke-linejoin/);
 assert.doesNotMatch(markup,/NaN|Infinity/);
});
test('time series gaps do not get bridged by invented values',()=>{
 const markup=chartMarkup([{lead_h:0,q50:2},{lead_h:3,q50:null},{lead_h:6,q50:3}],'hs',2,3);
 const path=markup.match(/<path d="([^"]*)" fill="none" stroke="#2c878d"/)[1];
 assert.equal((path.match(/M/g)||[]).length,2);
 assert.doesNotMatch(path,/L/);
 assert.doesNotMatch(markup,/NaN|Infinity/);
});
test('regional near-zero wave heights are displayed as missing gaps',()=>{
 const markup=chartMarkup([{lead_h:0,q50:0.005},{lead_h:3,q50:0.006}],'hs',0,3);
 assert.match(markup,/유효한 값이 없습니다/);
 const global=chartMarkup([{lead_h:0,q50:0.005},{lead_h:3,q50:0.006}],'hs',0,3,{source:'global'});
 assert.doesNotMatch(global,/유효한 값이 없습니다/);
});
