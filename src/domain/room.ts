import { z } from 'zod';

const number = z.number().finite();
const id = z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/);
export const pointSchema = z.object({ x: number.min(-500).max(500), z: number.min(-500).max(500) });
export type Point = z.infer<typeof pointSchema>;
export const evidenceSchema = z.enum(['measured', 'estimated', 'edited', 'sample']);
export const wallSchema = z.object({
  id, start: pointSchema, end: pointSchema, height: number.min(0.5).max(12),
  thickness: number.min(0.02).max(1), baseY: number.min(-10).max(10).default(0),
  evidence: evidenceSchema,
});
export const openingSchema = z.object({
  id, wallId: id, kind: z.enum(['door', 'window', 'opening']),
  offset: number.min(0), width: number.min(0.1).max(20),
  height: number.min(0.1).max(12), sill: number.min(0).max(10), evidence: evidenceSchema,
});
// Things fixed to or cut into a wall, found on scan photos: electrical plates, lights, holes, AC units.
// Placed like openings: offset along the wall from its start, sill above its base. `depth` is how far a unit stands out from the wall.
const fixtureKind = z.enum(['switch_board', 'socket', 'switch', 'regulator', 'distribution_box', 'other_electrical', 'light', 'hole', 'ac_unit', 'junction_box']);
export const fixtureSchema = z.object({
  id, wallId: id, kind: fixtureKind,
  offset: number.min(0), width: number.min(0.02).max(2), height: number.min(0.02).max(1), sill: number.min(0).max(10), depth: number.min(0).max(1).optional(),
  evidence: evidenceSchema, confidence: number.min(0).max(1).optional(), review: z.boolean().optional(),
  switches: z.number().int().min(0).max(40).optional(), sockets: z.number().int().min(0).max(40).optional(),
  note: z.string().max(200).optional(),
});
// Ceiling measured from scan depth: the main height above the floor plus every region at another height
// (beams, soffits and lofts below it; coffers and recesses above it).
const outline = z.array(pointSchema).min(3).max(128);
export const ceilingSchema = z.object({
  height: number.min(1).max(12), coverage: number.min(0).max(1).optional(),
  regions: z.array(z.object({ points: outline, holes: z.array(outline).max(32).optional(), height: number.min(0.5).max(12), evidence: evidenceSchema })).max(64),
  // Lights, holes and boxes on the ceiling or under a lower region: world X/Z, the height of the surface they sit on, and a footprint turned by `rotation`.
  items: z.array(z.object({ id, kind: fixtureKind, x: number.min(-500).max(500), z: number.min(-500).max(500), height: number.min(0.5).max(12), width: number.min(0.02).max(3), depth: number.min(0.02).max(3), rotation: number,
    evidence: evidenceSchema, confidence: number.min(0).max(1).optional(), review: z.boolean().optional(), note: z.string().max(200).optional() })).max(256).optional(),
});
export const objectSchema = z.object({
  id, label: z.string().min(1).max(80), category: z.string().min(1).max(50),
  position: z.object({ x: number.min(-500).max(500), y: number.min(-20).max(20), z: number.min(-500).max(500) }),
  size: z.object({ x: number.min(0.02).max(30), y: number.min(0.02).max(12), z: number.min(0.02).max(30) }),
  rotation: number, evidence: evidenceSchema,
  // Scanner-reported traits such as { ChairType: 'swivel', ChairLegType: 'star' }; absent for manual furniture.
  attributes: z.record(z.string().max(40), z.string().max(40)).optional(),
});
export const roomSchema = z.object({
  schemaVersion: z.literal(1), id, name: z.string().trim().min(1).max(100),
  createdAt: z.string().datetime(), updatedAt: z.string().datetime(),
  units: z.literal('meters'), coordinateSystem: z.literal('right-handed-y-up'),
  source: z.enum(['manual', 'roomplan', 'arcore-depth', 'arcore-guided', 'sample']),
  floor: z.array(pointSchema).max(128),
  floorEvidence: z.enum(['inferred', 'manual', 'missing', 'sample']),
  walls: z.array(wallSchema).min(3).max(128),
  openings: z.array(openingSchema).max(128), objects: z.array(objectSchema).max(256),
  fixtures: z.array(fixtureSchema).max(256).optional(), ceiling: ceilingSchema.optional(),
  notes: z.string().max(5000).default(''), warnings: z.array(z.string().max(500)).max(50).default([]),
  originalUsdzPath: z.string().max(2000).optional(),
});
export type Room = z.infer<typeof roomSchema>;
export type Wall = Room['walls'][number];
export type Opening = Room['openings'][number];
export type RoomObject = Room['objects'][number];
export type Fixture = z.infer<typeof fixtureSchema>;
export const fixtureLabel = (kind: Fixture['kind']) => kind === 'ac_unit' ? 'AC unit' : kind.replace('_', ' ');
export type Evidence = z.infer<typeof evidenceSchema>;

