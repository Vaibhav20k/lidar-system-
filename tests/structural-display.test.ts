import {it,expect} from 'vitest';
import * as THREE from 'three';
import {structuralElementSchema} from '../src/domain/structure';
import {newRoom,parseRoom,confirmElement,removeElement,needsCheck} from '../src/domain/room';
import {buildRoomGroup,disposeGroup} from '../src/scene/buildRoom';
import {structuralRings} from '../src/scene/structures';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';
import FloorPlan from '../src/components/FloorPlan';

const element=()=>structuralElementSchema.parse({id:'L-column',kind:'column',origin:{x:1,y:0,z:1},rotation:[0,0,0,1],geometry:{type:'extrusion',length:3,profile:[{x:0,z:0},{x:1,z:0},{x:1,z:.3},{x:.3,z:.3},{x:.3,z:1},{x:0,z:1}]},evidence:'estimated',review:'candidate',coverage:{status:'partial',observedFaces:['side-0'],inferredFaces:['top','bottom']},provenance:{processingVersion:'test',frameIds:[0,5]},quality:{absoluteAccuracyVerified:false,status:'unverified'}});

it('renders a concave canonical profile without replacing it by a box',()=>{
  const e=element(),room=parseRoom({...newRoom('Concave'),structures:[e]}),group=buildRoomGroup(room,{ceiling:true});
  const structural=group.children.find(o=>o.userData.elementId===e.id)!;
  const cap=structural.getObjectByName('top') as THREE.Mesh;
  const p=cap.geometry.getAttribute('position'),indices=cap.geometry.index!;
  let area=0;
  for(let i=0;i<indices.count;i+=3){const a=new THREE.Vector3().fromBufferAttribute(p,indices.getX(i)),b=new THREE.Vector3().fromBufferAttribute(p,indices.getX(i+1)),c=new THREE.Vector3().fromBufferAttribute(p,indices.getX(i+2));area+=b.sub(a).cross(c.sub(a)).length()/2;}
  expect(area).toBeCloseTo(.51);expect(structural.userData.structuralElement).toEqual(e);
  expect(cap.userData.inferred).toBe(true);expect(structural.getObjectByName('side-0')!.userData.inferred).toBe(false);
  expect(renderToStaticMarkup(createElement(FloorPlan,{room}))).toContain('inferred faces');
  disposeGroup(group);
});

it('confirmation does not verify accuracy and rejection retains evidence but hides geometry',()=>{
  const room=parseRoom({...newRoom('Review'),structures:[element()]});
  expect(needsCheck(room)).toContain('L-column');
  const kept=confirmElement(room,'L-column');expect(needsCheck(kept)).not.toContain('L-column');
  expect(kept.structures![0].quality.absoluteAccuracyVerified).toBe(false);
  const rejected=parseRoom(removeElement(kept,'L-column')),group=buildRoomGroup(rejected);
  expect(rejected.structures![0].provenance.frameIds).toEqual([0,5]);
  expect(group.children.some(o=>o.userData.elementId==='L-column')).toBe(false);disposeGroup(group);
});

it('retains circular radius and quaternion orientation in projected rings',()=>{
  const e=structuralElementSchema.parse({...element(),rotation:[0,0,Math.SQRT1_2,Math.SQRT1_2],geometry:{type:'cylinder',radius:.25,length:2}});
  const rings=structuralRings(e);
  const centers=rings.map(r=>r.reduce((a,b)=>a.add(b),new THREE.Vector3()).divideScalar(r.length));
  expect(centers[1].x-centers[0].x).toBeCloseTo(-2);expect(rings[0][0].distanceTo(centers[0])).toBeCloseTo(.25);
});
