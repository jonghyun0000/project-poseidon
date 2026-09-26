export const finite = v => (typeof v === 'number' || typeof v === 'string' && v.trim() !== '') && Number.isFinite(Number(v));
export const fmt = (v,n=2) => finite(v) ? Number(v).toFixed(n) : '—';
export const esc = v => String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function validPoint(lat,lon){return finite(lat)&&finite(lon)&&Math.abs(+lat)<=90&&+lon>=-180&&+lon<=360;}
export function utcDate(v){if(!v)return null;const s=String(v);const d=new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(s)?s:s+'Z');return Number.isNaN(+d)?null:d;}
export function dateLabel(v,tz='UTC'){
 const d=utcDate(v);if(!d)return '—';
 const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d).map(v=>[v.type,v.value]));
 return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} ${tz==='UTC'?'UTC':'KST'}`;
}
export function csvData(point,data,exportedAt){
 const header=['exported_at_utc','cycle_utc','produced_at_utc','lat','lon','level','valid_time_utc','lead_h','hs_m','hs_physics_raw_m','tp_s','tm02_s','dirp_from_deg','corrected','correction_model_version','hs_applicability','reasons','missing_variables','engine_metadata','source','provider','retrieved_at_utc','primary_period_s','primary_direction_deg','surface_wind_ms'];
 const quote=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
 const iso=v=>utcDate(v)?.toISOString()??'';
 const rows=(data.items||[]).map(i=>[iso(exportedAt),data.cycle,iso(data.produced_at),point.lat,point.lon,data.level,iso(i.valid_time),i.lead_h,sampleValue(i,'hs'),i.physics_raw,sampleValue(i,'tp'),sampleValue(i,'tm02'),sampleValue(i,'dirp'),data.corrected,data.model_version,i.applicability?.verdict,(i.applicability?.reasons||[]).join('|'),JSON.stringify(i.missing||{}),JSON.stringify(data.engine||{}),data.source||'regional',data.provider||'Poseidon regional',iso(data.retrieved_at),sampleValue(i,'primary_period'),sampleValue(i,'primary_direction'),i.wind?.speed_ms]);
 return '\ufeff'+[header,...rows].map(row=>row.map(quote).join(',')).join('\r\n');
}
export function sampleValue(item,varName){if(!item)return null;if(item.applicability?.reasons?.includes('no_wave_energy'))return null;const v=varName==='hs'?item.q50:item.values?.[varName];return finite(v)?Number(v):null;}
export function summarizeForecast(meta,now=Date.now()){
 if(!meta?.valid_times?.length)return {label:'예보 없음',current:false};
 const first=utcDate(meta.valid_times[0]),last=utcDate(meta.valid_times.at(-1));
 if(!first||!last)return {label:'예보 시각 확인 불가',current:false};
 if(+last<now)return {label:'과거 예보 · 현재 시각 미포함',current:false};
 if(+first>now)return {label:'미래 예보 · 아직 유효하지 않음',current:false};
 return {label:meta.freshness==='realtime'?'최신 예보':'지연 예보 · 갱신 확인 필요',current:true};
}
