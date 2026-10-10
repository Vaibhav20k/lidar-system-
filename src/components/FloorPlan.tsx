import { bounds, wallLength, type Room } from '../domain/room';
export default function FloorPlan({ room, selected = '', onSelect }: { room: Room; selected?: string; onSelect?: (id: string) => void }) {
  const b = bounds(room), margin = 0.9, width = b.width + margin * 2, height = b.depth + margin * 2;
  return <svg className="floorplan" viewBox={`${b.minX - margin} ${b.minZ - margin} ${width} ${height}`} role="img" aria-label="Measured room floorplan">
    <defs><pattern id="floor-grid" width="0.25" height="0.25" patternUnits="userSpaceOnUse"><path d="M .25 0 L 0 0 0 .25" fill="none" stroke="#dbded4" strokeWidth=".006" /></pattern></defs>
    <rect x={b.minX - margin} y={b.minZ - margin} width={width} height={height} fill="url(#floor-grid)" />
    {room.floor.length ? <polygon points={room.floor.map(p => `${p.x},${p.z}`).join(' ')} fill="#e8e4d8" /> : null}
    {room.objects.map(o => <g key={o.id} transform={`translate(${o.position.x},${o.position.z}) rotate(${-o.rotation * 180 / Math.PI})`}><rect x={-o.size.x / 2} y={-o.size.z / 2} width={o.size.x} height={o.size.z} rx=".05" fill="#c6ccb9" stroke="#899278" strokeWidth=".018" /></g>)}
    {room.walls.map((w, i) => <g key={w.id} className={onSelect ? 'plan-wall' : ''} role={onSelect ? 'button' : undefined} tabIndex={onSelect ? 0 : undefined} aria-label={`Select wall ${i + 1}, ${wallLength(w).toFixed(2)} meters`} onClick={() => onSelect?.(w.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect?.(w.id); } }}>
      <line x1={w.start.x} y1={w.start.z} x2={w.end.x} y2={w.end.z} stroke={selected === w.id ? '#648246' : '#434d43'} strokeWidth={w.thickness} />
      <line x1={w.start.x} y1={w.start.z} x2={w.end.x} y2={w.end.z} stroke="transparent" strokeWidth=".35" />
      <text x={(w.start.x + w.end.x) / 2} y={(w.start.z + w.end.z) / 2 - 0.18} textAnchor="middle" fontSize=".16" fill="#26372c" stroke="#f3f3ed" strokeWidth=".05" paintOrder="stroke">{wallLength(w).toFixed(2)} m</text>
    </g>)}
    {room.openings.map(o => { const w = room.walls.find(w => w.id === o.wallId)!; const length = wallLength(w), dx = (w.end.x - w.start.x) / length, dz = (w.end.z - w.start.z) / length; return <line key={o.id} x1={w.start.x + dx * o.offset} y1={w.start.z + dz * o.offset} x2={w.start.x + dx * (o.offset + o.width)} y2={w.start.z + dz * (o.offset + o.width)} stroke={o.kind === 'window' ? '#7eafbb' : '#f3f3ed'} strokeWidth={w.thickness + 0.025} />; })}
    {room.floor.map((p, i) => <g key={i}><circle cx={p.x} cy={p.z} r=".07" fill="#f6f8ee" stroke="#424c42" strokeWidth=".025" /><text x={p.x + 0.15} y={p.z + 0.22} fontSize=".12" fill="#657060">{i + 1}</text></g>)}
  </svg>;
}
