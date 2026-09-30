import test from 'node:test';
import assert from 'node:assert/strict';
import {SHIPS} from '../../web/public/console/ships.js';
import {initialState} from '../../web/public/console/helm-domain.js';
import {coastWindow,coastCovers,collision,harborObstacles} from '../../web/public/console/helm-navigation.js';

const ship=SHIPS[0];
const square=(x0,y0,x1,y1)=>({type:'Polygon',coordinates:[[[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]]});
const window=(geometries,center=[0,0])=>coastWindow({status:'ready',center,radius_degrees:.03,geometries,source:'test'});

test('unknown coast coverage fails closed and full hull is checked',()=>{
 const state=initialState(0,0,90),far=initialState(.02,0,90),water=window([]);
 assert.equal(collision(state,state,ship,null),'unknown');
 assert.equal(coastCovers(state,ship,water),true);
 assert.equal(coastCovers(far,ship,water),false);
 assert.equal(collision(state,far,ship,water),'unknown');
 assert.throws(()=>coastWindow({status:'ready',geometries:[]}));
});
test('hull edge, land interior, and swept path stop passage',()=>{
 const obstacle=window([square(.002,-.002,.003,.002)]);
 assert.equal(collision(initialState(.0025,0,90),initialState(.0025,0,90),ship,obstacle),'land');
 assert.equal(collision(initialState(0,0,90),initialState(.005,0,90),ship,obstacle),'land');
 assert.equal(collision(initialState(-.005,0,90),initialState(-.004,0,90),ship,obstacle),null);
});
test('loaded OSM pier stops a crossing; water dock is not treated as solid',()=>{
 const water=window([]),geo={features:[
  {properties:{kind:'pier'},geometry:{type:'LineString',coordinates:[[.002,-.01],[.002,.01]]}},
  {properties:{kind:'dock'},geometry:square(-.003,-.003,-.002,.003)},
 ]};
 const facilities=harborObstacles(geo,water.center);
 assert.equal(facilities.length,1);
 assert.equal(collision(initialState(0,0,90),initialState(.004,0,90),ship,water,facilities),'facility');
 assert.equal(collision(initialState(-.003,0,90),initialState(-.003,0,90),ship,water,facilities),null);
});
test('date-line geometry is unwrapped near the ship',()=>{
 const coast=window([square(-179.999,-.002,-179.998,.002)],[179.999,0]);
 assert.equal(collision(initialState(179.999,0,90),initialState(-179.997,0,90),ship,coast),'land');
});
