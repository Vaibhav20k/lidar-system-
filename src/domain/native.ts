import { z } from 'zod';
import { ceilingSchema, distance, newRoom, parseRoom, polygonErrors, wallsFromFloor, type Point, type Room, type Wall } from './room';

const finite = z.number().finite().min(-1000).max(1000);
const surface = z.object({ id: z.string().uuid(), transform: z.array(finite).length(16), width: finite.positive(), height: finite.positive(), depth: finite.nonnegative().default(0), confidence: z.string().optional() });
export const captureSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('roomplan'), walls: z.array(surface).min(3).max(128), openings: z.array(surface.extend({ kind: z.enum(['door', 'window', 'opening']) })).max(128), objects: z.array(surface.extend({ category: z.string().max(50), attributes: z.record(z.string().max(40), z.string().max(40)).optional() })).max(256), usdzPath: z.string().optional(),
    floors: z.array(z.object({ transform: z.array(finite).length(16), corners: z.array(z.array(finite).length(3)).max(128).optional() })).max(16).optional(),
    // Measured on the phone from the LiDAR right after the scan (CeilingEstimator.swift); checked below, never trusted blindly.
    ceiling: z.unknown().optional(), ceilingStatus: z.enum(['processing', 'deferred', 'unavailable', 'complete']).optional() }),
  z.object({ kind: z.literal('arcore'), corners: z.array(z.object({ x: finite, z: finite })).max(128), points: z.array(z.object({ x: finite, y: finite, z: finite })).max(20000), floorY: finite, height: z.number().min(0.5).max(12), depth: z.boolean() }),
]);
export type NativeCapture = z.infer<typeof captureSchema>;

