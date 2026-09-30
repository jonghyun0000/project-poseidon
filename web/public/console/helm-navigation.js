// Map-scale screening only. This is not a nautical chart or a grounding model.
import {hullRing,wrap} from './helm-domain.js';

const norm=(point,center)=>[center[0]+wrap(point[0]-center[0]),point[1]];
const sign=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
const between=(v,a,b)=>v>=Math.min(a,b)-1e-10&&v<=Math.max(a,b)+1e-10;
function onSegment(p,a,b){return Math.abs(sign(a,b,p))<1e-10&&between(p[0],a[0],b[0])&&between(p[1],a[1],b[1]);}
function crossing(a,b,c,d){
 if(onSegment(a,c,d)||onSegment(b,c,d)||onSegment(c,a,b)||onSegment(d,a,b))return true;
 return (sign(a,b,c)>0)!==(sign(a,b,d)>0)&&(sign(c,d,a)>0)!==(sign(c,d,b)>0);
}
function insideRing(p,ring){
 let inside=false;
 for(let i=0,j=ring.length-1;i<ring.length;j=i++){
  const a=ring[j],b=ring[i];if(onSegment(p,a,b))return true;
  if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])inside=!inside;
 }
 return inside;
}
function inPolygon(p,rings){return insideRing(p,rings[0])&&!rings.slice(1).some(r=>insideRing(p,r));}
function edges(line){const out=[];for(let i=1;i<line.length;i++)out.push([line[i-1],line[i]]);return out;}
function ringHit(a,b){return edges(a).some(([p,q])=>edges(b).some(([r,s])=>crossing(p,q,r,s)));}
function polygonHit(hull,rings){
 if(rings.some(r=>ringHit(hull,r)))return true;
 if(hull.some(p=>inPolygon(p,rings)))return true;
 return inPolygon(rings[0][0],[hull]);
}
function pathHit(path,rings){
 if(rings.some(r=>edges(r).some(([a,b])=>crossing(path[0],path[1],a,b))))return true;
 return inPolygon(path[0],rings)||inPolygon(path[1],rings);
}
function extent(points){return [Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];}
const overlaps=(a,b)=>a[0]<=b[2]&&b[0]<=a[2]&&a[1]<=b[3]&&b[1]<=a[3];
function unwrapGeometry(geometry,center,out){
 if(!geometry)return;
 const type=geometry.type,c=geometry.coordinates;
 if(type==='Polygon'){
  const rings=c.map(r=>r.map(p=>norm(p,center)));
  if(rings[0]?.length)out.push({kind:'polygon',rings,bbox:extent(rings[0])});
 }
 else if(type==='MultiPolygon')c.forEach(p=>unwrapGeometry({type:'Polygon',coordinates:p},center,out));
 else if(type==='LineString'){
  const points=c.map(p=>norm(p,center));if(points.length>1)out.push({kind:'line',points,bbox:extent(points)});
 }
 else if(type==='MultiLineString')c.forEach(p=>unwrapGeometry({type:'LineString',coordinates:p},center,out));
 else if(type==='GeometryCollection')geometry.geometries.forEach(g=>unwrapGeometry(g,center,out));
}
export function coastWindow(raw){
 if(raw?.status!=='ready'||!Array.isArray(raw.center)||raw.radius_degrees!==0.03||!Array.isArray(raw.geometries))throw Error('해안선 자료 형식 오류');
 const obstacles=[];for(const g of raw.geometries)unwrapGeometry(g,raw.center,obstacles);
 return {center:raw.center,radius:raw.radius_degrees,obstacles,source:raw.source};
}
export function harborObstacles(geo,center){
 const out=[];
 for(const f of geo?.features||[]){if(f.properties?.kind==='dock')continue;unwrapGeometry(f.geometry,center,out);}
 return out;
}
function hull(s,ship,center){return hullRing(s,ship).map(p=>norm(p,center));}
export function coastCovers(s,ship,window){
 if(!window)return false;
 return hull(s,ship,window.center).every(p=>Math.abs(p[0]-window.center[0])<window.radius/2&&Math.abs(p[1]-window.center[1])<window.radius/2);
}
export function collision(previous,next,ship,window,harbor=[]){
 if(!window||!coastCovers(previous,ship,window)||!coastCovers(next,ship,window))return 'unknown';
 const before=hull(previous,ship,window.center),after=hull(next,ship,window.center);
 const paths=before.map((p,i)=>[p,after[i]]);
 const swept=extent([...before,...after]);
 for(const [source,obstacles] of [['land',window.obstacles],['facility',harbor]])for(const o of obstacles){
  if(!overlaps(swept,o.bbox))continue;
  if(o.kind==='polygon'&&(polygonHit(before,o.rings)||polygonHit(after,o.rings)||paths.some(p=>pathHit(p,o.rings))))return source;
  if(o.kind==='line'&&(edges(o.points).some(([a,b])=>edges(before).some(([c,d])=>crossing(a,b,c,d))||edges(after).some(([c,d])=>crossing(a,b,c,d))||paths.some(([c,d])=>crossing(a,b,c,d)))))return source;
 }
 return null;
}
