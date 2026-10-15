import { describe, expect, it } from 'vitest';
import { floorArea, newRoom, parseRoom, calibrateRoom, updateCorner, polygonErrors, wallsFromFloor, wallLength, columnsOf, setRoomHeight } from '../src/domain/room';
import { sampleRoom } from '../src/domain/sample';
import { chainWalls, normalizeCapture, inferConvexFloor } from '../src/domain/native';
import { normalizeCaptures } from '../src/services/capture';
import { buildRoomGroup, disposeGroup } from '../src/scene/buildRoom';
import * as THREE from 'three';

describe('room geometry', () => {
  it('preserves a concave floor without filling in the missing region', () => {
    const floor = [{x:0,z:0},{x:5,z:0},{x:5,z:2},{x:2,z:2},{x:2,z:4},{x:0,z:4}];
    const room = parseRoom({...newRoom('L room'), floor, walls:wallsFromFloor(floor,2.7,'measured')});
    expect(floorArea(room)).toBe(14);
    const shuffled = [room.walls[3],room.walls[1],room.walls[5],room.walls[2],room.walls[0],room.walls[4]].map((w,i)=>i%2?{...w,start:w.end,end:w.start}:w);
    expect(Math.abs(chainWalls(shuffled).reduce((a,p,i,ps)=>a+p.x*ps[(i+1)%ps.length].z-ps[(i+1)%ps.length].x*p.z,0)/2)).toBe(14);
  });
  it('rejects crossing, repeated and tiny floor outlines', () => {
    expect(polygonErrors([{x:0,z:0},{x:4,z:3},{x:0,z:3},{x:4,z:0}])).not.toHaveLength(0);
    expect(polygonErrors([{x:0,z:0},{x:4,z:0},{x:4,z:0},{x:0,z:3}])).not.toHaveLength(0);
    expect(()=>newRoom('Too small',.1,.1)).toThrow();
    expect(()=>newRoom('Negative',-5,4)).toThrow();
  });
  it('rejects openings outside the wall, missing references and overlapping openings', () => {
    const room=sampleRoom();
    expect(()=>parseRoom({...room,openings:[{...room.openings[0],offset:5}]})).toThrow(/beyond/);
    expect(()=>parseRoom({...room,openings:[{...room.openings[0],wallId:'missing'}]})).toThrow(/missing wall/);
    expect(()=>parseRoom({...room,openings:[room.openings[0],{...room.openings[0],id:'duplicate-position'}]})).toThrow(/overlap/);
  });
  it('scales all geometry together and preserves original evidence source', () => {
    const room=sampleRoom(), result=calibrateRoom(room,wallLength(room.walls[0])*1.1,room.walls[0].id);
    expect(floorArea(result)).toBeCloseTo(floorArea(room)*1.21);
    expect(result.openings[0].sill).toBeCloseTo(room.openings[0].sill*1.1);
    expect(result.objects[0].position.y).toBeCloseTo(room.objects[0].position.y*1.1);
    expect(result.objects[0].size.x).toBeCloseTo(room.objects[0].size.x*1.1);
    expect(result.walls[0].evidence).toBe('edited'); expect(result.source).toBe('sample');
  });
  it('moves the two connected wall endpoints when editing a corner', () => {
    const room=newRoom('Room'), moved=updateCorner(room,1,{x:6,z:0});
    expect(moved.walls[0].end).toEqual({x:6,z:0}); expect(moved.walls[1].start).toEqual({x:6,z:0});
    expect(floorArea(moved)).toBe(22);
  });
  it('leaves actual holes in exported wall meshes', () => {
    const room=newRoom('Door test');
    room.openings=[{id:'door',wallId:'wall-1',kind:'opening',offset:1,width:1,height:2,sill:0,evidence:'edited'}];
    const group=buildRoomGroup(room); group.updateMatrixWorld(true);
    const ray=new THREE.Raycaster(new THREE.Vector3(1.5,1,-1),new THREE.Vector3(0,0,1));
    expect(ray.intersectObject(group,true).filter(h=>h.object.userData.elementId==='wall-1')).toHaveLength(0);
    ray.set(new THREE.Vector3(.5,1,-1),new THREE.Vector3(0,0,1));
    expect(ray.intersectObject(group,true).filter(h=>h.object.userData.elementId==='wall-1').length).toBeGreaterThan(0);
    disposeGroup(group);
  });
});

