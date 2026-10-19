import {it,expect} from 'vitest';
import request from 'supertest';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createApp} from '../server/app';
import {newRoom} from '../src/domain/room';

it('recovers interrupted jobs and refuses to overwrite the original bundle',async()=>{
  const root=mkdtempSync(join(tmpdir(),'roomform-jobs-')),id='00000000-0000-0000-0000-000000000001';
  const folder=join(root,'scans',id);mkdirSync(join(folder,'bundle'),{recursive:true});
  writeFileSync(join(folder,'bundle','manifest.json'),'{}');
  writeFileSync(join(folder,'job.json'),JSON.stringify({status:'processing',stage:'Geometry',notes:[]}));
  const instance=createApp(':memory:','',root);
  try {
    const recovered=await request(instance.app).get(`/api/scans/${id}`);
    expect(recovered.body.status).toBe('failed');expect(recovered.body.error).toMatch(/restarted/);
    expect((await request(instance.app).put(`/api/scans/${id}/raw`).set('Content-Type','application/zip').send(Buffer.from('bad'))).status).toBe(409);
    expect(readFileSync(join(folder,'bundle','manifest.json'),'utf8')).toBe('{}');
    const result=await request(instance.app).post(`/api/scans/${id}/process`).send({room:{...newRoom('Wrong capture'),captureId:'different'},capture:{}});
    expect(result.status).toBe(409);
  }finally{instance.close();rmSync(root,{recursive:true,force:true});}
});
