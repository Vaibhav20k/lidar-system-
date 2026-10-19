import { z } from 'zod';

const n = z.number().finite();
const id = z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/);
const vector = z.object({ x: n.min(-500).max(500), y: n.min(-500).max(500), z: n.min(-500).max(500) });
const point = z.object({ x: n.min(-50).max(50), z: n.min(-50).max(50) });
const profile = z.array(point).min(3).max(128).superRefine((p, ctx) => {
  const cross = (a: typeof p[0], b: typeof p[0], c: typeof p[0]) => (b.x-a.x)*(c.z-a.z)-(b.z-a.z)*(c.x-a.x);
  const on = (a: typeof p[0], b: typeof p[0], c: typeof p[0]) => Math.abs(cross(a,b,c)) < 1e-9 && c.x >= Math.min(a.x,b.x)-1e-9 && c.x <= Math.max(a.x,b.x)+1e-9 && c.z >= Math.min(a.z,b.z)-1e-9 && c.z <= Math.max(a.z,b.z)+1e-9;
  const area = p.reduce((s,a,i) => s+a.x*p[(i+1)%p.length].z-p[(i+1)%p.length].x*a.z,0)/2;
  let invalid = Math.abs(area) < .0001;
  for (let i=0;i<p.length;i++) {
    const a=p[i], b=p[(i+1)%p.length];
    invalid ||= Math.hypot(a.x-b.x,a.z-b.z)<.001;
    for (let j=i+2;j<p.length;j++) {
      if (i===0 && j===p.length-1) continue;
      const c=p[j], d=p[(j+1)%p.length];
      invalid ||= cross(a,b,c)*cross(a,b,d)<0 && cross(c,d,a)*cross(c,d,b)<0 || on(a,b,c) || on(a,b,d) || on(c,d,a) || on(c,d,b);
    }
  }
  if (invalid) ctx.addIssue({ code:'custom', message:'Structural profile must be a simple nondegenerate polygon.' });
});

// Local profiles lie in X/Z; extrusion is along local Y. Quaternion carries
// arbitrary element orientation. No mesh and profile can disagree: profiles
// are canonical and display meshes are derived from them.
export const structuralGeometrySchema = z.discriminatedUnion('type', [
  z.object({ type:z.literal('extrusion'), profile, length:n.positive().max(100) }).strict(),
  z.object({ type:z.literal('cylinder'), radius:n.positive().max(20), length:n.positive().max(100) }).strict(),
  z.object({ type:z.literal('sections'), sections:z.array(z.object({ at:n.min(0).max(100), profile }).strict()).min(2).max(32) }).strict(),
]).superRefine((g,ctx) => {
  if(g.type==='sections' && (g.sections[0].at!==0 || g.sections.some((s,i)=>i>0 && (s.at<=g.sections[i-1].at || s.profile.length!==g.sections[0].profile.length))))
    ctx.addIssue({code:'custom',message:'Sections must start at zero, increase in height, and have corresponding profile vertices.'});
});

export const structuralElementSchema = z.object({
  id, kind:z.enum(['beam','column','soffit','unknown-structural']),
  origin:vector,
  rotation:z.tuple([n,n,n,n]).refine(q=>Math.abs(Math.hypot(...q)-1)<1e-5,'Rotation must be a unit quaternion (x,y,z,w).'),
  geometry:structuralGeometrySchema,
  evidence:z.enum(['estimated','edited','sample']),
  review:z.enum(['candidate','confirmed','rejected']),
  coverage:z.object({ status:z.enum(['partial','observed','unknown']), observedFaces:z.array(z.string().max(40)).max(256), inferredFaces:z.array(z.string().max(40)).max(256) }).strict(),
  provenance:z.object({ captureId:id.optional(), processingVersion:z.string().min(1).max(80), frameIds:z.array(z.number().int().nonnegative()).max(2048) }).strict(),
  connections:z.array(id).max(64).default([]),
  quality:z.object({ absoluteAccuracyVerified:z.literal(false), status:z.enum(['unverified','consistent','edited']), residualMeters:n.nonnegative().max(100).optional() }).strict(),
}).strict();
export type StructuralElement = z.infer<typeof structuralElementSchema>;

export function scaleStructure(element: StructuralElement, scale: number): StructuralElement {
  const p=(v:{x:number;z:number})=>({x:v.x*scale,z:v.z*scale});
  const g=element.geometry;
  const geometry = g.type==='cylinder' ? {...g,radius:g.radius*scale,length:g.length*scale} : g.type==='extrusion' ? {...g,profile:g.profile.map(p),length:g.length*scale}
    : {...g,sections:g.sections.map(s=>({...s,at:s.at*scale,profile:s.profile.map(p)}))};
  return structuralElementSchema.parse({...element, origin:{x:element.origin.x*scale,y:element.origin.y*scale,z:element.origin.z*scale},geometry,evidence:'edited',quality:{absoluteAccuracyVerified:false,status:'edited'}});
}
