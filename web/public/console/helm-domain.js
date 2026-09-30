// This is a kinematic interaction demo, not a validated ship manoeuvring solver.
// Exponential easing is a UI response law; its constants are not measured trials.
export const HELM_VERSION='kinematic-demo-1';
export const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
export const wrap=v=>((v+180)%360+360)%360-180;
export const heading=v=>((v%360)+360)%360;
const rad=Math.PI/180;
export function destination(lon,lat,bearing,meters){
 const d=meters/6371008.8,p=lat*rad,l=lon*rad,b=bearing*rad;
 const p2=Math.asin(clamp(Math.sin(p)*Math.cos(d)+Math.cos(p)*Math.sin(d)*Math.cos(b),-1,1));
 return [wrap((l+Math.atan2(Math.sin(b)*Math.sin(d)*Math.cos(p),Math.cos(d)-Math.sin(p)*Math.sin(p2)))/rad),p2/rad];
}
export function initialState(lon=129.135,lat=35.065,hdg=90){
 if(![lon,lat,hdg].every(Number.isFinite)||Math.abs(lat)>80||Math.abs(lon)>180)throw Error('시작 좌표 범위: 위도 ±80°, 경도 ±180°');
 return {lon,lat,heading:heading(hdg),speed:0,rudder:0,yaw:0,elapsed:0,distance:0};
}
export function step(state,command,ship,seconds){
 if(!Number.isFinite(seconds)||seconds<0||seconds>60)throw Error('잘못된 시간 간격');
 if(![command.throttle,command.rudder].every(Number.isFinite))throw Error('잘못된 조종 입력');
 let s={...state},remaining=seconds;
 // Fixed bounded substeps keep large time multipliers from skipping response.
 while(remaining>1e-8){
  const dt=Math.min(.1,remaining);remaining-=dt;
  const target=clamp(command.throttle,-50,100)/100*ship.handling.maxKn;
  const speed=s.speed+(target-s.speed)*(1-Math.exp(-dt/ship.handling.accelSeconds));
  const rudder=s.rudder+(clamp(command.rudder,-35,35)-s.rudder)*(1-Math.exp(-dt/2));
  const targetYaw=(rudder/35)*(speed/ship.handling.maxKn)*Math.min(3,150/ship.length);
  const yaw=s.yaw+(targetYaw-s.yaw)*(1-Math.exp(-dt/ship.handling.turnSeconds));
  const midHeading=heading(s.heading+yaw*dt/2),distance=(s.speed+speed)/2*1852/3600*dt;
  const [lon,lat]=destination(s.lon,s.lat,midHeading,distance);
  s={lon,lat,heading:heading(s.heading+yaw*dt),speed,rudder,yaw,elapsed:s.elapsed+dt,distance:s.distance+Math.abs(distance)};
 }
 return s;
}
const HULL_PROFILE=[[0,.5],[.36,.36],[.5,.16],[.5,-.44],[.34,-.5],[-.34,-.5],[-.5,-.44],[-.5,.16],[-.36,.36]];
export function hullRing(s,ship){
 const points=HULL_PROFILE.map(([x,y])=>{
  const [lon,lat]=destination(s.lon,s.lat,s.heading+Math.atan2(x*ship.beam,y*ship.length)/rad,Math.hypot(x*ship.beam,y*ship.length));
  return [s.lon+wrap(lon-s.lon),lat];
 });
 points.push(points[0]);return points;
}
export function hullFeatures(s,ship){
 const local=(x,y)=>{
  const [lon,lat]=destination(s.lon,s.lat,s.heading+Math.atan2(x,y)/rad,Math.hypot(x,y));
  return [s.lon+wrap(lon-s.lon),lat];
 };
 const features=[];
 function polygon(points,color,height,kind){
  const c=points.map(([x,y])=>local(x*ship.beam,y*ship.length));c.push(c[0]);
  features.push({type:'Feature',properties:{color,height,kind},geometry:{type:'Polygon',coordinates:[c]}});
 }
 const rect=(x,y,w,h,color,z,kind)=>polygon([[x-w/2,y-h/2],[x+w/2,y-h/2],[x+w/2,y+h/2],[x-w/2,y+h/2]],color,z,kind);
 features.push({type:'Feature',properties:{color:ship.color,height:3,kind:'hull'},geometry:{type:'Polygon',coordinates:[hullRing(s,ship)]}});
 const style=ship.style;
 if(style==='container')for(let row=0;row<7;row++)for(let col=0;col<4;col++)rect((col-1.5)*.21,-.22+row*.082,.18,.068,['#b68561','#759986','#7599b2'][(row+col)%3],9,'deck');
 else if(style==='lng')for(let i=0;i<4;i++)polygon(Array.from({length:20},(_,j)=>[.39*Math.cos(j*Math.PI/10),-.24+i*.16+.063*Math.sin(j*Math.PI/10)]),'#cbdbe0',12,'tank');
 else if(style==='tanker'){rect(0,.02,.07,.7,'#d5bb96',5,'pipe');for(let i=0;i<6;i++)rect(0,-.25+i*.105,.7,.014,'#e1cfb3',5,'deck');}
 else if(['roro','liner','ferry'].includes(style)){rect(0,-.02,.83,.72,'#dce8ec',13,'deck');rect(0,.01,.58,.56,'#ffffff',18,'deck');}
 else if(style==='dredger'){rect(0,0,.65,.5,'#274c55',4,'well');rect(0,.4,.16,.4,'#d1b872',6,'ladder');}
 else if(style==='catamaran'){rect(-.37,0,.21,.8,'#e2e8ed',4,'hull');rect(.37,0,.21,.8,'#e2e8ed',4,'hull');}
 else rect(0,-.17,.64,.28,'#c9d6dc',5,'working-deck');
 rect(0,style==='container'||style==='lng'||style==='tanker'?-.36:.12,.68,.12,'#f1f4ed',ship.length>150?20:8,'bridge');
 rect(0,style==='container'||style==='lng'||style==='tanker'?-.34:.14,.6,.035,'#264d62',ship.length>150?21:9,'windows');
 return {type:'FeatureCollection',features};
}
export function trailGeo(samples){
 const lines=[];let line=[];
 for(const s of samples){const p=[s.lon,s.lat];if(line.length&&Math.abs(p[0]-line.at(-1)[0])>180){if(line.length>1)lines.push(line);line=[];}line.push(p);}
 if(line.length>1)lines.push(line);
 return {type:'FeatureCollection',features:lines.map(coordinates=>({type:'Feature',properties:{},geometry:{type:'LineString',coordinates}}))};
}
export function sessionDocument(ship,origin,samples,port){
 return {schema_version:HELM_VERSION,exported_at:new Date().toISOString(),reference_ship:ship,origin,port_reference:port||null,
  limitations:['조종 계수는 실선 시운전 미보정','실제 AIS 위치가 아닌 사용자 조종 항적','Natural Earth 1:10m 육지와 불러온 OSM 시설 교차 시 화면상 정지','작은 섬·해도·수심·다른 선박 충돌 미검증','해류·파랑 힘 미계산','항구 시설은 OSM 수록 범위이며 공식 해도 아님','최근 최대 12,000개 기록과 내보내기 시점만 보존'],
  sample_window:{start_elapsed:samples[0]?.elapsed??null,end_elapsed:samples.at(-1)?.elapsed??null,truncated:(samples[0]?.elapsed||0)>0},
  units:{speed:'kn',rudder:'degree',heading:'degree true clockwise',distance:'m',elapsed:'s'},samples};
}
