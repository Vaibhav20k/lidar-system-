import { it, expect } from 'vitest';
import { newRoom, parseRoom, calibrateRoom, pointDimensions } from '../src/domain/room';
import { buildRoomGroup, disposeGroup } from '../src/scene/buildRoom';

const fixture = {id:'ac',wallId:'wall-1',kind:'ac_unit',offset:1,sill:1,width:.4,height:.2,depth:.2,tolerance:.01,evidence:'estimated'};
it('calibration scales depth and invalidates tolerance without clamping oversized geometry',()=>{
  const room=parseRoom({...newRoom('Scale'),fixtures:[fixture]});
  const scaled=calibrateRoom(room,10,'wall-1');
  expect(scaled.fixtures?.[0]).toMatchObject({depth:.4,width:.8,review:true});
  expect(scaled.fixtures?.[0].tolerance).toBeUndefined();
  expect(()=>calibrateRoom(parseRoom({...room,fixtures:[{...fixture,width:1.5}]}),10,'wall-1')).toThrow();
});
it('ceiling holes are excluded from overhead beam measurements',()=>{
  const room=parseRoom({...newRoom('Hole'),fixtures:[fixture],ceiling:{height:3,regions:[{points:[{x:0,z:0},{x:5,z:0},{x:5,z:4},{x:0,z:4}],holes:[[{x:.5,z:0},{x:2,z:0},{x:2,z:1},{x:.5,z:1}]],height:2.5,evidence:'estimated'}]}});
  expect(pointDimensions(room,room.fixtures![0])?.overhead).toBe('ceiling');
});
it('export scene options include ceiling and ceiling fixtures',()=>{
  const room=parseRoom({...newRoom('Ceiling'),ceiling:{height:3,regions:[],items:[{id:'lamp',kind:'light',x:1,z:1,height:3,width:.2,depth:.2,rotation:0,evidence:'estimated'}]}});
  const group=buildRoomGroup(room,{ceiling:true});
  let lamp=false; group.traverse(o=>{lamp ||= o.userData.elementId==='lamp';});
  expect(group.getObjectByName('Ceiling')).toBeDefined(); expect(lamp).toBe(true); disposeGroup(group);
});
