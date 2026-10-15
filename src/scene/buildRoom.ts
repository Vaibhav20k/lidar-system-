import * as THREE from 'three';
import { wallLength, type Room } from '../domain/room';

export function buildRoomGroup(room: Room, options: { furniture?: boolean; selected?: string; ceiling?: boolean; markers?: boolean; fixtures?: boolean } = {}) {
  const root = new THREE.Group(); root.name = room.name;
  const material = (color: string, opacity = 1) => new THREE.MeshStandardMaterial({ color, roughness: 0.82, metalness: 0, side: THREE.DoubleSide, transparent: opacity < 1, opacity });
  const box = (size: [number, number, number], position: [number, number, number], mat: THREE.Material, parent: THREE.Group, id: string) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), mat); mesh.position.set(...position); mesh.castShadow = true; mesh.receiveShadow = true; mesh.userData.elementId = id; parent.add(mesh); return mesh;
  };
  if (room.floor.length) {
    const shape = new THREE.Shape(room.floor.map(p => new THREE.Vector2(p.x, -p.z)));
    const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shape), material('#ddd6c7'));
    mesh.rotation.x = -Math.PI / 2; mesh.position.y = -0.008; mesh.receiveShadow = true; mesh.name = 'Floor'; root.add(mesh);
    // Subtle boards are display styling, not captured surface textures.
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({ color: '#b5ad9d' }));
    edges.rotation.copy(mesh.rotation); edges.position.y = 0; root.add(edges);
    if (options.ceiling) {
      // A see-through lid so the room stays visible. Without measured ceiling data it sits at wall-top height.
      const lid = new THREE.Mesh(new THREE.ShapeGeometry(shape), material('#e6e3d8', 0.45));
      // The lid sits at the highest measured level; everything lower is a solid block hanging from it.
      const regions = room.ceiling?.regions ?? [], main = room.ceiling?.height ?? Math.max(...room.walls.map(w => w.baseY + w.height));
      const top = Math.max(main, ...regions.map(r => r.height));
      lid.rotation.x = -Math.PI / 2; lid.position.y = top; lid.name = 'Ceiling'; root.add(lid);
      const hang = (outline: THREE.Shape, bottom: number) => {
        if (bottom >= top - 0.02) return;
        const block = new THREE.Mesh(new THREE.ExtrudeGeometry(outline, { depth: top - bottom, bevelEnabled: false }), material('#d9d4c6', 0.9));
        block.rotation.x = -Math.PI / 2; block.position.y = bottom; block.castShadow = true; block.name = 'Ceiling block'; root.add(block);
      };
      const flat = (points: { x: number; z: number }[]) => points.map(p => new THREE.Vector2(p.x, -p.z));
      for (const region of regions) {
        const outline = new THREE.Shape(flat(region.points));
        for (const hole of region.holes ?? []) outline.holes.push(new THREE.Path(flat(hole)));
        hang(outline, region.height);
      }
      // Where recesses rise above the main ceiling, the main ceiling itself hangs below the lid around them.
      const rest = new THREE.Shape(flat(room.floor));
      for (const region of regions) rest.holes.push(new THREE.Path(flat(region.points)));
      hang(rest, main);
      // Lights hang just below the surface they are fixed to; holes are dark patches flush with it.
      for (const item of options.fixtures === false ? [] : room.ceiling?.items ?? []) {
        const holder = new THREE.Group(); holder.position.set(item.x, item.height, item.z); holder.rotation.y = -item.rotation; holder.userData.elementId = item.id;
        const tone = options.selected === item.id ? '#9cc070' : item.kind === 'light' ? '#ffe9a8' : item.kind === 'hole' ? '#26272b' : '#9a9d98';
        if (item.kind === 'light' && Math.max(item.width, item.depth) < 0.3) {
          const lamp = new THREE.Mesh(new THREE.CylinderGeometry(Math.max(item.width, item.depth) / 2, Math.max(item.width, item.depth) / 2, 0.1, 20), material(tone));
          lamp.position.y = -0.05; lamp.userData.elementId = item.id; holder.add(lamp);
        } else box([item.width, item.kind === 'hole' ? 0.012 : 0.05, item.depth], [0, item.kind === 'hole' ? -0.006 : -0.025, 0], material(tone), holder, item.id);
        if (options.markers) box([item.width + 0.08, 0.006, item.depth + 0.08], [0, -0.003, 0], material(item.review ? '#d9a521' : '#e07b39'), holder, item.id);
        root.add(holder);
      }
    }
  }
  const center = { x: room.walls.reduce((sum, w) => sum + w.start.x + w.end.x, 0) / (room.walls.length * 2), z: room.walls.reduce((sum, w) => sum + w.start.z + w.end.z, 0) / (room.walls.length * 2) };
  for (const wall of room.walls) {
    const group = new THREE.Group(); group.name = `Wall ${wall.id}`; group.userData.wallId = wall.id;
    const length = wallLength(wall), dx = wall.end.x - wall.start.x, dz = wall.end.z - wall.start.z;
    group.position.set(wall.start.x, wall.baseY, wall.start.z); group.rotation.y = -Math.atan2(dz, dx);
    const openings = room.openings.filter(o => o.wallId === wall.id);
    const cuts = [...new Set([0, length, ...openings.flatMap(o => [o.offset, o.offset + o.width])])].sort((a, b) => a - b);
    const wallMat = material(options.selected === wall.id ? '#c7d9a7' : '#eeede6');
    for (let i = 0; i < cuts.length - 1; i++) {
      const start = cuts[i], end = cuts[i + 1], mid = (start + end) / 2;
      const gaps = openings.filter(o => mid > o.offset && mid < o.offset + o.width).map(o => [o.sill, o.sill + o.height]).sort((a, b) => a[0] - b[0]);
      let y = 0;
      for (const [bottom, top] of [...gaps, [wall.height, wall.height]]) {
        if (bottom > y + 0.001) box([end - start, bottom - y, wall.thickness], [mid, (bottom + y) / 2, 0], wallMat, group, wall.id);
        y = top;
      }
    }
    for (const opening of openings) {
      const mat = material(options.selected === opening.id ? '#88bdb0' : opening.kind === 'window' ? '#b8d8d7' : '#cfb994', opening.kind === 'window' ? 0.38 : 0.65);
      box([opening.width, opening.height, 0.025], [opening.offset + opening.width / 2, opening.sill + opening.height / 2, 0], mat, group, opening.id);
      const trim = material('#a6aba0');
      box([opening.width + 0.06, 0.045, wall.thickness + 0.025], [opening.offset + opening.width / 2, opening.sill + opening.height, 0], trim, group, opening.id);
    }
    // Local +Z points to (-dz, dx) in the world; plates go on whichever wall face looks into the room.
    // Scanned walls already run so that this side faces the room; a centre test would pick the back of short jog walls in L-shaped rooms.
    const inside = room.source === 'roomplan' || (center.x - wall.start.x) * -dz + (center.z - wall.start.z) * dx >= 0 ? 1 : -1;
    for (const fixture of options.fixtures === false ? [] : (room.fixtures ?? []).filter(f => f.wallId === wall.id)) {
      const picked = options.selected === fixture.id, face = inside * (wall.thickness / 2 + 0.006);
      const at = (x: number, y: number, out: number): [number, number, number] => [fixture.offset + fixture.width / 2 + x, fixture.sill + fixture.height / 2 + y, face + inside * out];
      // The coloured surround is an on-screen marker so small plates stay findable; exports leave it out.
      const lit = material('#ffe9a8'), dark = material('#26272b');
      if (options.markers) box([fixture.width + 0.05, fixture.height + 0.05, 0.012], at(0, 0, 0), material(picked ? '#9cc070' : fixture.review ? '#d9a521' : '#e07b39'), group, fixture.id);
      if (fixture.kind === 'ac_unit') {
        // A split AC is a box standing out from the wall, with a darker outlet slot along the bottom.
        const proud = fixture.depth ?? 0.2;
        box([fixture.width, fixture.height, proud], at(0, 0, proud / 2), material('#f4f4f0'), group, fixture.id);
        box([fixture.width * 0.86, fixture.height * 0.12, 0.012], at(0, -fixture.height * 0.3, proud + 0.004), material('#b9bcc0'), group, fixture.id);
      } else if (fixture.kind === 'hole') box([fixture.width, fixture.height, 0.012], at(0, 0, 0.004), dark, group, fixture.id);
      else if (fixture.kind === 'light') box([fixture.width, fixture.height, 0.05], at(0, 0, 0.025), lit, group, fixture.id);
      else {
        box([fixture.width, fixture.height, 0.02], at(0, 0, 0.008), material('#f7f5ee'), group, fixture.id);
        const sockets = fixture.kind === 'socket', count = Math.max(1, Math.min(6, Math.round(fixture.width / 0.07)));
        if (fixture.kind !== 'other_electrical' && fixture.kind !== 'distribution_box' && fixture.kind !== 'junction_box') for (let i = 0; i < count; i++) box([fixture.width / count * 0.5, fixture.height * (sockets ? 0.35 : 0.5), 0.008], at((i + 0.5) * fixture.width / count - fixture.width / 2, 0, 0.02), material(sockets ? '#4a4f55' : '#d8d4c8'), group, fixture.id);
      }
    }
    root.add(group);
  }
  if (options.furniture !== false) for (const object of room.objects) {
    const group = new THREE.Group(); group.name = object.label; group.userData.elementId = object.id;
    group.position.set(object.position.x, object.position.y, object.position.z); group.rotation.y = object.rotation;
    const { x, y, z } = object.size;
    const color = options.selected === object.id ? '#b5d68a' : /sofa|bed/.test(object.category) ? '#c3c8b6' : /chair/.test(object.category) ? '#b29372' : '#947b60';
    const mat = material(color), id = object.id, a = object.attributes ?? {};
    // Scanned furniture is only a labelled bounding box, so these are stand-in shapes sized to that box.
    // Local -Z is the back: in RoomPlan captures that side sits against the wall.
    const trim = material(options.selected === id ? '#9cc070' : '#6b5a48'), bottom = -y / 2, front = z / 2 + 0.004;
    if (/sofa/.test(object.category)) {
      box([x, y * 0.5, z], [0, -y * 0.17, 0], mat, group, id);
      box([x, y * 0.45, z * 0.18], [0, y * 0.22, -z * 0.4], mat, group, id);
      for (const side of [-1, 1]) box([x * 0.08, y * 0.75, z], [side * x * 0.46, 0, 0], mat, group, id);
    } else if (/table/.test(object.category)) {
      box([x, y * 0.16, z], [0, y * 0.42, 0], mat, group, id);
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) box([0.045, y * 0.84, 0.045], [sx * x * 0.4, -y * 0.08, sz * z * 0.4], mat, group, id);
    } else if (/chair/.test(object.category)) {
      const stool = a.ChairType === 'stool' || a.ChairBackType === 'missing';
      const pad = Math.min(0.06, y * 0.1), seat = stool ? y : Math.min(y * 0.55, 0.5), legs = seat - pad;
      box([x * 0.92, pad, z * 0.86], [0, bottom + seat - pad / 2, z * 0.05], mat, group, id);
      if (!stool) box([x * 0.92, y - seat, Math.min(0.06, z * 0.12)], [0, bottom + seat + (y - seat) / 2, -z * 0.45], mat, group, id);
      if (a.ChairLegType === 'star' || a.ChairType === 'swivel') {
        box([0.06, legs, 0.06], [0, bottom + legs / 2, 0], trim, group, id);
        const reach = Math.min(x, z) * 0.45;
        for (let i = 0; i < 5; i++) {
          const angle = i * Math.PI * 0.4;
          box([reach, 0.03, 0.045], [Math.cos(angle) * reach / 2, bottom + 0.015, -Math.sin(angle) * reach / 2], trim, group, id).rotation.y = angle;
        }
      } else for (const sx of [-1, 1]) for (const sz of [-1, 1]) box([0.04, legs, 0.04], [sx * x * 0.4, bottom + legs / 2, sz * z * 0.38], trim, group, id);
      if (!stool && a.ChairArmType === 'existing') for (const side of [-1, 1]) {
        const rise = Math.min(0.2, (y - seat) * 0.6);
        box([0.045, 0.035, z * 0.6], [side * x * 0.45, bottom + seat + rise, -z * 0.1], trim, group, id);
        box([0.045, rise, 0.045], [side * x * 0.45, bottom + seat + rise / 2, z * 0.15], trim, group, id);
      }
    } else if (/bed/.test(object.category)) {
      box([x, y * 0.45, z], [0, bottom + y * 0.225, 0], trim, group, id);
      box([x * 0.97, y * 0.4, z * 0.97], [0, bottom + y * 0.65, 0], mat, group, id);
      const linen = material('#eeeae0');
      for (const side of [-1, 1]) box([x * 0.36, y * 0.12, z * 0.16], [side * x * 0.22, bottom + y * 0.91, -z * 0.38], linen, group, id);
    } else if (/storage/.test(object.category)) {
      box([x, y, z], [0, 0, 0], mat, group, id);
      if (a.StorageType === 'shelf') for (const level of [0.25, 0.5, 0.75]) box([x * 0.94, 0.02, 0.01], [0, bottom + y * level, front], trim, group, id);
      else {
        if (x > 0.5) box([0.012, y * 0.92, 0.01], [0, 0, front], trim, group, id);
        for (const side of x > 0.5 ? [-1, 1] : [1]) box([0.015, Math.min(0.14, y * 0.2), 0.02], [side * (x > 0.5 ? 0.05 : x * 0.35), 0, front + 0.008], trim, group, id);
      }
    } else if (/refrigerator/.test(object.category)) {
      box([x, y, z], [0, 0, 0], mat, group, id);
      box([x * 0.96, 0.012, 0.01], [0, bottom + y * 0.62, front], trim, group, id);
      for (const level of [0.45, 0.78]) box([0.018, y * 0.16, 0.025], [x * 0.38, bottom + y * level, front + 0.01], trim, group, id);
    } else box([x, y, z], [0, 0, 0], mat, group, id);
    root.add(group);
  }
  root.userData = { schemaVersion: room.schemaVersion, source: room.source, units: room.units, coordinateSystem: room.coordinateSystem };
  return root;
}
export function disposeGroup(root: THREE.Object3D) {
  const materials = new Set<THREE.Material>();
  root.traverse(o => { if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) { o.geometry.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => materials.add(m)); } });
  materials.forEach(m => m.dispose());
}
