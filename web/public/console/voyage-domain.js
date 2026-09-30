import {finite,sampleValue} from './domain.js';
import {unwrapRoute} from './global-domain.js';

export function voyageTrackFeatures(points,threshold,options={}){
 if(!Array.isArray(points)||points.length<2)return [];
 const track=unwrapRoute(points),features=[];
 for(let i=1;i<track.length;i++){
  const a=track[i-1],b=track[i],from=sampleValue({q50:a.values?.hs,status:a.status,applicability:a.applicability},'hs',options),to=sampleValue({q50:b.values?.hs,status:b.status,applicability:b.applicability},'hs',options);
  const missing=!Number.isFinite(from)||!Number.isFinite(to);
  const color=missing?'#8795a3':Math.max(from,to)>threshold?'#b97247':'#207f87';
  const previous=features.at(-1),end=[b.plot_lon,b.lat];
  if(previous?.properties.color===color)previous.geometry.coordinates.push(end);
  else features.push({type:'Feature',properties:{color},geometry:{type:'LineString',coordinates:[[a.plot_lon,a.lat],end]}});
 }
 return features;
}

export const EXAMPLE = '34.5, 129.0\n33.5, 128.0\n31.0, 127.0\n28.0, 126.0';
export function parseWaypoints(text){
 const lines=text.trim().split(/\n/).filter(v=>v.trim());
 if(lines.length<2||lines.length>20)throw new Error('웨이포인트는 2–20개를 입력하세요.');
 return lines.map((line,i)=>{
  const parts=line.trim().split(/[\s,]+/);
  if(parts.length!==2||!parts.every(finite))throw new Error(`${i+1}행: 위도, 경도 형식으로 입력하세요.`);
  const [lat,lon]=parts.map(Number);
  if(Math.abs(lat)>90||Math.abs(lon)>180)throw new Error(`${i+1}행의 좌표 범위를 확인하세요.`);
  return {lat,lon};
 });
}
export function validateImportedWaypoints(request,plan=null){
 const points=request.waypoints;
 if(!Array.isArray(points)||points.some(p=>!p||typeof p.lat!=='number'||typeof p.lon!=='number'||!Number.isFinite(p.lat)||!Number.isFinite(p.lon)))throw new Error('저장 좌표가 올바르지 않습니다.');
 const coordinates=points.map(p=>`${p.lat}, ${p.lon}`).join('\n');
 if(plan){
  if(request.source!=='global'||request.route_plan_id!==plan.plan_id||points.length!==plan.waypoints.length||points.some((p,i)=>p.port_id!=null||Math.abs(p.lat-plan.waypoints[i].lat)>1e-8||Math.abs(p.lon-plan.waypoints[i].lon)>1e-8))throw new Error('저장 파일의 좌표가 원본 항로 계획과 다릅니다.');
 }else{if(request.route_plan_id)throw new Error('원본 항로 계획을 먼저 확인해야 합니다.');parseWaypoints(coordinates);}
 return coordinates;
}
export function requestFromForm({name,coordinates,departure,speed,threshold,compare,cycle,deadline,performanceProfile,source}){
 if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(departure)||!Number.isFinite(Date.parse(departure+'Z')))throw new Error('UTC 출항 시각을 입력하세요.');
 if(!finite(speed)||+speed<1||+speed>40)throw new Error('대지속력은 1–40 kn을 입력하세요.');
 if(!finite(threshold)||+threshold<=0||+threshold>30)throw new Error('파고 비교선은 0 초과, 30 m 이하입니다.');
 if(deadline&&(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(deadline)||!Number.isFinite(Date.parse(deadline+'Z'))))throw new Error('UTC 도착 마감시각을 확인하세요.');
 const scenarios=[{name:'기준',speed_kn:+speed,departure_offset_h:0}];
 if(compare){scenarios.push({name:'6시간 후 출항',speed_kn:+speed,departure_offset_h:6});if(+speed>2)scenarios.push({name:'감속 운항',speed_kn:Math.max(1,+speed-2),departure_offset_h:0});}
 return {source:source||'regional',name:name.trim()||'항로 분석',cycle,departure_utc:departure+'Z',waypoints:parseWaypoints(coordinates),scenarios,hs_threshold_m:+threshold,arrival_deadline_utc:deadline?deadline+'Z':null,performance_profile:performanceProfile||null};
}
export function voyageCSV(report){
 const cell=v=>{let s=String(v??'');if(typeof v==='string'&&/^[=+\-@]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
 const rows=[['analysis_id','created_at_utc','cycle','produced_at_utc','scenario','scenario_status','speed_sog_kn','departure_utc','arrival_utc','hs_threshold_m','elapsed_h','valid_time_utc','lat','lon','distance_nm','course_true_deg','hs_physics_m','tp_s','tm02_s','dirm_from_deg','dirp_from_deg','relative_wave_deg','status','hs_applicability','hs_reasons','missing_variables','peak_valid_time_utc','screening','corrected','wind_status','wind_10m_ms','wind_from_deg','apparent_wind_ms','wind_cycle','fuel_status','fuel_profile','fuel_source_kind','fuel_source','fuel_load_condition','fuel_t_total','combustion_co2_t_total','co2_factor','fuel_scope','arrival_status','deadline_utc','arrival_margin_h','source','provider','primary_period_s','primary_direction_deg','primary_valid_time_utc','surface_wind_ms','wind_reference_level','waypoint_port_ids','port_catalog_sha256','route_plan_id','route_scope','route_network_sha256','route_screening_policy','departure_reference_gap_nm','arrival_reference_gap_nm']];
 for(const s of report.scenarios)for(const p of s.points)rows.push([report.analysis_id,report.created_at_utc,report.cycle,report.produced_at,s.name,s.status,s.speed_kn,s.departure_utc,s.arrival_utc,report.request.hs_threshold_m,p.elapsed_h,p.valid_time,p.lat,p.lon,p.distance_nm,p.course_deg,p.values.hs,p.values.tp,p.values.tm02,p.values.dirm,p.values.dirp,p.relative_wave_deg,p.status,p.applicability?.verdict,p.applicability?.reasons?.join('|'),JSON.stringify(p.missing||{}),p.peak_valid_time,report.screening.status,report.corrected,p.wind?.status,report.source==='global'?null:p.wind?.speed_ms,p.wind?.from_deg,p.wind?.apparent_speed_ms,report.environment?.wind?.cycle,s.fuel?.status,s.fuel?.profile_name,s.fuel?.source_kind,s.fuel?.source,s.fuel?.load_condition,s.fuel?.fuel_t,s.fuel?.co2_t,s.fuel?.co2_factor,s.fuel?.scope,s.arrival_constraint?.status,s.arrival_constraint?.deadline_utc,s.arrival_constraint?.margin_h,report.source||'regional',report.engine?.provider||'Poseidon regional',p.values.primary_period,p.values.primary_direction,p.primary_valid_time,report.source==='global'?p.wind?.speed_ms:null,p.wind?.reference_level||'10 m above ground',report.port_references?.map(p=>`${p.waypoint_index}:${p.port_id}`).join('|'),[...new Set((report.port_references||[]).map(p=>p.catalog_sha256).filter(Boolean))].join('|'),report.route_plan?.plan_id,report.scope,report.route_plan?.network?.graph_sha256,report.route_plan?.screening?.policy,report.route_plan?.departure?.gap_nm,report.route_plan?.arrival?.gap_nm]);
 return '\ufeff'+rows.map(r=>r.map(cell).join(',')).join('\r\n');
}