// One line saying what an element is and its key dimension, for tooltips and the selection card.
export function describeElement(room: Room, id: string): string {
  const wall = room.walls.findIndex(w => w.id === id);
  if (wall >= 0) return `Wall ${wall + 1} · ${wallLength(room.walls[wall]).toFixed(2)} m long · ${room.walls[wall].height.toFixed(2)} m high`;
  const opening = room.openings.find(o => o.id === id);
  if (opening) return `${opening.kind[0].toUpperCase()}${opening.kind.slice(1)} · ${opening.width.toFixed(2)} × ${opening.height.toFixed(2)} m`;
  const object = room.objects.find(o => o.id === id);
  if (object) return `${object.label[0].toUpperCase()}${object.label.slice(1)} · ${object.size.x.toFixed(2)} × ${object.size.z.toFixed(2)} × ${object.size.y.toFixed(2)} m`;
  const fixture = room.fixtures?.find(f => f.id === id);
  if (fixture) { const name = fixtureLabel(fixture.kind); return `${name[0].toUpperCase()}${name.slice(1)} · ${(fixture.sill + fixture.height / 2).toFixed(2)} m above floor`; }
  const item = room.ceiling?.items?.find(i => i.id === id);
  if (item) { const name = fixtureLabel(item.kind); return `${name[0].toUpperCase()}${name.slice(1)} on the ceiling · ${item.height.toFixed(2)} m up`; }
  return '';
}
// Everything a person should look at before trusting it: fixtures the detector was unsure of, and furniture the scanner itself rated low.
export const needsCheck = (room: Room): string[] => [
  ...(room.fixtures ?? []).filter(f => f.review).map(f => f.id), ...(room.ceiling?.items ?? []).filter(i => i.review).map(i => i.id),
  ...room.objects.filter(o => o.evidence === 'estimated').map(o => o.id),
];
// A person has looked at it and it is right.
export function confirmElement(room: Room, id: string): Room {
  return { ...room,
    objects: room.objects.map(o => o.id === id ? { ...o, evidence: 'edited' as const } : o),
    fixtures: room.fixtures?.map(f => f.id === id ? { ...f, review: undefined, evidence: 'edited' as const } : f),
    ceiling: room.ceiling && { ...room.ceiling, items: room.ceiling.items?.map(i => i.id === id ? { ...i, review: undefined, evidence: 'edited' as const } : i) },
  };
}
export function removeElement(room: Room, id: string): Room {
  return { ...room, openings: room.openings.filter(o => o.id !== id), objects: room.objects.filter(o => o.id !== id), fixtures: room.fixtures?.filter(f => f.id !== id),
    ceiling: room.ceiling && { ...room.ceiling, items: room.ceiling.items?.filter(i => i.id !== id) } };
}
export const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.z - b.z);
export const wallLength = (wall: Wall) => distance(wall.start, wall.end);
export function signedArea(points: Point[]): number {
  return points.reduce((sum, p, i) => {
    const q = points[(i + 1) % points.length];
    return sum + p.x * q.z - q.x * p.z;
  }, 0) / 2;
}
export const floorArea = (room: Room) => Math.abs(signedArea(room.floor));
export const perimeter = (room: Room) => room.walls.reduce((sum, wall) => sum + wallLength(wall), 0);
const cross = (a: Point, b: Point, c: Point) => (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x);
function intersects(a: Point, b: Point, c: Point, d: Point): boolean {
  const on = (p: Point, q: Point, r: Point) => Math.abs(cross(p, q, r)) < 1e-8 && r.x >= Math.min(p.x, q.x) - 1e-8 && r.x <= Math.max(p.x, q.x) + 1e-8 && r.z >= Math.min(p.z, q.z) - 1e-8 && r.z <= Math.max(p.z, q.z) + 1e-8;
  return (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) || on(a, b, c) || on(a, b, d) || on(c, d, a) || on(c, d, b);
}
export function polygonErrors(points: Point[]): string[] {
  if (points.length < 3) return ['A floor outline needs at least three corners.'];
  if (Math.abs(signedArea(points)) < 0.25) return ['Floor area must be at least 0.25 m².'];
  for (let i = 0; i < points.length; i++) {
    if (distance(points[i], points[(i + 1) % points.length]) < 0.1) return ['Adjacent floor corners must be at least 10 cm apart.'];
    for (let j = i + 1; j < points.length; j++) {
      if (j === i + 1 || (i === 0 && j === points.length - 1)) continue;
      if (intersects(points[i], points[(i + 1) % points.length], points[j], points[(j + 1) % points.length])) return ['The floor outline crosses itself. Reorder or correct its corners.'];
    }
  }
  return [];
}
export function parseRoom(value: unknown): Room {
  const room = roomSchema.parse(value);
  const allIds = [...room.walls, ...room.openings, ...room.objects, ...(room.fixtures ?? []), ...(room.ceiling?.items ?? [])].map(v => v.id);
  if (new Set(allIds).size !== allIds.length) throw new Error('Room element IDs must be unique.');
  if (room.floor.length) {
    const errors = polygonErrors(room.floor);
    if (errors.length) throw new Error(errors[0]);
    if (room.floorEvidence === 'missing') throw new Error('A floor outline must have an evidence source.');
  } else if (room.floorEvidence !== 'missing') throw new Error('A missing floor must be marked as missing.');
  for (const wall of room.walls) if (wallLength(wall) < 0.1 || wallLength(wall) > 100) throw new Error('Wall length must be between 0.1 and 100 m.');
  for (const opening of room.openings) {
    const wall = room.walls.find(v => v.id === opening.wallId);
    if (!wall) throw new Error('An opening refers to a missing wall.');
    if (opening.offset + opening.width > wallLength(wall) + 0.005) throw new Error('An opening extends beyond its wall.');
    if (opening.sill + opening.height > wall.height + 0.005) throw new Error('An opening extends above its wall.');
    const others = room.openings.filter(v => v.wallId === wall.id && v.id !== opening.id);
    if (others.some(v => opening.offset < v.offset + v.width - 0.005 && opening.offset + opening.width > v.offset + 0.005 && opening.sill < v.sill + v.height - 0.005 && opening.sill + opening.height > v.sill + 0.005)) throw new Error('Openings on the same wall cannot overlap.');
  }
  for (const fixture of room.fixtures ?? []) {
    const wall = room.walls.find(v => v.id === fixture.wallId);
    if (!wall) throw new Error('An electrical fixture refers to a missing wall.');
    if (fixture.offset + fixture.width > wallLength(wall) + 0.005 || fixture.sill + fixture.height > wall.height + 0.005) throw new Error('An electrical fixture extends beyond its wall.');
  }
  return room;
}
export function bounds(room: Room) {
  const p = room.walls.flatMap(w => [w.start, w.end]);
  const minX = Math.min(...p.map(v => v.x)), maxX = Math.max(...p.map(v => v.x));
  const minZ = Math.min(...p.map(v => v.z)), maxZ = Math.max(...p.map(v => v.z));
  return { minX, maxX, minZ, maxZ, width: maxX - minX, depth: maxZ - minZ };
}
export function wallsFromFloor(floor: Point[], height: number, evidence: Evidence): Wall[] {
  return floor.map((start, i) => ({ id: `wall-${i + 1}`, start, end: floor[(i + 1) % floor.length], height, baseY: 0, thickness: 0.12, evidence }));
}
export function newRoom(name: string, width = 5, depth = 4, height = 2.7): Room {
  if (!Number.isFinite(width) || !Number.isFinite(depth) || width < 0.5 || depth < 0.5 || width > 50 || depth > 50) throw new Error('Room width and depth must be between 0.5 and 50 meters.');
  const floor = [{ x: 0, z: 0 }, { x: width, z: 0 }, { x: width, z: depth }, { x: 0, z: depth }];
  const now = new Date().toISOString();
  return parseRoom({ schemaVersion: 1, id: crypto.randomUUID(), name, createdAt: now, updatedAt: now, units: 'meters', coordinateSystem: 'right-handed-y-up', source: 'manual', floor, floorEvidence: 'manual', walls: wallsFromFloor(floor, height, 'edited'), openings: [], objects: [], warnings: [], notes: '' });
}
export function calibrateRoom(room: Room, measuredLength: number, wallId: string): Room {
  const wall = room.walls.find(w => w.id === wallId);
  if (!wall || measuredLength <= 0) throw new Error('Select a wall and enter a positive reference length.');
  const s = measuredLength / wallLength(wall);
  const p = (v: Point) => ({ x: v.x * s, z: v.z * s });
  return parseRoom({ ...room, updatedAt: new Date().toISOString(),
    floor: room.floor.map(p),
    walls: room.walls.map(w => ({ ...w, start: p(w.start), end: p(w.end), height: w.height * s, baseY: w.baseY * s, thickness: w.thickness * s, evidence: 'edited' })),
    openings: room.openings.map(o => ({ ...o, offset: o.offset * s, width: o.width * s, height: o.height * s, sill: o.sill * s, evidence: 'edited' })),
    ...(room.ceiling ? { ceiling: { ...room.ceiling, height: room.ceiling.height * s, regions: room.ceiling.regions.map(d => ({ ...d, height: d.height * s, points: d.points.map(p), holes: d.holes?.map(h => h.map(p)), evidence: 'edited' as const })),
      items: room.ceiling.items?.map(i => ({ ...i, ...p(i), height: i.height * s, width: Math.min(3, i.width * s), depth: Math.min(3, i.depth * s), evidence: 'edited' as const })) } } : {}),
    ...(room.fixtures ? { fixtures: room.fixtures.map(f => ({ ...f, offset: f.offset * s, sill: f.sill * s, width: Math.min(2, f.width * s), height: Math.min(1, f.height * s), evidence: 'edited' as const })) } : {}),
    objects: room.objects.map(o => ({ ...o, position: { x: o.position.x * s, y: o.position.y * s, z: o.position.z * s }, size: { x: o.size.x * s, y: o.size.y * s, z: o.size.z * s }, evidence: 'edited' })),
    warnings: [...new Set([...room.warnings, 'Global scale calibrated from one reference. Local distortion still needs review.'])],
  });
}
export function updateCorner(room: Room, index: number, point: Point): Room {
  const old = room.floor[index];
  if (!old) throw new Error('Corner not found.');
  return parseRoom({ ...room, floor: room.floor.map((p, i) => i === index ? point : p), floorEvidence: 'manual',
    walls: room.walls.map(w => ({ ...w, start: distance(w.start, old) < 0.01 ? point : w.start, end: distance(w.end, old) < 0.01 ? point : w.end, evidence: 'edited' })),
  });
}
