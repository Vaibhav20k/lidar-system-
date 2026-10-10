import { afterEach, describe, expect, it } from 'vitest';
import request from 'supertest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createApp } from '../server/app';
import { newRoom } from '../src/domain/room';

const instances: ReturnType<typeof createApp>[]=[];
afterEach(()=>{for(const instance of instances.splice(0)) instance.close();});
function server(token=''){const value=createApp(':memory:',token);instances.push(value);return value.app;}
describe('room sync API',()=>{
  it('requires bearer auth and rejects unexpected origins',async()=>{
    const app=server('test-token');
    expect((await request(app).get('/api/rooms')).status).toBe(401);
    expect((await request(app).get('/api/rooms').set('Authorization','Bearer test-token')).status).toBe(200);
    expect((await request(app).get('/api/rooms').set('Origin','https://unrelated.example')).status).toBe(403);
    expect((await request(app).options('/api/rooms').set('Origin','capacitor://localhost')).status).toBe(204);
  });
  it('round-trips measurements, strips device paths and prevents stale overwrites',async()=>{
    const app=server(),room={...newRoom('Measured room'),originalUsdzPath:'/private/device/file.usdz'};
    expect((await request(app).put(`/api/rooms/${room.id}`).send({room,revision:0})).body.revision).toBe(1);
    const loaded=await request(app).get(`/api/rooms/${room.id}`);
    expect(loaded.body.room.name).toBe(room.name); expect(loaded.body.room.originalUsdzPath).toBeUndefined();
    expect((await request(app).put(`/api/rooms/${room.id}`).send({room:{...room,name:'Stale'},revision:0})).status).toBe(409);
    expect((await request(app).get(`/api/rooms/${room.id}`)).body.room.name).toBe(room.name);
    expect((await request(app).put(`/api/rooms/${room.id}`).send({room:{...room,name:'Updated'},revision:1})).body.revision).toBe(2);
  });
  it('rejects invalid geometry, mismatched ids and malformed JSON',async()=>{
    const app=server(),room=newRoom('Room');
    expect((await request(app).put(`/api/rooms/${room.id}`).send({room:{...room,walls:[]},revision:0})).status).toBe(400);
    expect((await request(app).put('/api/rooms/wrong-id').send({room,revision:0})).status).toBe(400);
    expect((await request(app).put(`/api/rooms/${room.id}`).type('json').send('{bad')).status).toBe(400);
    expect((await request(app).get('/api/rooms')).body.rooms).toHaveLength(0);
  });
  it('persists rooms after closing and reopening the database',async()=>{
    const directory=mkdtempSync(join(tmpdir(),'roomform-test-')),path=join(directory,'rooms.sqlite');
    let instance=createApp(path); const room=newRoom('Persistent');
    try {
      await request(instance.app).put(`/api/rooms/${room.id}`).send({room,revision:0});
      instance.close(); instance=createApp(path);
      expect((await request(instance.app).get('/api/rooms')).body.rooms[0].room.name).toBe('Persistent');
    } finally {instance.close();rmSync(directory,{recursive:true,force:true});}
  });
});
