import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { bounds, describeElement, signedArea, wallLength, type Point, type Room } from '../domain/room';
import { buildRoomGroup, disposeGroup } from '../scene/buildRoom';

export type ViewMode = 'orbit' | 'top' | 'inside';
export type Display = 'model' | 'lidar';

// The room as the LiDAR saw it: coloured points from scripts/scan_processing/cloud.py, split by the group each point
// belongs to (a wall, the ceiling, or anything else) so the view can fade near walls and hide the ceiling.
type Cloud = { voxel: number; parts: { group: number; positions: Float32Array; colours: Uint8Array }[] };
const clouds = new Map<string, Promise<Cloud | null>>();
function loadCloud(roomId: string) {
  if (!clouds.has(roomId)) clouds.set(roomId, fetch(`${import.meta.env.BASE_URL}clouds/${roomId}.bin`).then(async response => {
    if (!response.ok) return null;
    const buffer = await response.arrayBuffer();
    // A missing file can come back as the app's HTML page, so check the header before trusting the bytes.
    if (buffer.byteLength < 16 || new TextDecoder().decode(buffer.slice(0, 4)) !== 'RFPC') return null;
    const header = new DataView(buffer), count = header.getUint32(8, true), voxel = header.getFloat32(12, true);
    const positions = new Float32Array(buffer, 16, count * 3), colours = new Uint8Array(buffer, 16 + count * 12, count * 3), groups = new Uint8Array(buffer, 16 + count * 15, count);
    const sizes = new Map<number, number>(); for (const g of groups) sizes.set(g, (sizes.get(g) ?? 0) + 1);
    const parts = [...sizes].map(([group, size]) => ({ group, positions: new Float32Array(size * 3), colours: new Uint8Array(size * 3), filled: 0 }));
    const byGroup = new Map(parts.map(part => [part.group, part]));
    for (let i = 0; i < count; i++) {
      const part = byGroup.get(groups[i])!, at = part.filled++ * 3;
      part.positions.set(positions.subarray(i * 3, i * 3 + 3), at); part.colours.set(colours.subarray(i * 3, i * 3 + 3), at);
    }
    return { voxel, parts };
  }).catch(() => null));
  return clouds.get(roomId)!;
}
type Props = { room: Room; furniture: boolean; cutaway: boolean; ceiling: boolean; selected: string; onSelect: (id: string) => void; resetKey: number; view: ViewMode; viewKey: number; display: Display; focusKey: number; labels: boolean; measure: boolean; fixtures: boolean };
const EYE = 1.55; // standing eye height for the walk-in view, meters

// A small text tag that always faces the camera. `height` is its size in meters, or with `fixed` a fraction of the view
// so it stays readable at any zoom.
function tag(text: string, height: number, tone = '#26372c', fixed = false) {
  const canvas = document.createElement('canvas'), context = canvas.getContext('2d')!;
  context.font = '600 56px system-ui, sans-serif';
  canvas.width = Math.ceil(context.measureText(text).width) + 48; canvas.height = 88;
  context.font = '600 56px system-ui, sans-serif'; context.fillStyle = '#ffffffee'; context.beginPath(); context.roundRect(0, 0, canvas.width, canvas.height, 20); context.fill();
  context.fillStyle = tone; context.textBaseline = 'middle'; context.fillText(text, 24, canvas.height / 2 + 3);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true, sizeAttenuation: !fixed }));
  sprite.scale.set(height * canvas.width / canvas.height, height, 1); sprite.renderOrder = 10;
  return { sprite, dispose: () => { texture.dispose(); sprite.material.dispose(); } };
}