// Join only observed endpoints. A convex hull would silently invent a floor for incomplete/L-shaped rooms.
export function chainWalls(walls: Wall[], tolerance = 0.18): Point[] {
  const remaining = [...walls];
  const first = remaining.shift();
  if (!first) return [];
  const points = [first.start, first.end];
  while (remaining.length) {
    const end = points.at(-1)!;
    let best = -1, reverse = false, gap = tolerance;
    remaining.forEach((w, i) => {
      if (distance(end, w.start) < gap) { best = i; gap = distance(end, w.start); reverse = false; }
      if (distance(end, w.end) < gap) { best = i; gap = distance(end, w.end); reverse = true; }
    });
    if (best < 0) return [];
    const w = remaining.splice(best, 1)[0];
    points.push(reverse ? w.start : w.end);
  }
  if (distance(points.at(-1)!, points[0]) > tolerance) return [];
  points.pop();
  return polygonErrors(points).length ? [] : points;
}
export function normalizeCapture(value: unknown, name: string): Room {
  const capture = captureSchema.parse(value);
  const base = newRoom(name);
  if (capture.kind === 'arcore') {
    const floor = capture.corners.length >= 3 ? capture.corners : inferConvexFloor(capture.points, capture.floorY);
    if (polygonErrors(floor).length) throw new Error('The scan did not produce a closed floor. Scan again and mark floor corners in order.');
    const guided = capture.corners.length >= 3;
    return parseRoom({ ...base, source: guided ? 'arcore-guided' : 'arcore-depth', floor, floorEvidence: guided ? 'manual' : 'inferred', walls: wallsFromFloor(floor, capture.height, guided ? 'measured' : 'estimated'),
      warnings: [guided ? 'Floor corners measured with AR tracking. Wall height was entered manually.' : 'Experimental convex room estimate from depth. Check every corner and dimension. Wall height was entered manually.', 'Add doors, windows, and furniture during review. Android semantic object capture is not available in this version.'] });
  }
  const floorY = Math.min(...capture.walls.map(w => w.transform[13] - w.height / 2));
  const walls: Wall[] = capture.walls.map(w => {
    const m = w.transform, half = w.width / 2;
    return { id: w.id, start: { x: m[12] - m[0] * half, z: m[14] - m[2] * half }, end: { x: m[12] + m[0] * half, z: m[14] + m[2] * half }, height: w.height, thickness: 0.12, baseY: m[13] - w.height / 2 - floorY, evidence: w.confidence === 'low' ? 'estimated' : 'measured' };
  });
  let floor = chainWalls(walls), scannedFloor = false;
  if (!floor.length) {
    // Rooms with columns or wall jogs rarely chain cleanly; fall back to the floor polygon the scanner reports (iOS 17+).
    const source = [...(capture.floors ?? [])].sort((a, b) => (b.corners?.length ?? 0) - (a.corners?.length ?? 0))[0];
    const corners = (source?.corners ?? []).map(([x, y]) => ({ x: source.transform[0] * x + source.transform[4] * y + source.transform[12], z: source.transform[2] * x + source.transform[6] * y + source.transform[14] }))
      .filter((p, i, all) => distance(p, all[(i + all.length - 1) % all.length]) >= 0.1);
    if (corners.length >= 3 && !polygonErrors(corners).length) { floor = corners; scannedFloor = true; }
  }
  const warnings = scannedFloor ? ['Floor outline taken from the scanned floor surface; some wall ends do not meet. Wall thickness is a display default, not a measured value.']
    : floor.length ? ['Floor outline inferred from wall endpoints. Wall thickness is a display default, not a measured value.'] : ['Wall endpoints do not form a closed floor. Review walls; floor area is unavailable.', 'Wall thickness is a display default, not a measured value.'];
  const openings: Room['openings'] = [];
  for (const o of capture.openings) {
    const center = { x: o.transform[12], z: o.transform[14] };
    const candidates = walls.map(w => {
      const length = distance(w.start, w.end), dx = (w.end.x - w.start.x) / length, dz = (w.end.z - w.start.z) / length;
      const along = (center.x - w.start.x) * dx + (center.z - w.start.z) * dz;
      const gap = Math.hypot(center.x - (w.start.x + dx * along), center.z - (w.start.z + dz * along));
      const alignment = Math.abs(o.transform[0] * dx + o.transform[2] * dz);
      return { w, along, gap, length, alignment };
    }).filter(v => v.gap < 0.3 && v.alignment > 0.85 && v.along >= -0.1 && v.along <= v.length + 0.1).sort((a, b) => a.gap - b.gap);
    const match = candidates[0];
    if (!match || o.width > match.length + 0.05) { warnings.push(`Opening ${o.id.slice(0, 8)} could not be matched to a wall.`); continue; }
    const width = Math.min(o.width, match.length), offset = Math.max(0, Math.min(match.along - width / 2, match.length - width));
    const sill = Math.max(0, o.transform[13] - o.height / 2 - floorY - match.w.baseY);
    const height = Math.min(o.height, match.w.height - sill);
    if (height < 0.1) continue;
    const opening = { id: o.id, wallId: match.w.id, kind: o.kind, offset, width, height, sill, evidence: o.confidence === 'low' ? 'estimated' as const : 'measured' as const };
    if (openings.some(v => v.wallId === opening.wallId && opening.offset < v.offset + v.width && opening.offset + opening.width > v.offset && opening.sill < v.sill + v.height && opening.sill + opening.height > v.sill)) {
      warnings.push(`Overlapping opening ${o.id.slice(0, 8)} was omitted; check this wall.`); continue;
    }
    openings.push(opening);
  }
  const ceiling = capture.ceiling === undefined ? undefined : ceilingSchema.safeParse(capture.ceiling);
  if (!ceiling?.success && ['processing', 'deferred'].includes(capture.ceilingStatus ?? '')) warnings.push('Room saved; ceiling processing was deferred. Raw scan data is preserved for local processing on the laptop.');
  if (ceiling?.success) warnings.push('Ceiling, beams and hanging fixtures measured on the phone from LiDAR.');
  else if (ceiling) warnings.push('The ceiling measured on the phone was not usable and was left out.');
  if (ceiling?.success && ceiling.data.measurementDiagnostics) {
    const diagnostics = ceiling.data.measurementDiagnostics;
    warnings.push('Ceiling height uses the scanner’s floor estimate; absolute accuracy is not verified.');
    if ((diagnostics.repeatCoverage ?? 0) < .5) warnings.push('Less than half of the observed ceiling has repeat-view coverage.');
    if (diagnostics.repeatSpreadP90Meters > .02) warnings.push(`Ceiling repeat views differ by ${(diagnostics.repeatSpreadP90Meters * 100).toFixed(1)} cm (P90). Recheck before using a 2 cm tolerance.`);
  }
  return parseRoom({ ...base, source: 'roomplan', walls, floor, floorEvidence: floor.length ? 'inferred' : 'missing', openings, ...(ceiling?.success ? { ceiling: ceiling.data } : {}),
    objects: capture.objects.map(o => ({ id: o.id, label: o.category, category: o.category, position: { x: o.transform[12], y: o.transform[13] - floorY, z: o.transform[14] }, size: { x: o.width, y: o.height, z: Math.max(0.02, o.depth) }, rotation: Math.atan2(-o.transform[2], o.transform[0]), evidence: o.confidence === 'low' ? 'estimated' : 'measured', ...(o.attributes ? { attributes: o.attributes } : {}) })),
    measurementQuality: { version: 'geometry-qa-v1', status: 'needs-rescan', targetMeters: .02, reasons: ['Raw depth repeat-view checks have not run. Process the raw bundle before relying on 2 cm measurements.'], applied: [] },
    warnings: warnings.slice(0, 50), originalUsdzPath: capture.usdzPath,
  });
}

