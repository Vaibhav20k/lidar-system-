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
const fixtureKind = z.enum(['switch_board', 'socket', 'switch', 'regulator', 'distribution_box', 'other_electrical', 'light', 'hole', 'ac_unit', 'junction_box', 'water_point', 'drain']);
export const fixtureSchema = z.object({
  id, wallId: id, kind: fixtureKind,
  offset: number.min(0), width: number.min(0.02).max(2), height: number.min(0.02).max(1), sill: number.min(0).max(10), depth: number.min(0).max(1).optional(),
  evidence: evidenceSchema, confidence: number.min(0).max(1).optional(), review: z.boolean().optional(),
  switches: z.number().int().min(0).max(40).optional(), sockets: z.number().int().min(0).max(40).optional(),
  note: z.string().max(200).optional(),
  // How far the photos measuring the edges spread, in metres; set only when all four edges were measured on photos.
  tolerance: number.min(0).max(0.2).optional(),
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

// Which side of a wall the room is on, as the sign of the wall's local +Z ((-dz, dx) in plan). Scanned walls are
// oriented so +Z faces the room. Walls made from a floor outline follow its order, so the outline's winding tells:
// counter-clockwise (positive area) puts the room on the left of each wall, which is +Z.
export function wallInside(room: Room, wall: Wall): 1 | -1 {
  if (room.source === 'roomplan') return 1;
  if (room.floor.length >= 3) return signedArea(room.floor) >= 0 ? 1 : -1;
  const ends = room.walls.flatMap(w => [w.start, w.end]), cx = ends.reduce((s, p) => s + p.x, 0) / ends.length, cz = ends.reduce((s, p) => s + p.z, 0) / ends.length;
  return (cx - wall.start.x) * -(wall.end.z - wall.start.z) + (cz - wall.start.z) * (wall.end.x - wall.start.x) >= 0 ? 1 : -1;
}

const BEAM_DROP = 0.1;  // a lower region counts as a beam over the wall when it hangs at least this far
const covers = (points: Point[], p: Point) => { let inside = false; for (let i = 0, j = points.length - 1; i < points.length; j = i++) { const a = points[i], b = points[j]; if ((a.z > p.z) !== (b.z > p.z) && p.x < (b.x - a.x) * (p.z - a.z) / (b.z - a.z) + a.x) inside = !inside; } return inside; };
/** The underside height of the lowest beam right over a spot on a wall (3 cm out into the room), or undefined. */
function beamOver(room: Room, wall: Wall, along: number): number | undefined {
  const ceiling = room.ceiling;
  if (!ceiling) return undefined;
  const length = wallLength(wall), dx = (wall.end.x - wall.start.x) / length, dz = (wall.end.z - wall.start.z) / length, out = 0.03 * wallInside(room, wall);
  const p = { x: wall.start.x + dx * along - dz * out, z: wall.start.z + dz * along + dx * out };
  const heights = ceiling.regions.filter(r => r.height <= ceiling.height - BEAM_DROP && covers(r.points, p)).map(r => r.height);
  return heights.length ? Math.min(...heights) : undefined;
}
/** Beams running along a wall, as stretches of it (metres from the wall's start) with their underside height. */
export function beamsAlongWall(room: Room, wall: Wall): { from: number; to: number; height: number }[] {
  const length = wallLength(wall), step = 0.05, runs: { from: number; to: number; height: number }[] = [];
  for (let along = step / 2; along < length; along += step) {
    const height = beamOver(room, wall, along), last = runs.at(-1);
    if (height === undefined) continue;
    if (last && Math.abs(last.height - height) < 0.02 && along - last.to <= step * 1.5) last.to = Math.min(length, along + step / 2);
    else runs.push({ from: Math.max(0, along - step / 2), to: Math.min(length, along + step / 2), height });
  }
  // The end of the next wall's beam only touches this one at the corner.
  return runs.filter(r => r.to - r.from >= Math.min(0.5, length / 2));
}

// Everything an electrician, plumber or carpenter needs for one point on a wall: distances from both corners of the
// wall, heights from the floor right under it (a wall on a step or platform has its own floor level), the gap to the
// ceiling or the beam above it, and its size.
export type PointDimensions = { wall: number; fromStart: number; fromEnd: number; bottom: number; centre: number; top: number; toCeiling: number | null; overhead: 'ceiling' | 'beam' | null; width: number; height: number; tolerance?: number };
export function pointDimensions(room: Room, fixture: Fixture): PointDimensions | null {
  const index = room.walls.findIndex(w => w.id === fixture.wallId);
  if (index < 0) return null;
  const wall = room.walls[index], length = wallLength(wall);
  const beam = beamOver(room, wall, fixture.offset + fixture.width / 2), overheadHeight = beam ?? room.ceiling?.height;
  // Ceiling heights are from the room's main floor; the point's top is too once the wall's own floor level is added.
  const topAboveMainFloor = wall.baseY + fixture.sill + fixture.height;
  return { wall: index + 1, fromStart: fixture.offset, fromEnd: length - fixture.offset - fixture.width,
    bottom: fixture.sill, centre: fixture.sill + fixture.height / 2, top: fixture.sill + fixture.height,
    toCeiling: overheadHeight === undefined ? null : overheadHeight - topAboveMainFloor, overhead: overheadHeight === undefined ? null : beam !== undefined ? 'beam' : 'ceiling',
    width: fixture.width, height: fixture.height, ...(fixture.tolerance !== undefined ? { tolerance: fixture.tolerance } : {}) };
}
// The points of a room as CSV (centimetres), for a spreadsheet.
export function pointsCsv(room: Room): string {
  const cm = (v: number | null | undefined) => v === null || v === undefined ? '' : (v * 100).toFixed(1);
  const rows = [['Point', 'Kind', 'Wall', 'From wall start (cm)', 'From wall end (cm)', 'Bottom above floor (cm)', 'Centre above floor (cm)', 'Top above floor (cm)', 'Gap to ceiling or beam (cm)', 'Above it', 'Width (cm)', 'Height (cm)', 'Within (± mm)', 'Check', 'Note']];
  (room.fixtures ?? []).forEach((f, i) => {
    const d = pointDimensions(room, f);
    if (!d) return;
    rows.push([String(i + 1), fixtureLabel(f.kind), String(d.wall), cm(d.fromStart), cm(d.fromEnd), cm(d.bottom), cm(d.centre), cm(d.top), cm(d.toCeiling), d.overhead ?? '', cm(d.width), cm(d.height),
      d.tolerance !== undefined ? (d.tolerance * 1000).toFixed(0) : '', f.review ? 'yes' : '', (f.note ?? '').replace(/\s+/g, ' ')]);
  });
  return rows.map(r => r.map(v => /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v).join(',')).join('\n');
}
export type Evidence = z.infer<typeof evidenceSchema>;

// Something the scan missed, drawn by hand as a box on a wall: a door, window or opening, or a fixture on the wall.
export type DrawnKind = 'window' | 'door' | 'opening' | Fixture['kind'];
export function addDrawn(room: Room, box: { wallId: string; offset: number; sill: number; width: number; height: number }, kind: DrawnKind): { room: Room; id: string } {
  const id = crypto.randomUUID(), round = (v: number) => Math.round(v * 1000) / 1000;
  const placed = { id, wallId: box.wallId, offset: round(box.offset), width: round(box.width), height: round(box.height), sill: round(box.sill), evidence: 'edited' as const };
  if (kind === 'window' || kind === 'door' || kind === 'opening') return { room: { ...room, openings: [...room.openings, { ...placed, kind }] }, id };
  if (placed.width > 2 || placed.height > 1) throw new Error('A wall fixture is at most 2 m wide and 1 m tall; draw a smaller box.');
  return { room: { ...room, fixtures: [...(room.fixtures ?? []), { ...placed, kind, note: 'Drawn by hand on the scan.' }] }, id };
}

// One line saying what an element is and its key dimension, for tooltips and the selection card.
export function describeElement(room: Room, id: string): string {
  const wall = room.walls.findIndex(w => w.id === id);
  const column = columnsOf(room).find(c => c.wallIds.includes(id));
  if (column) return `${columnName(column)} · ${column.height.toFixed(2)} m high · this face ${wallLength(room.walls[wall]).toFixed(2)} m`;
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

// Columns, read from the walls. RoomPlan has no columns: it draws a column's exposed faces as short walls. Walking
// round the room, a corner column is two short walls between an ordinary corner, a corner turning into the room and
// another ordinary corner; a column standing out of a wall is three short walls with the two middle corners turning
// into the room (the other way round is a niche). Sizes are the face lengths, which match the LiDAR. Only full-height
// boxes count; anything else is left as walls.
export type Column = { kind: 'corner' | 'wall'; wallIds: string[]; width: number; depth: number; height: number };
const COLUMN_SIDE = 0.8, SQUARE = 0.26, JOIN = 0.03;
export function columnsOf(room: Room): Column[] {
  const walls = room.walls;
  if (walls.length < 4) return [];
  // The wall list is not in walking order; follow the corners instead.
  const next = new Map(walls.map(w => [w.id, walls.find(v => v !== w && distance(w.end, v.start) < JOIN)]));
  const direction = (w: Wall) => { const l = wallLength(w); return { x: (w.end.x - w.start.x) / l, z: (w.end.z - w.start.z) / l }; };
  const bend = (a: Wall, b: Wall) => { const p = direction(a), q = direction(b); return p.x * q.z - p.z * q.x; };
  // Going round a room, the usual corners outnumber the ones turning into it, so the bends add up to its direction.
  const orientation = Math.sign(walls.reduce((sum, w) => { const v = next.get(w.id); return v ? sum + bend(w, v) : sum; }, 0));
  // +1 for a corner that turns the usual way round the room, -1 for one that turns into it, 0 if not square.
  const turn = (a: Wall, b: Wall) => {
    const p = direction(a), q = direction(b);
    return Math.abs(p.x * q.x + p.z * q.z) > SQUARE ? 0 : Math.sign(bend(a, b) * orientation);
  };
  const short = (w?: Wall): w is Wall => !!w && wallLength(w) <= COLUMN_SIDE;
  // A column runs the full height: its faces are as tall as each other and as the walls either side. Faces of
  // different heights are a step in the wall (under a beam, beside a low partition), not a column.
  const fullHeight = (faces: Wall[], sides: Wall[]) => Math.max(...faces.map(w => w.height)) - Math.min(...faces.map(w => w.height)) <= 0.05
    && Math.min(...faces.map(w => w.height)) >= Math.min(...sides.map(w => w.height)) - 0.05;
  const used = new Set<string>(), columns: Column[] = [];
  for (const a of walls) {
    const b = next.get(a.id), c = b && next.get(b.id), d = c && next.get(c.id), e = d && next.get(d.id);
    if (!b || !c || !d || [b, c, d].some(w => used.has(w.id)) || wallLength(a) <= COLUMN_SIDE) continue;
    const corners = [turn(a, b), turn(b, c), turn(c, d)];
    if (e && short(b) && short(c) && short(d) && wallLength(e) > COLUMN_SIDE && corners[0] === 1 && corners[1] === -1 && corners[2] === -1 && turn(d, e) === 1
        && Math.abs(wallLength(b) - wallLength(d)) < 0.05 && fullHeight([b, c, d], [a, e])) {
      columns.push({ kind: 'wall', wallIds: [b.id, c.id, d.id], width: wallLength(c), depth: (wallLength(b) + wallLength(d)) / 2, height: Math.min(b.height, c.height, d.height) });
      [b, c, d].forEach(w => used.add(w.id));
    } else if (short(b) && short(c) && wallLength(d) > COLUMN_SIDE && corners[0] === 1 && corners[1] === -1 && corners[2] === 1 && fullHeight([b, c], [a, d])) {
      columns.push({ kind: 'corner', wallIds: [b.id, c.id], width: wallLength(b), depth: wallLength(c), height: Math.min(b.height, c.height) });
      [b, c].forEach(w => used.add(w.id));
    }
  }
  return columns;
}
export const columnName = (column: Column) => `${column.kind === 'corner' ? 'Corner column' : 'Column'} · ${Math.round(column.width * 100)} × ${Math.round(column.depth * 100)} cm`;
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
// The floor from one tape measurement. The ceiling is measured well by LiDAR, but a shiny floor reflects it and the
// scan can put the floor 20 cm off. Given the real floor-to-ceiling height, every height is moved onto the real floor;
// tops of walls, doors and windows stay where they are, so their heights shrink or grow with it.
export function setRoomHeight(room: Room, measuredHeight: number): Room {
  if (!room.ceiling) throw new Error('This room has no measured ceiling to measure the height to.');
  if (!(measuredHeight > 1.5 && measuredHeight < 12)) throw new Error('Enter the floor-to-ceiling height in metres.');
  const up = room.ceiling.height - measuredHeight;  // how far the real floor is above the scanned one
  if (Math.abs(up) > 0.5) throw new Error(`That is ${Math.abs(up).toFixed(2)} m from the scanned height; measure to the main ceiling, not a beam or a lower part.`);
  const note = `Heights set from a tape-measured room height of ${measuredHeight.toFixed(3)} m (floor moved ${up >= 0 ? 'up' : 'down'} ${Math.abs(up * 100).toFixed(1)} cm).`;
  return parseRoom({ ...room, updatedAt: new Date().toISOString(),
    walls: room.walls.map(w => ({ ...w, height: w.height - up, evidence: 'edited' as const })),
    openings: room.openings.map(o => { const sill = Math.max(0, o.sill - up); return { ...o, sill, height: o.sill + o.height - up - sill, evidence: 'edited' as const }; }),
    ceiling: { ...room.ceiling, height: measuredHeight, regions: room.ceiling.regions.map(d => ({ ...d, height: d.height - up })),
      items: room.ceiling.items?.map(i => ({ ...i, height: i.height - up })) },
    ...(room.fixtures ? { fixtures: room.fixtures.map(f => ({ ...f, sill: Math.max(0, f.sill - up) })) } : {}),
    objects: room.objects.map(o => ({ ...o, position: { ...o.position, y: o.position.y - up } })),
    warnings: [...room.warnings.filter(w => !w.startsWith('Heights set from a tape-measured') && !w.startsWith('Heights measured from')), note],
  });
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
