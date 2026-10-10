import * as THREE from 'three';
import { wallLength, type Room } from '../domain/room';

export function buildRoomGroup(room: Room, options: { furniture?: boolean; selected?: string } = {}) {
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
  }
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
    root.add(group);
  }
  if (options.furniture !== false) for (const object of room.objects) {
    const group = new THREE.Group(); group.name = object.label; group.userData.elementId = object.id;
    group.position.set(object.position.x, object.position.y, object.position.z); group.rotation.y = object.rotation;
    const { x, y, z } = object.size;
    const color = options.selected === object.id ? '#b5d68a' : /sofa|bed/.test(object.category) ? '#c3c8b6' : /chair/.test(object.category) ? '#b29372' : '#947b60';
    const mat = material(color);
    if (/sofa/.test(object.category)) {
      box([x, y * 0.5, z], [0, -y * 0.17, 0], mat, group, object.id);
      box([x, y * 0.45, z * 0.18], [0, y * 0.22, z * 0.4], mat, group, object.id);
      for (const side of [-1, 1]) box([x * 0.08, y * 0.75, z], [side * x * 0.46, 0, 0], mat, group, object.id);
    } else if (/table/.test(object.category)) {
      box([x, y * 0.16, z], [0, y * 0.42, 0], mat, group, object.id);
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) box([0.045, y * 0.84, 0.045], [sx * x * 0.4, -y * 0.08, sz * z * 0.4], mat, group, object.id);
    } else box([x, y, z], [0, 0, 0], mat, group, object.id);
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
