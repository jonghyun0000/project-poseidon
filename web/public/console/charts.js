import {finite,sampleValue,esc} from './domain.js';
export function chartMarkup(items,varName,leadIndex,threshold,options={}){
 const w=680,h=132,left=38,right=12,top=13,bottom=22,iw=w-left-right,ih=h-top-bottom;
 const values=items.map(i=>sampleValue(i,varName,options));
 if(!values.some(finite))return '<div class="empty">이 변수는 선택한 예보에 유효한 값이 없습니다.</div>';
 const circular=varName==='dirp'||varName==='primary_direction';
 const maxLead=Math.max(...items.map(i=>i.lead_h),1);
 const upper=circular?360:Math.max(1,...values.filter(finite),varName==='hs'?threshold:0)*1.15;
 const x=i=>left+(i.lead_h/maxLead)*iw,y=v=>top+(1-v/upper)*ih;
 let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(varName)} 예측 추세">`;
 for(let k=0;k<4;k++){const v=upper*k/3,yy=y(v);svg+=`<path d="M${left} ${yy}H${w-right}" stroke="#e6edf1" stroke-width="1"/><text x="${left-8}" y="${yy+3}" text-anchor="end" fill="#8ba0ae" font-size="9">${v.toFixed(circular?0:1)}</text>`;}
 for(let lead=0;lead<=maxLead;lead+=Math.max(12,Math.ceil(maxLead/6/12)*12))svg+=`<text x="${left+lead/maxLead*iw}" y="${h-4}" text-anchor="middle" fill="#8ba0ae" font-size="9">+${lead}h</text>`;
 if(varName==='hs'&&finite(threshold))svg+=`<path d="M${left} ${y(threshold)}H${w-right}" stroke="#c39e63" stroke-dasharray="4 4"/><text x="${w-right}" y="${Math.max(9,y(threshold)-4)}" text-anchor="end" fill="#a7834b" font-size="8">사용자 비교선</text>`;
 // 방향은 점으로 표시한다. 359°와 1°를 180°를 지나 연결하지 않는다.
 if(!circular){
  let path='',pen=false;
  items.forEach((it,k)=>{if(!finite(values[k])){pen=false;return;}path+=(pen?'L':'M')+x(it)+' '+y(values[k])+' ';pen=true;});
  svg+=`<path d="${path}" fill="none" stroke="#2c878d" stroke-width="2.3" stroke-linejoin="round"/>`;
 }
 items.forEach((it,k)=>{if(finite(values[k]))svg+=`<circle cx="${x(it)}" cy="${y(values[k])}" r="${k===leadIndex?4:circular?2.8:1.7}" fill="${k===leadIndex?'#123e51':'#2c878d'}"/>`;});
 const selected=items[leadIndex];if(selected)svg+=`<path d="M${x(selected)} ${top}V${h-bottom}" stroke="#698491" stroke-dasharray="3 3"/>`;
 return svg+'</svg>';
}
