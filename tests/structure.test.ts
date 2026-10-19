import { describe, it, expect } from 'vitest';
import request from 'supertest';
import { createApp } from '../server/app';
import { newRoom, parseRoom, calibrateRoom } from '../src/domain/room';
import { structuralElementSchema } from '../src/domain/structure';

export const columnFixture = () => structuralElementSchema.parse({id:'column-A',kind:'column',origin:{x:1,y:0,z:1},rotation:[0,0,0,1],
  geometry:{type:'extrusion',profile:[{x:0,z:0},{x:.4,z:0},{x:.4,z:.5},{x:0,z:.5}],length:3},
  evidence:'estimated',review:'candidate',coverage:{status:'partial',observedFaces:['side-0','side-1'],inferredFaces:['bottom','top']},
  provenance:{captureId:'capture-A',processingVersion:'synthetic-v1',frameIds:[1,2,3]},connections:[],quality:{absoluteAccuracyVerified:false,status:'unverified'}});

describe('versioned structures',()=>{
  it('migrates v1 without inventing structure and leaves its source unchanged',()=>{
    const old={...newRoom('Legacy'),schemaVersion:1};
    expect(parseRoom(old).schemaVersion).toBe(2); expect(old.schemaVersion).toBe(1);
    expect(parseRoom(old).structures).toBeUndefined();
    expect(()=>parseRoom({...old,schemaVersion:3})).toThrow();
    expect(()=>parseRoom({...old,structure:{walls:[]}})).toThrow(/Experimental/);
  });
  it('preserves profiles, evidence and capture identity through JSON',()=>{
    const room=parseRoom({...newRoom('Structural'),captureId:'capture-A',structures:[columnFixture()]});
    expect(parseRoom(JSON.parse(JSON.stringify(room)))).toEqual(room);
    const circle={...columnFixture(),geometry:{type:'cylinder',radius:.25,length:3}};
    expect(parseRoom({...room,structures:[circle]}).structures?.[0].geometry.type).toBe('cylinder');
    const sections={...columnFixture(),geometry:{type:'sections',sections:[{at:0,profile:(columnFixture().geometry as {profile:unknown}).profile},{at:2,profile:[{x:0,z:0},{x:.3,z:0},{x:.3,z:.4},{x:0,z:.4}]}]}};
    expect(parseRoom({...room,structures:[sections]}).structures?.[0].geometry.type).toBe('sections');
  });
  it('rejects bad rotation, crossing profiles, duplicate IDs and missing connections',()=>{
    const room=newRoom('Invalid'), element=columnFixture();
    for(const changed of [{...element,rotation:[0,0,0,2]},{...element,id:room.walls[0].id},{...element,connections:['absent']},
      {...element,geometry:{type:'extrusion',length:3,profile:[{x:0,z:0},{x:1,z:1},{x:1,z:0},{x:0,z:1}]}}])
      expect(()=>parseRoom({...room,structures:[changed]})).toThrow();
  });
  it('scales the canonical shape and invalidates prior quality',()=>{
    const room=parseRoom({...newRoom('Scale'),structures:[columnFixture()]});
    const scaled=calibrateRoom(room,10,room.walls[0].id).structures![0];
    expect(scaled.origin.x).toBe(2); expect(scaled.geometry).toMatchObject({length:6});
    expect(scaled.quality.status).toBe('edited');
  });
  it('round-trips structural geometry through revision-aware API',async()=>{
    const instance=createApp(':memory:'); const room=parseRoom({...newRoom('API'),structures:[columnFixture()]});
    try {
      expect((await request(instance.app).put(`/api/rooms/${room.id}`).send({room,revision:0})).status).toBe(200);
      expect((await request(instance.app).get(`/api/rooms/${room.id}`)).body.room.structures).toEqual(room.structures);
      expect((await request(instance.app).put(`/api/rooms/${room.id}`).send({room,revision:0})).status).toBe(409);
    } finally {instance.close();}
  });
});