// Experimental, deliberately conservative: fit vertical boundary lines and intersect their inward half-planes.
// Only supports convex layouts. Guided corners remain the required fallback for incomplete/complex captures.
export function inferConvexFloor(points: { x: number; y: number; z: number }[], floorY: number): Point[] {
  let cloud = points.filter(p => p.y > floorY + 0.35 && p.y < floorY + 2.3);
  if (cloud.length < 150) return [];
  const center = { x: cloud.reduce((s, p) => s + p.x, 0) / cloud.length, z: cloud.reduce((s, p) => s + p.z, 0) / cloud.length };
  const lines: { nx: number; nz: number; d: number }[] = [];
  let seed = 1234567;
  const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
  for (let pass = 0; pass < 10 && cloud.length >= 60; pass++) {
    let best: typeof lines[number] | undefined, bestCount = 0;
    for (let i = 0; i < 180; i++) {
      const a = cloud[Math.floor(random() * cloud.length)], b = cloud[Math.floor(random() * cloud.length)];
      const len = distance(a, b); if (len < 0.8) continue;
      let nx = (b.z - a.z) / len, nz = -(b.x - a.x) / len, d = nx * a.x + nz * a.z;
      if (nx * center.x + nz * center.z > d) { nx = -nx; nz = -nz; d = -d; }
      const inliers = cloud.filter(p => Math.abs(nx * p.x + nz * p.z - d) < 0.07);
      const outside = cloud.filter(p => nx * p.x + nz * p.z > d + 0.15).length;
      if (outside > cloud.length * 0.08) continue;
      if (inliers.length > bestCount) { bestCount = inliers.length; best = { nx, nz, d }; }
    }
    if (!best || bestCount < 45) break;
    const line = best;
    if (!lines.some(l => Math.abs(l.nx * line.nz - l.nz * line.nx) < 0.08 && l.nx * line.nx + l.nz * line.nz > 0.9)) lines.push(line);
    cloud = cloud.filter(p => Math.abs(line.nx * p.x + line.nz * p.z - line.d) >= 0.09);
  }
  if (lines.length < 3 || lines.length > 8) return [];
  lines.sort((a, b) => Math.atan2(a.nz, a.nx) - Math.atan2(b.nz, b.nx));
  const floor: Point[] = [];
  for (let i = 0; i < lines.length; i++) {
    const a = lines[i], b = lines[(i + 1) % lines.length];
    const det = a.nx * b.nz - a.nz * b.nx;
    if (Math.abs(det) < 0.1) return [];
    const p = { x: (a.d * b.nz - a.nz * b.d) / det, z: (a.nx * b.d - a.d * b.nx) / det };
    if (distance(p, center) > 15 || lines.some(l => l.nx * p.x + l.nz * p.z > l.d + 0.2)) return [];
    floor.push(p);
  }
  return polygonErrors(floor).length ? [] : floor;
}
