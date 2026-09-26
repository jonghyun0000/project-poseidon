import {esc} from './domain.js';
import {latestTask} from './ports-domain.js';
import {portIssue,routingSearchPath,routingPortLabel,routingOptionsMarkup,routingRequest,validateRoutingPlan,routingResultMarkup} from './routing-domain.js';

export function initRouting({api,onPlan,onClear,getSource=()=>'global'}){
 const $=id=>document.getElementById(id),roles=['departure','arrival'];
 const S={ports:{departure:null,arrival:null},plan:null,busy:false,revision:0};
 const searches=Object.fromEntries(roles.map(role=>[role,{gate:latestTask(),items:[],active:-1,timer:null}]));
 const input=role=>$(`routing-${role}`),list=role=>$(`routing-${role}-results`);
 const avoidance=()=>[...$('routing-avoid').querySelectorAll('input:checked')].map(element=>element.value);
 function closeSearch(role){const search=searches[role];clearTimeout(search.timer);search.gate.invalidate();list(role).hidden=true;input(role).setAttribute('aria-expanded','false');input(role).removeAttribute('aria-activedescendant');}
 function controls(){
  const global=getSource()==='global';
  $('routing-regional-note').hidden=global;
  $('routing-calculate').disabled=S.busy||!global||!!portIssue(S.ports.departure)||!!portIssue(S.ports.arrival)||S.ports.departure?.id===S.ports.arrival?.id;
  $('routing-calculate').textContent=S.busy?'항로망 계산 중…':'해상 항로 계산';
  $('routing-clear').hidden=!S.plan&&!S.busy;
  $('routing-section').setAttribute('aria-busy',String(S.busy));
 }
 function invalidate(message='',{notify=true}={}){
  const hadPlan=!!S.plan||S.busy;
  S.revision++;S.plan=null;S.busy=false;
  $('routing-result').replaceChildren();$('routing-result').hidden=true;
  $('routing-error').textContent='';$('routing-status').textContent=message;$('routing-retry').hidden=true;
  if(hadPlan&&notify)onClear?.();
  controls();
 }
 function renderSelection(role){
  const port=S.ports[role],host=$(`routing-${role}-selected`),issue=portIssue(port);
  host.textContent=port?(issue||`${port.name} · 항구 대표 좌표 선택됨`):'검색 결과의 항구를 선택하세요.';
  host.classList.toggle('inline-error',!!port&&!!issue);
  input(role).setAttribute('aria-invalid',String(!!port&&!!issue));
 }
 function setPort(port,role){
  if(!roles.includes(role))return;
  closeSearch(role);S.ports[role]=port?{...port}:null;input(role).value=routingPortLabel(port);renderSelection(role);
  invalidate('항구를 선택했습니다. 출발항·도착항을 확인하고 해상 항로를 계산하세요.');
 }
 function renderOptions(role){
  const search=searches[role];list(role).innerHTML=routingOptionsMarkup(search.items,role,search.active);
  list(role).querySelectorAll('[data-routing-option]').forEach(button=>button.onclick=()=>choose(role,+button.dataset.routingOption));
  if(search.active>=0)input(role).setAttribute('aria-activedescendant',`routing-${role}-option-${search.active}`);
  else input(role).removeAttribute('aria-activedescendant');
 }
 function choose(role,index){
  const port=searches[role].items[index];if(!port)return;
  const issue=portIssue(port);
  if(issue){$(`routing-${role}-selected`).textContent=issue;$(`routing-${role}-selected`).classList.add('inline-error');return;}
  setPort(port,role);input(role).focus();
 }
 function searchPorts(role){
  const search=searches[role],query=input(role).value.trim();
  if(!query){closeSearch(role);return;}
  search.items=[];search.active=-1;list(role).hidden=false;input(role).setAttribute('aria-expanded','true');
  list(role).innerHTML='<div class="routing-search-empty" role="status">항구 검색 중…</div>';
  return search.gate.run(()=>api(routingSearchPath(query)),data=>{
   search.items=Array.isArray(data.items)?data.items:[];search.active=-1;renderOptions(role);
  },error=>{
   list(role).innerHTML=`<div class="routing-search-empty inline-error" role="alert">${esc(error.message)}</div><button type="button" data-routing-search-retry>항구 검색 다시 시도</button>`;
   list(role).querySelector('[data-routing-search-retry]').onclick=()=>searchPorts(role);
  });
 }
 async function calculate(){
  if(S.busy)return;
  let request;
  try{request=routingRequest(S.ports.departure,S.ports.arrival,avoidance(),getSource());}
  catch(error){$('routing-error').textContent=error.message;return;}
  roles.forEach(closeSearch);invalidate();S.busy=true;const revision=++S.revision;controls();
  $('routing-status').textContent='선택한 항구에 연결되는 해상 항로망을 확인하고 있습니다.';
  try{
   const result=await api('/v1/routing/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
   if(revision!==S.revision)return;
   const plan=validateRoutingPlan(result,request);
   onPlan(plan);if(revision!==S.revision)return;S.plan=plan;S.busy=false;
   $('routing-result').innerHTML=routingResultMarkup(plan);$('routing-result').hidden=false;
   $('routing-status').textContent='해상 항로 후보를 불러왔습니다. 외해 구간 시작 시각·속력을 입력하고 항로 분석을 실행하세요.';
  }catch(error){
   if(revision!==S.revision)return;
   S.busy=false;S.plan=null;$('routing-error').textContent=error.message;$('routing-retry').hidden=false;
   $('routing-status').textContent='항로를 계산하지 못했습니다. 항구 선택과 회피 조건을 확인하세요.';
  }finally{if(revision===S.revision)controls();}
 }
 for(const role of roles){
  input(role).addEventListener('input',()=>{
   closeSearch(role);S.ports[role]=null;renderSelection(role);invalidate('항구 선택이 변경되었습니다. 검색 결과에서 다시 선택하세요.');
   if(input(role).value.trim())searches[role].timer=setTimeout(()=>searchPorts(role),250);
  });
  input(role).addEventListener('keydown',event=>{
   const search=searches[role];
   if(event.key==='Escape'){event.preventDefault();closeSearch(role);return;}
   if(event.key==='Enter'){
    event.preventDefault();event.stopPropagation();
    if(!list(role).hidden&&search.active>=0)choose(role,search.active);
    else if(!S.ports[role]){clearTimeout(search.timer);searchPorts(role);}
    return;
   }
   if(['ArrowDown','ArrowUp'].includes(event.key)){
    event.preventDefault();if(list(role).hidden){searchPorts(role);return;}
    if(!search.items.length)return;
    search.active=search.active<0?(event.key==='ArrowDown'?0:search.items.length-1):(search.active+(event.key==='ArrowDown'?1:-1)+search.items.length)%search.items.length;
    renderOptions(role);$(`routing-${role}-option-${search.active}`)?.scrollIntoView({block:'nearest'});
   }
  });
  $(`routing-${role}-picker`).addEventListener('focusout',event=>{if(!event.currentTarget.contains(event.relatedTarget))closeSearch(role);});
  renderSelection(role);
 }
 $('routing-avoid').addEventListener('change',()=>invalidate('회피 조건이 변경되었습니다. 해상 항로를 다시 계산하세요.'));
 $('routing-calculate').onclick=calculate;$('routing-retry').onclick=calculate;
 function clear(){invalidate('자동 항로를 해제했습니다. 수동 웨이포인트를 사용하거나 다시 계산하세요.');}
 function restore(plan){
  validateRoutingPlan(plan,plan?.request);
  roles.forEach(closeSearch);invalidate('',{notify:false});
  for(const role of roles){S.ports[role]={...plan[role].port};input(role).value=routingPortLabel(S.ports[role]);renderSelection(role);}
  const avoid=new Set(plan.request.avoid_passages);
  $('routing-avoid').querySelectorAll('input').forEach(element=>element.checked=avoid.has(element.value));
  S.plan=plan;S.busy=false;$('routing-result').innerHTML=routingResultMarkup(plan);$('routing-result').hidden=false;
  $('routing-status').textContent='해상 항로 후보를 적용했습니다. 외해 구간 시작 시각·속력을 확인하고 분석하세요.';
  controls();
 }
 $('routing-clear').onclick=clear;
 controls();
 return {setPort,clear,restore,sourceChanged({notify=true}={}){roles.forEach(closeSearch);invalidate(getSource()==='global'?'전 세계 예보 범위입니다. 해상 항로를 다시 계산하세요.':'자동 항로 계산은 전 세계 예보 범위에서 사용할 수 있습니다.',{notify});}};
}
