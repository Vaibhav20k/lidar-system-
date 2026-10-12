import { newRoom, type Room } from './room';
export function sampleRoom(): Room {
  const room = newRoom('The living room', 5.8, 4.4, 2.8);
  return { ...room, id: 'sample-living-room', source: 'sample', floorEvidence: 'sample',
    walls: room.walls.map(w => ({ ...w, evidence: 'sample' })),
    openings: [
      { id: 'window-1', wallId: 'wall-1', kind: 'window', offset: 1.7, width: 2.4, height: 1.45, sill: 0.85, evidence: 'sample' },
      { id: 'door-1', wallId: 'wall-4', kind: 'door', offset: 0.4, width: 0.95, height: 2.1, sill: 0, evidence: 'sample' },
    ],
    objects: [
      { id: 'sofa-1', label: 'Sofa', category: 'sofa', position: { x: 2.7, y: 0.4, z: 3.55 }, size: { x: 2.65, y: 0.8, z: 0.9 }, rotation: Math.PI, evidence: 'sample' },
      { id: 'table-1', label: 'Coffee table', category: 'table', position: { x: 2.8, y: 0.22, z: 2.25 }, size: { x: 1.25, y: 0.44, z: 0.7 }, rotation: 0, evidence: 'sample' },
      { id: 'storage-1', label: 'Media unit', category: 'storage', position: { x: 4.95, y: 0.34, z: 1.6 }, size: { x: 1.7, y: 0.68, z: 0.45 }, rotation: -Math.PI / 2, evidence: 'sample' },
      { id: 'chair-1', label: 'Lounge chair', category: 'chair', position: { x: 0.9, y: 0.4, z: 1.1 }, size: { x: 0.8, y: 0.8, z: 0.8 }, rotation: -0.4, evidence: 'sample' },
    ], notes: 'Sample room for exploring the editor. This is not a sensor scan.', warnings: [],
  };
}