export default function RoomViewer({ room, furniture, cutaway, ceiling, selected, onSelect, resetKey, view, viewKey, display, focusKey, labels, measure, fixtures }: Props) {
  const host = useRef<HTMLDivElement>(null), tip = useRef<HTMLDivElement>(null), note = useRef<HTMLDivElement>(null);
  const pose = useRef<{ roomId: string; resetKey: number; position: THREE.Vector3; target: THREE.Vector3; fov: number } | null>(null);
  const mode = useRef<ViewMode | 'free'>(view);
  const api = useRef<{ go: (next: ViewMode, animate: boolean) => void; focus: () => void } | null>(null);
  const onSelectRef = useRef(onSelect); onSelectRef.current = onSelect;
  const [error, setError] = useState('');
  useEffect(() => {
    if (!host.current) return;
    const container = host.current, tooltip = tip.current!, banner = note.current!;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); } catch { setError('3D is unavailable on this device. Use the floorplan view.'); return; }
    setError('');
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor('#f3f3ed', 1); renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.3;
    renderer.domElement.setAttribute('aria-label', 'Interactive 3D room. Drag to orbit, scroll to zoom, click a wall or object to select.');
    renderer.domElement.setAttribute('role', 'img'); container.appendChild(renderer.domElement);
    const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(38, 1, 0.01, 1000);
    const b = bounds(room), cx = (b.minX + b.maxX) / 2, cz = (b.minZ + b.maxZ) / 2, span = Math.max(b.width, b.depth, 3);
    const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
    const cleanup: (() => void)[] = [];

    // Camera limits per view. 'free' is used after flying to something, so the camera may also look up at a ceiling.
    const limits = (next: ViewMode | 'free') => {
      const walking = next === 'inside', low = next === 'orbit' || next === 'top';
      controls.minPolarAngle = low ? 0 : 0.03; controls.maxPolarAngle = Math.PI * (low ? 0.48 : 0.97);
      controls.minDistance = walking ? 0.3 : low ? 1 : 0.3; controls.maxDistance = walking ? 0.3 : span * 6;
      controls.enableZoom = !walking; controls.enablePan = !walking; controls.rotateSpeed = walking ? -0.5 : 1;
      if (!walking && camera.fov !== 38) { camera.fov = 38; camera.updateProjectionMatrix(); }
    };
    // A standing point inside the room: the floor's centroid when there is one, since the bounding box centre can fall outside an L-shaped room.
    const inside = () => {
      const f = room.floor, area = signedArea(f);
      if (f.length < 3 || Math.abs(area) < 0.01) return new THREE.Vector3(cx, EYE, cz);
      let x = 0, z = 0;
      f.forEach((p, i) => { const q = f[(i + 1) % f.length], cross = p.x * q.z - q.x * p.z; x += (p.x + q.x) * cross; z += (p.z + q.z) * cross; });
      return new THREE.Vector3(x / (6 * area), EYE, z / (6 * area));
    };
    // Walking stays on the floor outline, a little back from the walls, so the camera cannot pass through them.
    const onFloor = (x: number, z: number) => {
      const f = room.floor;
      if (f.length < 3) return x > b.minX && x < b.maxX && z > b.minZ && z < b.maxZ;
      let within = false;
      for (let i = 0, j = f.length - 1; i < f.length; j = i++) {
        if ((f[i].z > z) !== (f[j].z > z) && x < (f[j].x - f[i].x) * (z - f[i].z) / (f[j].z - f[i].z) + f[i].x) within = !within;
        const ex = f[j].x - f[i].x, ez = f[j].z - f[i].z, t = Math.max(0, Math.min(1, ((x - f[i].x) * ex + (z - f[i].z) * ez) / (ex * ex + ez * ez)));
        if (Math.hypot(x - f[i].x - ex * t, z - f[i].z - ez * t) < 0.2) return false;
      }
      return within;
    };
    // What a measurement can snap to: the corners and edges of walls, openings, fixtures, the floor outline and furniture boxes.
    const corners: THREE.Vector3[] = [], edges: [THREE.Vector3, THREE.Vector3][] = [];
    const along = (wall: Room['walls'][number], offset: number, up: number) => { const length = wallLength(wall); return new THREE.Vector3(wall.start.x + (wall.end.x - wall.start.x) / length * offset, wall.baseY + up, wall.start.z + (wall.end.z - wall.start.z) / length * offset); };
    const rectangle = (wall: Room['walls'][number], from: number, to: number, bottom: number, top: number) => {
      const c = [along(wall, from, bottom), along(wall, to, bottom), along(wall, to, top), along(wall, from, top)];
      corners.push(...c); c.forEach((point, i) => edges.push([point, c[(i + 1) % 4]]));
    };
    for (const wall of room.walls) {
      rectangle(wall, 0, wallLength(wall), 0, wall.height);
      for (const hole of [...room.openings, ...(fixtures ? room.fixtures ?? [] : [])].filter(o => o.wallId === wall.id)) rectangle(wall, hole.offset, hole.offset + hole.width, hole.sill, hole.sill + hole.height);
    }
    room.floor.forEach((p: Point, i) => { const q = room.floor[(i + 1) % room.floor.length]; corners.push(new THREE.Vector3(p.x, 0, p.z)); edges.push([new THREE.Vector3(p.x, 0, p.z), new THREE.Vector3(q.x, 0, q.z)]); });
    if (furniture) for (const o of room.objects) {
      const cos = Math.cos(o.rotation), sin = Math.sin(o.rotation), box: THREE.Vector3[] = [];
      for (let i = 0; i < 8; i++) { const lx = (i & 1 ? 1 : -1) * o.size.x / 2, ly = (i & 2 ? 1 : -1) * o.size.y / 2, lz = (i & 4 ? 1 : -1) * o.size.z / 2; box.push(new THREE.Vector3(o.position.x + lx * cos + lz * sin, o.position.y + ly, o.position.z - lx * sin + lz * cos)); }
      corners.push(...box);
      for (let i = 0; i < 8; i++) for (const bit of [1, 2, 4]) if (!(i & bit)) edges.push([box[i], box[i | bit]]); // the 12 edges join corners that differ on one axis
    }
    const preset = (next: ViewMode): [THREE.Vector3, THREE.Vector3] => {
      if (next === 'top') return [new THREE.Vector3(cx, span * 2.4, cz + 0.01), new THREE.Vector3(cx, 0, cz)];
      if (next === 'inside') {
        const eye = inside(), longest = [...room.walls].sort((p, q) => wallLength(q) - wallLength(p))[0];
        const ahead = new THREE.Vector3((longest.start.x + longest.end.x) / 2 - eye.x, 0, (longest.start.z + longest.end.z) / 2 - eye.z).normalize();
        return [eye, eye.clone().addScaledVector(ahead, 0.3)];
      }
      return [new THREE.Vector3(cx + span * 1.12, span, cz + span * 1.35), new THREE.Vector3(cx, 0.5, cz)];
    };
    let flight: { fromP: THREE.Vector3; fromT: THREE.Vector3; toP: THREE.Vector3; toT: THREE.Vector3; start: number; done: () => void } | null = null;
    const fly = (position: THREE.Vector3, target: THREE.Vector3, done: () => void) => { flight = { fromP: camera.position.clone(), fromT: controls.target.clone(), toP: position, toT: target, start: performance.now(), done }; };
    const go = (next: ViewMode, animate: boolean) => {
      mode.current = next;
      const [position, target] = preset(next);
      if (!animate) { camera.position.copy(position); controls.target.copy(target); limits(next); controls.update(); return; }
      fly(position, target, () => limits(next));
    };
    // Which side of a wall faces the room, as a unit vector in X/Z (same rule the room builder uses).
    const inward = (wall: Room['walls'][number]) => {
      const length = wallLength(wall), dx = (wall.end.x - wall.start.x) / length, dz = (wall.end.z - wall.start.z) / length;
      const side = room.source === 'roomplan' || (cx - wall.start.x) * -dz + (cz - wall.start.z) * dx >= 0 ? 1 : -1;
      return { dx, dz, nx: -dz * side, nz: dx * side, length };
    };
    // Fly the camera to face whatever is selected.
    const focus = () => {
      let target: THREE.Vector3 | undefined, position: THREE.Vector3 | undefined;
      const onWall = (wallId: string, along: number, up: number, reach: number) => {
        const wall = room.walls.find(w => w.id === wallId); if (!wall) return;
        const d = inward(wall);
        target = new THREE.Vector3(wall.start.x + d.dx * along, wall.baseY + up, wall.start.z + d.dz * along);
        position = target.clone().add(new THREE.Vector3(d.nx * reach, Math.max(0, EYE - target.y) * 0.4, d.nz * reach));
      };
      const wall = room.walls.find(w => w.id === selected), opening = room.openings.find(o => o.id === selected), fixture = room.fixtures?.find(f => f.id === selected);
      const object = room.objects.find(o => o.id === selected), item = room.ceiling?.items?.find(i => i.id === selected);
      if (wall) onWall(wall.id, wallLength(wall) / 2, wall.height / 2, Math.max(wallLength(wall), wall.height) * 1.15);
      else if (opening) onWall(opening.wallId, opening.offset + opening.width / 2, opening.sill + opening.height / 2, Math.max(opening.width, opening.height) * 1.6);
      else if (fixture) onWall(fixture.wallId, fixture.offset + fixture.width / 2, fixture.sill + fixture.height / 2, Math.max(1.1, fixture.width * 2.5));
      else if (object) {
        target = new THREE.Vector3(object.position.x, object.position.y, object.position.z);
        const away = camera.position.clone().sub(target).setY(0).normalize(), reach = Math.max(object.size.x, object.size.y, object.size.z) * 2.4 + 0.8;
        position = target.clone().addScaledVector(away, reach).setY(target.y + reach * 0.45);
      } else if (item) {
        // Stand a little to one side and look up at it.
        target = new THREE.Vector3(item.x, item.height, item.z);
        position = new THREE.Vector3(item.x + 0.5, Math.max(0.5, item.height - 1.9), item.z + 0.5);
      }
      if (!target || !position) return;
      mode.current = 'free'; limits('free'); fly(position, target, () => undefined);
    };
    api.current = { go, focus };

    if (pose.current?.roomId === room.id && pose.current.resetKey === resetKey) {
      camera.position.copy(pose.current.position); controls.target.copy(pose.current.target); camera.fov = pose.current.fov; limits(mode.current); controls.update();
    } else go(view, false);

    scene.add(new THREE.HemisphereLight('#ffffff', '#a6ad99', 2.8));
    const sun = new THREE.DirectionalLight('#fff9e9', 3); sun.position.set(cx - 3, 9, cz + 2); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.left = -span; sun.shadow.camera.right = span; sun.shadow.camera.top = span; sun.shadow.camera.bottom = -span; sun.shadow.bias = -0.001; sun.target.position.set(cx, 0, cz); scene.add(sun, sun.target);
    const group = buildRoomGroup(room, { furniture, selected, ceiling, markers: true, fixtures }); scene.add(group);
    const grid = new THREE.GridHelper(Math.ceil(span * 4), Math.ceil(span * 4) * 2, '#d7dcd2', '#e1e4db'); grid.position.set(cx, -0.04, cz); scene.add(grid);
    // LiDAR view: the drawn model steps aside and the scanned points take its place.
    const lidar = display === 'lidar', scanned = new THREE.Group(); scene.add(scanned);
    let alive = true, voxel = 0.015;
    cleanup.push(() => { alive = false; banner.hidden = true; scanned.traverse(o => { if (o instanceof THREE.Points) { o.geometry.dispose(); (o.material as THREE.Material).dispose(); } }); });
    if (lidar) {
      group.visible = false; banner.hidden = false; banner.textContent = 'Loading the LiDAR scan…';
      void loadCloud(room.id).then(cloud => {
        if (!alive) return;
        if (!cloud) { group.visible = true; banner.textContent = 'No LiDAR scan is saved for this room, so the drawn model is shown.'; return; }
        banner.hidden = true; voxel = cloud.voxel;
        for (const part of cloud.parts) {
          if (part.group === 255 && !ceiling) continue;
          const geometry = new THREE.BufferGeometry();
          geometry.setAttribute('position', new THREE.BufferAttribute(part.positions, 3)); geometry.setAttribute('color', new THREE.BufferAttribute(part.colours, 3, true));
          const points = new THREE.Points(geometry, new THREE.PointsMaterial({ size: cloud.voxel * 1.7, vertexColors: true, sizeAttenuation: true, transparent: true }));
          points.userData.wallNumber = part.group >= 1 && part.group <= 250 ? part.group : 0; scanned.add(points);
        }
      });
    }
    const overlay = new THREE.Group(); scene.add(overlay); // tags and measurements, drawn over everything and never faded by the cutaway
    if (labels) for (const wall of room.walls) {
      const label = tag(`${wallLength(wall).toFixed(2)} m`, 0.17); cleanup.push(label.dispose);
      label.sprite.position.set((wall.start.x + wall.end.x) / 2, wall.baseY + wall.height + 0.16, (wall.start.z + wall.end.z) / 2); overlay.add(label.sprite);
    }

    const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
    // The first solid thing under the pointer; faded cutaway walls and the see-through ceiling lid do not count.
    const pick = (clientX: number, clientY: number) => {
      const rect = renderer.domElement.getBoundingClientRect(); pointer.set((clientX - rect.left) / rect.width * 2 - 1, -(clientY - rect.top) / rect.height * 2 + 1); raycaster.setFromCamera(pointer, camera);
      if (lidar && !group.visible) {
        raycaster.params.Points = { threshold: voxel * 1.2 };
        return raycaster.intersectObject(scanned, true).find(h => h.object instanceof THREE.Points && (h.object.material as THREE.Material).opacity > 0.5);
      }
      return raycaster.intersectObject(group, true).find(h => h.object instanceof THREE.Mesh && !Array.isArray(h.object.material) && h.object.material.opacity > 0.5);
    };
    const owner = (object: THREE.Object3D | null): string => { for (let o = object; o; o = o.parent) if (o.userData.elementId) return o.userData.elementId; return ''; };

    // Measuring: two clicks make a line with its length; a third click starts again. Before each click a preview dot
    // shows exactly where the point will land, and turns green when it has locked onto a corner or an edge.
    const disc = (fill: string) => {
      const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d')!;
      g.beginPath(); g.arc(32, 32, 26, 0, Math.PI * 2); g.fillStyle = '#ffffff'; g.fill(); g.beginPath(); g.arc(32, 32, 17, 0, Math.PI * 2); g.fillStyle = fill; g.fill();
      const texture = new THREE.CanvasTexture(c); texture.colorSpace = THREE.SRGBColorSpace;
      // Not size-attenuated: the dot stays the same few pixels wide however near the camera is, so it marks the point precisely.
      const material = new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true, sizeAttenuation: false });
      cleanup.push(() => { texture.dispose(); material.dispose(); });
      return material;
    };
    const redDot = disc('#d2452d'), greenDot = disc('#2f8f46');
    const dotAt = (point: THREE.Vector3, material: THREE.SpriteMaterial) => { const sprite = new THREE.Sprite(material); sprite.scale.setScalar(0.024); sprite.position.copy(point); sprite.renderOrder = 11; return sprite; };
    const marks: THREE.Vector3[] = [], measured = new THREE.Group(); overlay.add(measured);
    const preview = dotAt(new THREE.Vector3(), redDot); preview.visible = false; overlay.add(preview);
    const band = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]), new THREE.LineBasicMaterial({ color: '#d2452d', depthTest: false, transparent: true, opacity: 0.6 }));
    band.visible = false; band.renderOrder = 9; band.frustumCulled = false; overlay.add(band); cleanup.push(() => { band.geometry.dispose(); (band.material as THREE.Material).dispose(); });
    const clearMarks = () => { for (const child of [...measured.children]) { measured.remove(child); if (child instanceof THREE.Line) { child.geometry.dispose(); (child.material as THREE.Material).dispose(); } } };
    // Where a click at this screen position would put a point. Snapping is judged in screen pixels, so it feels the same at any zoom,
    // and only to corners and edges close to the surface under the pointer, so a corner hidden behind that surface cannot grab it.
    const resolve = (clientX: number, clientY: number): { point: THREE.Vector3; snap: '' | 'corner' | 'edge' } | null => {
      const hit = pick(clientX, clientY), surface = hit?.point.clone() ?? raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
      if (!surface) return null;
      const rect = canvas.getBoundingClientRect(), px = clientX - rect.left, py = clientY - rect.top, projected = new THREE.Vector3();
      const pixels = (point: THREE.Vector3) => { projected.copy(point).project(camera); return projected.z > 1 ? Infinity : Math.hypot((projected.x + 1) / 2 * rect.width - px, (1 - projected.y) / 2 * rect.height - py); };
      if (lidar && !group.visible) return { point: surface, snap: '' }; // scanned points have no corners to lock onto
      let best: THREE.Vector3 | null = null, reach = 14;
      for (const corner of corners) { if (corner.distanceTo(surface) > 0.4) continue; const d = pixels(corner); if (d < reach) { reach = d; best = corner; } }
      if (best) return { point: best.clone(), snap: 'corner' };
      const onEdge = new THREE.Vector3(), segment = new THREE.Line3(); reach = 9;
      for (const [from, to] of edges) {
        segment.set(from, to).closestPointToPoint(surface, true, onEdge);
        if (onEdge.distanceTo(surface) > 0.08) continue;
        const d = pixels(onEdge); if (d < reach) { reach = d; best = onEdge.clone(); }
      }
      return best ? { point: best, snap: 'edge' } : { point: surface, snap: '' };
    };
    let lengthTag: ReturnType<typeof tag> | null = null;
    cleanup.push(() => lengthTag?.dispose());
    const redraw = () => {
      clearMarks(); lengthTag?.dispose(); lengthTag = null;
      for (const point of marks) measured.add(dotAt(point, redDot));
      if (marks.length < 2) return;
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(marks), new THREE.LineBasicMaterial({ color: '#d2452d', depthTest: false })); line.renderOrder = 9; measured.add(line);
      lengthTag = tag(`${marks[0].distanceTo(marks[1]).toFixed(2)} m`, 0.04, '#b3331d', true);
      lengthTag.sprite.position.copy(marks[0]).lerp(marks[1], 0.5); lengthTag.sprite.center.set(0.5, -0.35); measured.add(lengthTag.sprite);
    };
    const mark = (point: THREE.Vector3) => { if (marks.length === 2) marks.length = 0; marks.push(point); redraw(); };
    // An end of the measurement under the pointer can be picked up and moved; -1 when the pointer is not on one.
    const grip = (clientX: number, clientY: number) => {
      const rect = canvas.getBoundingClientRect(), projected = new THREE.Vector3();
      let found = -1, reach = 16;
      marks.forEach((point, index) => {
        projected.copy(point).project(camera); if (projected.z > 1) return;
        const d = Math.hypot((projected.x + 1) / 2 * rect.width - (clientX - rect.left), (1 - projected.y) / 2 * rect.height - (clientY - rect.top));
        if (d < reach) { reach = d; found = index; }
      });
      return found;
    };
    let dragging = -1, dragSnap: '' | 'corner' | 'edge' = '';

    let start = { x: 0, y: 0 }, hover: { x: number; y: number } | null = null;
    const down = (e: PointerEvent) => {
      start = { x: e.clientX, y: e.clientY };
      if (!measure) return;
      dragging = grip(e.clientX, e.clientY);
      if (dragging < 0) return;
      // Holding an end: the camera must stay still while it is moved.
      e.stopImmediatePropagation(); controls.enabled = false; canvas.setPointerCapture(e.pointerId);
    };
    const up = (e: PointerEvent) => {
      if (dragging >= 0) { dragging = -1; dragSnap = ''; controls.enabled = true; if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId); return; }
      if (Math.hypot(start.x - e.clientX, start.y - e.clientY) > 5) return;
      const hit = pick(e.clientX, e.clientY);
      if (measure) { const placed = resolve(e.clientX, e.clientY); if (placed) mark(placed.point); return; }
      const id = hit ? owner(hit.object) : ''; if (id) onSelectRef.current(id);
    };
    // Double-click: walk to that spot when inside the room, otherwise move in on that point.
    const dive = (e: MouseEvent) => {
      const hit = pick(e.clientX, e.clientY); if (!hit || measure) return;
      if (mode.current === 'inside') {
        if (!onFloor(hit.point.x, hit.point.z)) return;
        const ahead = controls.target.clone().sub(camera.position).setY(0).normalize(), eye = new THREE.Vector3(hit.point.x, EYE, hit.point.z);
        fly(eye, eye.clone().addScaledVector(ahead, 0.3).setY(controls.target.y - camera.position.y + EYE), () => limits('inside'));
      } else {
        const away = camera.position.clone().sub(hit.point).normalize();
        mode.current = 'free'; limits('free'); fly(hit.point.clone().addScaledVector(away, 1.8), hit.point.clone(), () => undefined);
      }
    };
    const move = (e: PointerEvent) => {
      hover = e.pointerType === 'touch' ? null : { x: e.clientX, y: e.clientY };
      if (dragging < 0) return;
      const placed = resolve(e.clientX, e.clientY); if (!placed) return;
      marks[dragging] = placed.point; dragSnap = placed.snap; redraw();
    };
    const leave = () => { hover = null; tooltip.hidden = true; };
    // Walking: arrow keys or WASD step through the room at eye height.
    const walk = (e: KeyboardEvent) => {
      if (mode.current !== 'inside' || flight || (e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName))) return;
      const key = e.key.toLowerCase(), forward = key === 'arrowup' || key === 'w' ? 1 : key === 'arrowdown' || key === 's' ? -1 : 0, sideways = key === 'arrowright' || key === 'd' ? 1 : key === 'arrowleft' || key === 'a' ? -1 : 0;
      if (!forward && !sideways) return;
      e.preventDefault();
      const ahead = controls.target.clone().sub(camera.position).setY(0).normalize(), right = new THREE.Vector3(-ahead.z, 0, ahead.x);
      const step = ahead.multiplyScalar(forward * 0.25).addScaledVector(right, sideways * 0.25), next = camera.position.clone().add(step);
      if (!onFloor(next.x, next.z)) return;
      camera.position.add(step); controls.target.add(step);
    };
    // In the walk-in view the wheel narrows or widens the lens instead of moving the camera.
    const lens = (e: WheelEvent) => { if (mode.current !== 'inside') return; e.preventDefault(); camera.fov = Math.min(80, Math.max(25, camera.fov + Math.sign(e.deltaY) * 3)); camera.updateProjectionMatrix(); };
    const canvas = renderer.domElement;
    canvas.addEventListener('pointerdown', down, true); canvas.addEventListener('pointerup', up); canvas.addEventListener('dblclick', dive); canvas.addEventListener('pointermove', move); canvas.addEventListener('pointerleave', leave); canvas.addEventListener('wheel', lens, { passive: false }); window.addEventListener('keydown', walk);
    const resize = () => { const width = container.clientWidth, height = container.clientHeight; renderer.setSize(width, height); camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix(); };
    const observer = new ResizeObserver(resize); observer.observe(container); resize();
    let frame = 0, hoverAt = 0;
    const draw = () => {
      frame = requestAnimationFrame(draw);
      if (flight) {
        const t = Math.min(1, (performance.now() - flight.start) / 550), k = t * t * (3 - 2 * t);
        camera.position.lerpVectors(flight.fromP, flight.toP, k); controls.target.lerpVectors(flight.fromT, flight.toT, k); camera.lookAt(controls.target);
        if (t >= 1) { const done = flight.done; flight = null; done(); controls.update(); }
      } else controls.update();
      for (const child of group.children) {
        const wall = room.walls.find(w => w.id === child.userData.wallId); if (!wall) continue;
        const mx = (wall.start.x + wall.end.x) / 2, mz = (wall.start.z + wall.end.z) / 2;
        const near = (camera.position.x - mx) * (mx - cx) + (camera.position.z - mz) * (mz - cz) > 0;
        child.traverse(o => { if (!(o instanceof THREE.Mesh)) return; const mats = Array.isArray(o.material) ? o.material : [o.material]; for (const m of mats) {
          if (m.userData.originalOpacity === undefined) m.userData.originalOpacity = m.opacity;
          m.opacity = cutaway && near ? (wall.id === selected ? 0.3 : 0.08) : m.userData.originalOpacity;
          m.transparent = m.opacity < 1; m.depthWrite = m.opacity > 0.5;
        } });
      }
      // Fade the scanned points of near walls the same way as the drawn walls.
      for (const points of scanned.children) {
        const number = points.userData.wallNumber as number, wall = number ? room.walls[number - 1] : undefined, material = (points as THREE.Points).material as THREE.PointsMaterial;
        const near = wall ? (camera.position.x - (wall.start.x + wall.end.x) / 2) * ((wall.start.x + wall.end.x) / 2 - cx) + (camera.position.z - (wall.start.z + wall.end.z) / 2) * ((wall.start.z + wall.end.z) / 2 - cz) > 0 : false;
        material.opacity = cutaway && near ? 0.06 : 1; material.depthWrite = material.opacity > 0.5;
      }
      // Picking among hundreds of thousands of points is slow, so the scanned view checks the pointer about 12 times a second.
      const now = performance.now(), due = !lidar || now - hoverAt > 80;
      if (due) preview.visible = band.visible = false;
      if (hover && due) {
        hoverAt = now;
        let text = '';
        const held = measure ? (dragging >= 0 ? dragging : grip(hover.x, hover.y)) : -1;
        if (held >= 0) {
          // Over or holding an end: show where it sits and the length it gives, instead of offering a new point.
          preview.visible = true; preview.position.copy(marks[held]); preview.material = dragging >= 0 && !dragSnap ? redDot : greenDot;
          const length = marks.length === 2 ? `${marks[0].distanceTo(marks[1]).toFixed(2)} m` : '';
          text = dragging >= 0 ? `${length}${dragSnap ? ` · locked to ${dragSnap}` : ''}` || 'Moving the point' : `${length ? `${length} · ` : ''}drag to adjust`;
        } else if (measure) {
          const placed = resolve(hover.x, hover.y);
          if (placed) {
            preview.visible = true; preview.position.copy(placed.point); preview.material = placed.snap ? greenDot : redDot;
            const lock = placed.snap ? ` · locked to ${placed.snap}` : '';
            if (marks.length === 1) {
              band.visible = true; band.geometry.setFromPoints([marks[0], placed.point]);
              text = `${marks[0].distanceTo(placed.point).toFixed(2)} m${lock} · click to finish`;
            } else text = `Click the first point${lock}`;
          }
        } else {
          const hit = pick(hover.x, hover.y);
          text = !hit ? '' : hit.object instanceof THREE.Points ? `Scanned surface · ${hit.point.y.toFixed(2)} m above the floor` : describeElement(room, owner(hit.object));
        }
        const rect = canvas.getBoundingClientRect();
        tooltip.hidden = !text; tooltip.textContent = text; // Keep the tip inside the view: flip it to the other side of the pointer near the right and bottom edges.
        const x = hover.x - rect.left, y = hover.y - rect.top, wide = tooltip.offsetWidth, tall = tooltip.offsetHeight;
        tooltip.style.left = `${x + 14 + wide > rect.width ? Math.max(4, x - wide - 10) : x + 14}px`; tooltip.style.top = `${y + 14 + tall > rect.height ? Math.max(4, y - tall - 10) : y + 14}px`;
        canvas.style.cursor = held >= 0 ? (dragging >= 0 ? 'grabbing' : 'grab') : measure ? 'crosshair' : text ? 'pointer' : '';
      }
      renderer.render(scene, camera);
    }; draw();
    return () => {
      pose.current = { roomId: room.id, resetKey, position: camera.position.clone(), target: controls.target.clone(), fov: camera.fov };
      api.current = null; cancelAnimationFrame(frame); observer.disconnect(); controls.dispose(); window.removeEventListener('keydown', walk);
      canvas.removeEventListener('pointerdown', down, true); canvas.removeEventListener('pointerup', up); canvas.removeEventListener('dblclick', dive); canvas.removeEventListener('pointermove', move); canvas.removeEventListener('pointerleave', leave); canvas.removeEventListener('wheel', lens);
      clearMarks(); cleanup.forEach(dispose => dispose()); disposeGroup(scene); renderer.dispose(); container.removeChild(canvas); tooltip.hidden = true;
    };
  }, [room, furniture, cutaway, ceiling, selected, resetKey, labels, measure, fixtures, display]);
  // Changing the view or asking to focus moves the existing camera; it must not rebuild the scene.
  const mounted = useRef(false);
  useEffect(() => { if (mounted.current) api.current?.go(view, true); mounted.current = true; }, [view, viewKey]);
  useEffect(() => { if (focusKey) api.current?.focus(); }, [focusKey]);
  return <div ref={host} className="three-view">{error ? <p className="viewer-error" role="alert">{error}</p> : null}<div ref={tip} className="viewer-tip" hidden /><div ref={note} className="viewer-note" hidden /></div>;
}
