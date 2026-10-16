import { bounds, columnName, columnsOf, fixtureLabel, wallInside, wallLength, type Room } from '../domain/room';
export default function FloorPlan({ room, selected = '', onSelect }: { room: Room; selected?: string; onSelect?: (id: string) => void }) {
  const b = bounds(room), columns = columnsOf(room), margin = 0.9, width = b.width + margin * 2, height = b.depth + margin * 2;
  return <svg className="floorplan" viewBox={`${b.minX - margin} ${b.minZ - margin} ${width} ${height}`} role="img" aria-label="Measured room floorplan">
    <defs><pattern id="floor-grid" width="0.25" height="0.25" patternUnits="userSpaceOnUse"><path d="M .25 0 L 0 0 0 .25" fill="none" stroke="#dbded4" strokeWidth=".006" /></pattern></defs>
    <rect x={b.minX - margin} y={b.minZ - margin} width={width} height={height} fill="url(#floor-grid)" />
    {room.floor.length ? <polygon points={room.floor.map(p => `${p.x},${p.z}`).join(' ')} fill="#e8e4d8" /> : null}
    {room.objects.map(o => <g key={o.id} transform={`translate(${o.position.x},${o.position.z}) rotate(${-o.rotation * 180 / Math.PI})`}><rect x={-o.size.x / 2} y={-o.size.z / 2} width={o.size.x} height={o.size.z} rx=".05" fill="#c6ccb9" stroke="#899278" strokeWidth=".018" />
      {/* Local -Z is the back of scanned furniture: mark chair and sofa backs, and the pillow end of beds. */}
      {/chair|sofa/.test(o.category) && o.attributes?.ChairType !== 'stool' && o.attributes?.ChairBackType !== 'missing' ? <rect x={-o.size.x / 2} y={-o.size.z / 2} width={o.size.x} height={o.size.z * 0.18} rx=".03" fill="#899278" /> : null}
      {/bed/.test(o.category) ? [-1, 1].map(side => <rect key={side} x={side * o.size.x * 0.22 - o.size.x * 0.18} y={-o.size.z * 0.46} width={o.size.x * 0.36} height={o.size.z * 0.16} rx=".04" fill="#f3f1e8" stroke="#899278" strokeWidth=".012" />) : null}</g>)}
    {room.walls.map((w, i) => { const column = columns.find(c => c.wallIds.includes(w.id)); return <g key={w.id} className={onSelect ? 'plan-wall' : ''} role={onSelect ? 'button' : undefined} tabIndex={onSelect ? 0 : undefined} aria-label={column ? `Select ${columnName(column)}` : `Select wall ${i + 1}, ${wallLength(w).toFixed(2)} meters`} onClick={() => onSelect?.(w.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect?.(w.id); } }}>
      {/* The wall line is the wall's inner face: the thickness is drawn behind it, as in the 3D model. */}
      {(() => { const l = wallLength(w), back = -wallInside(room, w) * w.thickness / 2, ox = -(w.end.z - w.start.z) / l * back, oz = (w.end.x - w.start.x) / l * back;
        return <line x1={w.start.x + ox} y1={w.start.z + oz} x2={w.end.x + ox} y2={w.end.z + oz} stroke={selected === w.id ? '#648246' : '#434d43'} strokeWidth={w.thickness} />; })()}
      <line x1={w.start.x} y1={w.start.z} x2={w.end.x} y2={w.end.z} stroke="transparent" strokeWidth=".35" />
      {/* A column is named once at its corner; its faces carry no lengths of their own. */}
      {column ? (column.wallIds[0] === w.id ? <text x={w.end.x} y={w.end.z + 0.3} textAnchor="middle" fontSize=".13" fill="#26372c" stroke="#f3f3ed" strokeWidth=".05" paintOrder="stroke">{columnName(column)}</text> : null)
        : <text x={(w.start.x + w.end.x) / 2} y={(w.start.z + w.end.z) / 2 - 0.18} textAnchor="middle" fontSize=".16" fill="#26372c" stroke="#f3f3ed" strokeWidth=".05" paintOrder="stroke">{wallLength(w).toFixed(2)} m</text>}
    </g>; })}
    {room.openings.map(o => { const w = room.walls.find(w => w.id === o.wallId)!; const length = wallLength(w), dx = (w.end.x - w.start.x) / length, dz = (w.end.z - w.start.z) / length; return <line key={o.id} x1={w.start.x + dx * o.offset} y1={w.start.z + dz * o.offset} x2={w.start.x + dx * (o.offset + o.width)} y2={w.start.z + dz * (o.offset + o.width)} stroke={o.kind === 'window' ? '#7eafbb' : '#f3f3ed'} strokeWidth={w.thickness + 0.025} />; })}
    {/* Dashed outlines mark parts of the ceiling that hang lower, as on a reflected ceiling plan. */}
    {(room.ceiling?.regions ?? []).map((d, i) => { const lower = d.height < room.ceiling!.height; return <polygon key={i} points={d.points.map(p => `${p.x},${p.z}`).join(' ')} fill="#7c8a9a" fillOpacity={lower ? 0.12 : 0} stroke="#5d6b7b" strokeWidth=".02" strokeDasharray={lower ? '.09 .06' : '.02 .05'}><title>{`Ceiling ${lower ? 'drops' : 'rises'} to ${d.height.toFixed(2)} m here`}</title></polygon>; })}
    {(room.fixtures ?? []).map(f => { const w = room.walls.find(w => w.id === f.wallId)!; const length = wallLength(w), dx = (w.end.x - w.start.x) / length, dz = (w.end.z - w.start.z) / length, along = f.offset + f.width / 2;
      // Nudge the marker onto the room side of the wall line.
      const side = wallInside(room, w), out = 0.06;
      return <g key={f.id} className={onSelect ? 'plan-wall' : ''} onClick={() => onSelect?.(f.id)}><title>{`${fixtureLabel(f.kind)}, ${(f.sill + f.height / 2).toFixed(2)} m above floor`}</title><rect x={-Math.max(0.1, f.width) / 2} y={-0.045} width={Math.max(0.1, f.width)} height={0.09} rx=".02" transform={`translate(${w.start.x + dx * along - dz * side * out},${w.start.z + dz * along + dx * side * out}) rotate(${Math.atan2(dz, dx) * 180 / Math.PI})`} fill={selected === f.id ? '#648246' : f.kind === 'light' ? '#e2b53c' : f.kind === 'hole' ? '#26272b' : f.kind === 'ac_unit' ? '#5d8fb5' : '#e07b39'} stroke="#f3f3ed" strokeWidth=".012" /></g>; })}
    {/* Ceiling items, as on a reflected ceiling plan: lights are crossed circles, holes are dark squares. */}
    {(room.ceiling?.items ?? []).map(i => <g key={i.id} className={onSelect ? 'plan-wall' : ''} onClick={() => onSelect?.(i.id)} transform={`translate(${i.x},${i.z}) rotate(${i.rotation * 180 / Math.PI})`}><title>{`${fixtureLabel(i.kind)} on the ceiling at ${i.height.toFixed(2)} m`}</title>
      {i.kind === 'light' && Math.max(i.width, i.depth) < 0.3 ? <><circle r={Math.max(0.07, i.width / 2)} fill="#ffe9a8" stroke={selected === i.id ? '#648246' : '#b8891f'} strokeWidth=".02" /><path d="M -.05 -.05 L .05 .05 M -.05 .05 L .05 -.05" stroke="#b8891f" strokeWidth=".015" /></>
        : <rect x={-Math.max(0.08, i.width) / 2} y={-Math.max(0.08, i.depth) / 2} width={Math.max(0.08, i.width)} height={Math.max(0.08, i.depth)} rx=".01" fill={i.kind === 'hole' ? '#26272b' : i.kind === 'light' ? '#ffe9a8' : '#9a9d98'} stroke={selected === i.id ? '#648246' : '#6b6f68'} strokeWidth=".02" />}</g>)}
    {room.floor.map((p, i) => <g key={i}><circle cx={p.x} cy={p.z} r=".07" fill="#f6f8ee" stroke="#424c42" strokeWidth=".025" /><text x={p.x + 0.15} y={p.z + 0.22} fontSize=".12" fill="#657060">{i + 1}</text></g>)}
  </svg>;
}
