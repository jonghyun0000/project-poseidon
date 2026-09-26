export function normalizeLongitude(lon){return ((Number(lon)+180)%360+360)%360-180;}
export function unwrapRoute(points){
 let previous=null;
 return points.map(p=>{let plot_lon=normalizeLongitude(p.lon);if(previous!==null){while(plot_lon-previous>180)plot_lon-=360;while(plot_lon-previous< -180)plot_lon+=360;}previous=plot_lon;return {...p,plot_lon};});
}
export function coordinateLabel(lat,lon,digits=3){const n=normalizeLongitude(lon);return `${Math.abs(lat).toFixed(digits)}°${lat<0?'S':'N'} ${Math.abs(n).toFixed(digits)}°${n<0?'W':'E'}`;}
export const GLOBAL_ROUTES={
 pacific:{name:'북태평양 해상 구간 · 날짜변경선 횡단',coordinates:'35, 142\n42, 170\n42, -160\n37, -124',speed:18},
 atlantic:{name:'북대서양 해상 구간',coordinates:'40, -72\n41, -60\n46, -40\n48, -20\n49, -7',speed:16},
 indian:{name:'인도양 해상 구간',coordinates:'-5, 95\n-12, 80\n-20, 60\n-30, 45',speed:16},
};
export const OCEAN_VIEWS={world:[0,20,1.25],pacific:[180,25,2],atlantic:[-35,35,2.1],indian:[75,-15,2.1],mediterranean:[18,36,3.5],southern:[20,-45,2]};
