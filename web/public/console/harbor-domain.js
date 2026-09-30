// OSM geometry stays at its source coordinates. Open ways are never closed.
export function harborMapURL(lat,lon){
 if(![lat,lon].every(Number.isFinite)||Math.abs(lat)>80||Math.abs(lon)>179.99)throw Error('이 좌표의 상세 형상 조회는 지원되지 않습니다.');
 const bbox=[lon-.005,lat-.005,lon+.005,lat+.005].map(n=>n.toFixed(6)).join(',');
 return 'https://api.openstreetmap.org/api/0.6/map.json?'+new URLSearchParams({bbox});
}
export function harborFeatures(raw){
 if(!Array.isArray(raw?.elements)||raw.remark)throw Error(raw?.remark||'항만 형상 자료가 올바르지 않습니다.');
 const features=[];
 const nodes=new Map(raw.elements.filter(e=>e.type==='node').map(e=>[e.id,e]));
 for(const w of raw.elements){
  if(w.type!=='way')continue;
  const tags=w.tags||{};
  if(!['pier','breakwater','quay'].includes(tags.man_made)&&!tags.building&&tags.waterway!=='dock')continue;
  const geometry=w.geometry||w.nodes?.map(id=>nodes.get(id));
  if(!Array.isArray(geometry)||geometry.length<2||geometry.some(p=>!p))continue;
  const coords=geometry.map(p=>[p.lon,p.lat]);
  if(coords.some(p=>!p.every(Number.isFinite)||Math.abs(p[0])>180||Math.abs(p[1])>90))continue;
  const closed=coords.length>=4&&coords[0][0]===coords.at(-1)[0]&&coords[0][1]===coords.at(-1)[1];
  const kind=w.tags?.man_made||(w.tags?.building?'building':w.tags?.waterway)||'facility';
  features.push({type:'Feature',properties:{osm_id:w.id,name:w.tags?.name||'',kind,
   source:`https://www.openstreetmap.org/way/${w.id}`,color:kind==='dock'?'#6baebc':kind==='building'?'#a3947f':'#ddae6a'},
   geometry:{type:closed?'Polygon':'LineString',coordinates:closed?[coords]:coords}});
 }
 return {type:'FeatureCollection',features,source:'OpenStreetMap contributors · ODbL 1.0',osm_base:raw.osm3s?.timestamp_osm_base||raw.snapshot?.retrieved_at||null};
}
