import {parseWaypoints} from './voyage-domain.js';

const located=p=>p&&typeof p.lat==='number'&&typeof p.lon==='number'&&Number.isFinite(p.lat)&&Number.isFinite(p.lon)&&Math.abs(p.lat)<=90&&Math.abs(p.lon)<=180;
const same=(a,b)=>located(a)&&located(b)&&Math.abs(a.lat-b.lat)<=1e-7&&Math.abs(a.lon-b.lon)<=1e-7;

export function matchingPortReferences(points, references=[]){
 if(!Array.isArray(references))return [];
 const valid=references.filter(p=>p&&typeof p.id==='string'&&located(p));
 return points.flatMap((point,index)=>{
  const matches=valid.filter(p=>same(p,point));
  const indexed=matches.filter(p=>p.waypoint_index===index);
  const candidates=indexed.length?indexed:matches;
  const unique=[...new Map(candidates.map(p=>[p.id,p])).values()];
  // Repeated calls at the same port are valid; co-located distinct facilities
  // need an explicit waypoint association instead of a coordinate-based guess.
  return unique.length===1?[{...unique[0],waypoint_index:index}]:[];
 });
}

export function attachPortReferences(points,references=[]){
 const valid=matchingPortReferences(points,references);
 return points.map((p,index)=>{
  const ref=valid.find(r=>r.waypoint_index===index);
  return ref?{...p,port_id:ref.id,name:String(ref.name||'').slice(0,80)}:p;
 });
}

export function applyPortToRoute(text,references,port,role){
 if(!['departure','arrival','via'].includes(role))throw new Error('항구의 출발·도착·경유 역할을 확인하세요.');
 if(!located(port)||!port.id)throw new Error('확인된 좌표가 있는 항구만 항로에 넣을 수 있습니다.');
 let points=[];
 if(text.trim()){
  const lines=text.trim().split('\n').filter(l=>l.trim());
  // A single point is a valid unfinished draft, never an analyzable voyage.
  points=lines.length===1?parseWaypoints(lines[0]+'\n'+lines[0]).slice(0,1):parseWaypoints(text);
 }
 const bound=matchingPortReferences(points,references);
 points=points.map((p,index)=>({...p,reference:bound.find(r=>r.waypoint_index===index)}));
 const reference={id:port.id,name:port.name,lat:port.lat,lon:port.lon,unlocode:port.unlocode||null,coordinate_source:port.coordinate_source||null};
 const point={lat:port.lat,lon:port.lon,reference};
 if(!points.length)points.push(point);
 else if(role==='departure')points[0]=point;
 else if(role==='arrival'&&points.length>1)points[points.length-1]=point;
 else if(role==='via'&&points.length>1)points.splice(points.length-1,0,point);
 else points.push(point);
 if(points.length>20)throw new Error('웨이포인트는 최대 20개입니다. 기존 지점을 먼저 정리하세요.');
 if(points.some((p,i)=>i&&same(p,points[i-1])))throw new Error('서로 이웃한 웨이포인트에 같은 항구 좌표를 넣을 수 없습니다.');
 return {coordinates:points.map(p=>`${p.lat}, ${p.lon}`).join('\n'),references:points.flatMap((p,index)=>p.reference?[{...p.reference,waypoint_index:index}]:[])};
}

export function importedPortReferences(request){
 return (request.waypoints||[]).flatMap((p,index)=>p.port_id&&located(p)?[{id:p.port_id,name:p.name||p.port_id,lat:p.lat,lon:p.lon,waypoint_index:index}]:[]);
}
