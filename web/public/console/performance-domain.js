import {finite} from './domain.js';

export const SYNTHETIC_PROFILE={
 name:'가상 연료 곡선 · 기능 시험용',source:'Poseidon synthetic tutorial data; no real vessel evidence',
 source_kind:'synthetic_example',load_condition:'가상 조건 · 실선 미검증',fuel_type:'hfo',assume_zero_current:true,
 rate_scope:'total_at_sea_single_fuel',
 curve:[{speed_stw_kn:8,fuel_t_day:8},{speed_stw_kn:12,fuel_t_day:18},{speed_stw_kn:14,fuel_t_day:28},{speed_stw_kn:16,fuel_t_day:42}]
};
export function profileFromFields(f){
 if(!f.enabled)return null;
 if(!f.zeroCurrent)throw new Error('연료 기준선에는 해류 0 가정이 필요합니다. 실제 해류를 반영한 연료 예측은 아직 지원하지 않습니다.');
 if(!f.name.trim()||f.source.trim().length<5||f.load.trim().length<2)throw new Error('선박/곡선 이름, 자료 출처, 적재 조건을 입력하세요.');
 const lines=f.curve.trim().split('\n').filter(v=>v.trim());
 if(lines.length<2||lines.length>20)throw new Error('연료 곡선은 2–20행을 입력하세요.');
 const curve=lines.map((line,i)=>{const p=line.trim().split(/[\s,]+/);if(p.length!==2||!p.every(finite))throw new Error(`연료 곡선 ${i+1}행: 대수속력 kn, 연료 t/day 형식입니다.`);const [speed_stw_kn,fuel_t_day]=p.map(Number);if(speed_stw_kn<1||speed_stw_kn>40||fuel_t_day<=0||fuel_t_day>1000)throw new Error(`연료 곡선 ${i+1}행의 범위를 확인하세요.`);return {speed_stw_kn,fuel_t_day};});
 if(curve.some((p,i)=>i&&p.speed_stw_kn<=curve[i-1].speed_stw_kn))throw new Error('연료 곡선 속력은 중복 없이 오름차순으로 입력하세요.');
 return {name:f.name.trim(),source:f.source.trim(),source_kind:f.kind,load_condition:f.load.trim(),fuel_type:f.fuel,assume_zero_current:true,rate_scope:'total_at_sea_single_fuel',curve};
}

export function fuelLabel(fuel){
 if(!fuel||fuel.status==='not_configured')return '성능 자료 미입력';
 return {synthetic_example:'가상 예제 · 실선 미검증',baseline_estimate:'제공 곡선 · 정온 기준값',outside_curve:'곡선 속력 범위 밖',invalid_route:'육지 통과 · 연료 산출 제외'}[fuel.status]||'계산 불가';
}
