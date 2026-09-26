import {SYNTHETIC_PROFILE,profileFromFields} from './performance-domain.js';

export function initPerformanceForm(changed){
 const $=id=>document.getElementById(id);
 function raw(){return {enabled:$('perf-enabled').checked,name:$('perf-name').value,source:$('perf-source').value,kind:$('perf-kind').value,load:$('perf-load').value,fuel:$('perf-fuel').value,zeroCurrent:$('perf-zero').checked,curve:$('perf-curve').value};}
 function visibility(){$('perf-fields').hidden=!$('perf-enabled').checked;$('perf-example-note').hidden=$('perf-kind').value!=='synthetic_example'||!$('perf-enabled').checked;}
 function restore(f){if(!f)return;$('perf-enabled').checked=!!f.enabled;$('perf-name').value=f.name||'';$('perf-source').value=f.source||'';$('perf-kind').value=f.kind||'user_assumption';$('perf-load').value=f.load||'';$('perf-fuel').value=f.fuel||'hfo';$('perf-zero').checked=!!f.zeroCurrent;$('perf-curve').value=f.curve||'';visibility();}
 function loadProfile(p){if(!p){restore({enabled:false});return;}restore({enabled:true,name:p.name,source:p.source,kind:p.source_kind,load:p.load_condition,fuel:p.fuel_type,zeroCurrent:p.assume_zero_current,curve:p.curve.map(r=>`${r.speed_stw_kn}, ${r.fuel_t_day}`).join('\n')});}
 $('perf-enabled').addEventListener('change',visibility);$('perf-kind').addEventListener('change',visibility);
 $('perf-example').onclick=()=>{loadProfile(SYNTHETIC_PROFILE);changed();};
 return {raw,restore,loadProfile,profile:()=>profileFromFields(raw())};
}
