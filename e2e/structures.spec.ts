import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {newRoom} from '../src/domain/room';

test('structural candidate survives import, review, save and GLB export',async({page})=>{
  const room={...newRoom('Structural review'),structures:[{id:'column-test',kind:'column',origin:{x:2,y:0,z:2},rotation:[0,0,0,1],geometry:{type:'cylinder',radius:.3,length:2.7},evidence:'estimated',review:'candidate',coverage:{status:'partial',observedFaces:['lateral'],inferredFaces:['top','bottom']},provenance:{processingVersion:'e2e-synthetic',frameIds:[1,8]},connections:[],quality:{absoluteAccuracyVerified:false,status:'unverified'}}]};
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await page.getByRole('button',{name:'Import room JSON'}).setInputFiles({name:'structural.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(room))});
  await page.getByRole('button',{name:'column candidate · partial coverage',exact:true}).click();
  await expect(page.getByText(/Pale faces are inferred/)).toBeVisible();
  await page.getByRole('button',{name:'Keep candidate',exact:true}).click();
  await page.getByRole('button',{name:'Save',exact:true}).click();
  await page.reload();
  await expect(page.getByRole('button',{name:'column confirmed · partial coverage',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Export',exact:true}).click();
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:/GLB 3D model/}).click();
  const glb=await readFile((await(await download).path())!);
  expect(glb.subarray(0,4).toString()).toBe('glTF');
  const jsonLength=glb.readUInt32LE(12),model=JSON.parse(glb.subarray(20,20+jsonLength).toString());
  const node=model.nodes.find((n:{extras?:{structuralElement?:{id:string}}})=>n.extras?.structuralElement?.id==='column-test');
  expect(node.extras.structuralElement.geometry).toEqual({type:'cylinder',radius:.3,length:2.7});
  expect(node.extras.structuralElement.quality.absoluteAccuracyVerified).toBe(false);
  expect(errors).toEqual([]);
});