function matrix(x:number,y:number,z:number,angle=0) {
  return [Math.cos(angle),0,-Math.sin(angle),0, 0,1,0,0, Math.sin(angle),0,Math.cos(angle),0, x,y,z,1];
}
function appleCapture() {
  const wall=(x:number,z:number,width:number,angle=0)=>({id:crypto.randomUUID(),transform:matrix(x,1.4,z,angle),width,height:2.8,depth:0,confidence:'high'});
  return {kind:'roomplan',walls:[wall(2.5,0,5),wall(5,2,4,-Math.PI/2),wall(2.5,4,5,Math.PI),wall(0,2,4,Math.PI/2)],openings:[],objects:[]} as Record<string,unknown>;
}
describe('columns', () => {
  // A 5 x 4 m room with a 42 x 21 cm column in one corner, the way RoomPlan draws it: two short walls.
  const corner = [{x:0,z:0},{x:5,z:0},{x:5,z:4},{x:0.42,z:4},{x:0.42,z:3.79},{x:0,z:3.79}];
  it('finds a full-height corner column and sizes it from its faces, whatever order the walls are listed in', () => {
    const room = parseRoom({...newRoom('Corner'), floor: corner, walls: wallsFromFloor(corner, 2.8, 'measured')});
    const shuffled = {...room, walls: [room.walls[3], room.walls[0], room.walls[5], room.walls[2], room.walls[4], room.walls[1]]};
    for (const r of [room, shuffled]) {
      const [column, ...rest] = columnsOf(r);
      expect(rest).toHaveLength(0);
      expect(column.kind).toBe('corner');
      expect([column.width, column.depth].sort()).toEqual([expect.closeTo(0.21), expect.closeTo(0.42)]); expect(column.height).toBeCloseTo(2.8);
      expect(column.wallIds).toEqual([room.walls[3].id, room.walls[4].id]);
    }
  });
  it('finds a column standing out of the middle of a wall', () => {
    const floor = [{x:0,z:0},{x:2,z:0},{x:2,z:0.3},{x:2.5,z:0.3},{x:2.5,z:0},{x:5,z:0},{x:5,z:4},{x:0,z:4}];
    const [column, ...rest] = columnsOf(parseRoom({...newRoom('Pilaster'), floor, walls: wallsFromFloor(floor, 2.8, 'measured')}));
    expect(rest).toHaveLength(0);
    expect(column.kind).toBe('wall'); expect(column.width).toBeCloseTo(0.5); expect(column.depth).toBeCloseTo(0.3);
  });
  it('leaves a step whose faces are not full height as walls, and never calls a room corner a column', () => {
    const room = parseRoom({...newRoom('Step'), floor: corner, walls: wallsFromFloor(corner, 2.8, 'measured')});
    expect(columnsOf({...room, walls: room.walls.map((w, i) => i === 4 ? {...w, height: 2.1} : w)})).toHaveLength(0);
    const plain = [{x:0,z:0},{x:5,z:0},{x:5,z:4},{x:0,z:4}];
    expect(columnsOf(parseRoom({...newRoom('Plain'), floor: plain, walls: wallsFromFloor(plain, 2.8, 'measured')}))).toHaveLength(0);
  });
});

