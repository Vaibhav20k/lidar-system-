import * as THREE from 'three';
import type {StructuralElement} from '../domain/structure';

export function structuralRings(element:StructuralElement): THREE.Vector3[][] {
  const g=element.geometry;
  const sections=g.type==='sections'?g.sections:g.type==='extrusion'?[{at:0,profile:g.profile},{at:g.length,profile:g.profile}]:[0,g.length].map(at=>({at,profile:Array.from({length:64},(_,i)=>({x:g.radius*Math.cos(i*Math.PI/32),z:g.radius*Math.sin(i*Math.PI/32)}))}));
  const quaternion=new THREE.Quaternion(...element.rotation),origin=new THREE.Vector3(element.origin.x,element.origin.y,element.origin.z);
  return sections.map(s=>s.profile.map(p=>new THREE.Vector3(p.x,s.at,p.z).applyQuaternion(quaternion).add(origin)));
}

export function buildStructure(element:StructuralElement,selected=false) {
  const root=new THREE.Group();root.name=`${element.kind} ${element.id}`;
  root.userData={elementId:element.id,structuralElement:element};
  const rings=structuralRings(element),observed=element.coverage.observedFaces;
  const color=selected?'#81aa55':element.review==='candidate'?'#c58b2d':'#69869b';
  const face=(vertices:THREE.Vector3[],indices:number[],name:string)=>{
    const supported=observed.includes(name)||(name==='bottom'&&observed.includes('underside'))||(name.startsWith('side-')&&observed.includes('lateral'));
    const geometry=new THREE.BufferGeometry();geometry.setFromPoints(vertices);geometry.setIndex(indices);geometry.computeVertexNormals();
    const material=new THREE.MeshStandardMaterial({color,roughness:.8,side:THREE.DoubleSide,transparent:true,opacity:supported?.85:.22,depthWrite:supported});
    const mesh=new THREE.Mesh(geometry,material);mesh.name=name;mesh.userData={elementId:element.id,face:name,inferred:!supported};root.add(mesh);
  };
  for(let j=1;j<rings.length;j++)for(let i=0;i<rings[j].length;i++) {
    const next=(i+1)%rings[j].length;
    face([rings[j-1][i],rings[j-1][next],rings[j][next],rings[j][i]],[0,1,2,0,2,3],`side-${i}`);
  }
  // Triangulate in the canonical local profile plane, including concave forms.
  const g=element.geometry;
  for(const [index,name] of [[0,'bottom'],[rings.length-1,'top']] as const) {
    const profile=g.type==='sections'?g.sections[index].profile:g.type==='extrusion'?g.profile:Array.from({length:64},(_,i)=>({x:g.radius*Math.cos(i*Math.PI/32),z:g.radius*Math.sin(i*Math.PI/32)}));
    const triangles=THREE.ShapeUtils.triangulateShape(profile.map(p=>new THREE.Vector2(p.x,p.z)),[]).flat();
    face(rings[index],triangles,name);
  }
  return root;
}