describe('room height from a tape', () => {
  it('moves the floor up to the real one and keeps the tops of walls, doors and ceilings where they were', () => {
    const floor = [{x:0,z:0},{x:4,z:0},{x:4,z:3},{x:0,z:3}];
    const base = parseRoom({...newRoom('Shiny'), floor, walls: wallsFromFloor(floor, 3.4, 'measured')});
    const room = parseRoom({...base, ceiling: {height: 3.41, coverage: 0.9, regions: [{points: floor, height: 2.82, evidence: 'estimated'}]},
      openings: [{id: crypto.randomUUID(), wallId: base.walls[0].id, kind: 'door', offset: 1, width: 0.9, height: 2.3, sill: 0, evidence: 'measured'}],
      fixtures: [{id: crypto.randomUUID(), wallId: base.walls[0].id, kind: 'switch_board', offset: 2, width: 0.23, height: 0.08, sill: 0.8, evidence: 'estimated'}]});
    const fixed = setRoomHeight(room, 3.218);
    expect(fixed.ceiling!.height).toBeCloseTo(3.218); expect(fixed.ceiling!.regions[0].height).toBeCloseTo(2.627);
    expect(fixed.walls[0].height).toBeCloseTo(3.208); expect(fixed.fixtures![0].sill).toBeCloseTo(0.608);
    expect(fixed.openings[0].sill).toBe(0); expect(fixed.openings[0].height).toBeCloseTo(2.108);
    expect(() => setRoomHeight(room, 2.6)).toThrow();
  });
});

describe('native measurement normalization', () => {
  it('converts column-major Apple matrices, openings and object yaw to meters', () => {
    const raw=appleCapture();
    raw.openings=[{id:crypto.randomUUID(),kind:'window',transform:matrix(2,1.6,0),width:1.4,height:1,depth:0}];
    raw.objects=[{id:crypto.randomUUID(),category:'sofa',transform:matrix(2,.5,2,Math.PI/2),width:2,height:1,depth:.8}];
    const room=normalizeCapture(raw,'Apple room');
    expect(room.source).toBe('roomplan'); expect(floorArea(room)).toBeCloseTo(20);
    expect(room.openings[0].offset).toBeCloseTo(1.3); expect(room.openings[0].sill).toBeCloseTo(1.1);
    expect(room.objects[0].rotation).toBeCloseTo(Math.PI/2);
  });
  it('turns a multi-room Apple scan into one room each and skips an unreadable room', () => {
    const extra={...appleCapture(),roomIndex:1,sharedSpace:true,rawPath:'/device/raw',floors:[]};
    const result=normalizeCaptures([appleCapture(),{kind:'roomplan',walls:[]},extra],'Flat');
    expect(result.rooms.map(r=>r.name)).toEqual(['Flat','Flat 3']); expect(result.skipped).toBe(1);
    expect(new Set(result.rooms.map(r=>r.id)).size).toBe(2);
    expect(()=>normalizeCaptures([{kind:'roomplan',walls:[]}],'Bad')).toThrow();
  });
  it('keeps incomplete Apple walls but never invents a closed floor', () => {
    const raw=appleCapture(); (raw.walls as unknown[]).pop(); const room=normalizeCapture(raw,'Incomplete');
    expect(room.floor).toEqual([]); expect(room.floorEvidence).toBe('missing');
  });
  it('creates a guided Android room including concave corners with a manual height warning', () => {
    const room=normalizeCapture({kind:'arcore',corners:[{x:0,z:0},{x:5,z:0},{x:5,z:2},{x:2,z:2},{x:2,z:4},{x:0,z:4}],points:[],floorY:-1.5,height:2.6,depth:false},'Android');
    expect(floorArea(room)).toBe(14); expect(room.source).toBe('arcore-guided'); expect(room.walls[0].height).toBe(2.6);
    expect(room.warnings.join()).toMatch(/height.*manually/);
  });
  it('estimates a convex perimeter from synthetic depth and refuses insufficient coverage', () => {
    const points=[];
    for(let i=0;i<120;i++){const t=i/119;points.push({x:5*t,y:.4+(i%15)*.1,z:0},{x:5,y:.4+(i%15)*.1,z:4*t},{x:5*t,y:.4+(i%15)*.1,z:4},{x:0,y:.4+(i%15)*.1,z:4*t});}
    const floor=inferConvexFloor(points,0);
    expect(floor.length).toBe(4); expect(floorArea({...newRoom('Cloud'),floor})).toBeCloseTo(20,0);
    expect(inferConvexFloor(points.filter(p=>p.z===0),0)).toEqual([]);
    expect(()=>normalizeCapture({kind:'arcore',corners:[],points:[],floorY:0,height:2.7,depth:true},'Empty')).toThrow(/closed floor/);
  });
});
