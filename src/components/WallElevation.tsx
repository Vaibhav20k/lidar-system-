import { beamsAlongWall, fixtureLabel, pointDimensions, wallLength, type Room } from '../domain/room';

const cm = (v: number) => `${(v * 100).toFixed(1)}`;

// One wall seen from inside the room, as on a site drawing: doors and windows, beams and the ceiling above, every
// point on the wall, a dimension chain from corner to corner along the floor, and each point's height and gap above.
export default function WallElevation({ room, wallId, selected = '', onSelect }: { room: Room; wallId: string; selected?: string; onSelect?: (id: string) => void }) {
  const wall = room.walls.find(w => w.id === wallId);
  if (!wall) return null;
  const length = wallLength(wall), number = room.walls.indexOf(wall) + 1;
  const top = Math.max(wall.baseY + wall.height, room.ceiling?.height ?? 0);
  const points = (room.fixtures ?? []).filter(f => f.wallId === wall.id).sort((a, b) => a.offset - b.offset);
  const openings = room.openings.filter(o => o.wallId === wall.id);
  // Beams running along this wall, the same ones the points' "to ceiling" gap is measured to.
  const beams = beamsAlongWall(room, wall);
  // Lettering scales with the drawing so it stays readable on long walls.
  const font = Math.max(0.07, Math.max(length, top) / 38), pad = font * 6, chainY = -font * 3;
  const y = (h: number) => top - h;  // drawing y grows downward; heights grow upward
  const stops = [0, ...points.flatMap(f => [f.offset, f.offset + f.width]), length].sort((a, b) => a - b);
  return <svg className="wall-elevation" viewBox={`${-pad} ${-pad / 2} ${length + 2 * pad} ${top + pad * 1.6}`} role="img" aria-label={`Wall ${number} elevation, ${length.toFixed(2)} metres`}>
    <rect x={0} y={y(wall.baseY + wall.height)} width={length} height={wall.height} fill="#f3f2ea" stroke="#434d43" strokeWidth=".012" />
    {room.ceiling ? <line x1={0} x2={length} y1={y(room.ceiling.height)} y2={y(room.ceiling.height)} stroke="#5d6b7b" strokeWidth=".01" strokeDasharray=".05 .03" /> : null}
    {beams.map((b, i) => <g key={i}><rect x={b.from} y={y(room.ceiling!.height)} width={b.to - b.from} height={room.ceiling!.height - b.height} fill="#c9ced6" stroke="#5d6b7b" strokeWidth=".008" />
      <text x={(b.from + b.to) / 2} y={y(b.height) - 0.03} fontSize={font * 0.8} textAnchor="middle" fill="#3c4856">beam · {cm(room.ceiling!.height - b.height)} cm deep · {cm(b.height)} cm up</text></g>)}
    {openings.map(o => <g key={o.id}><rect x={o.offset} y={y(wall.baseY + o.sill + o.height)} width={o.width} height={o.height} fill={o.kind === 'window' ? '#d6e7ea' : '#e4dccb'} stroke="#7b8579" strokeWidth=".01" />
      <text x={o.offset + o.width / 2} y={y(wall.baseY + o.sill + o.height / 2)} fontSize={font} textAnchor="middle" fill="#4c564b">{o.kind} {cm(o.width)}</text></g>)}
    <line x1={-0.1} x2={length + 0.1} y1={y(wall.baseY)} y2={y(wall.baseY)} stroke="#26372c" strokeWidth=".02" />
    {points.map(f => {
      const i = (room.fixtures ?? []).indexOf(f);  // the same number as in the points table
      const d = pointDimensions(room, f)!, picked = selected === f.id;
      const colour = f.review ? '#d9a521' : f.kind === 'water_point' ? '#4f8fbf' : f.kind === 'drain' ? '#8d9196' : '#e07b39';
      return <g key={f.id} onClick={() => onSelect?.(f.id)} className={onSelect ? 'plan-wall' : ''}>
        <title>{`${fixtureLabel(f.kind)}: ${cm(d.width)} × ${cm(d.height)} cm, bottom ${cm(d.bottom)} cm above the floor`}</title>
        <rect x={f.offset} y={y(wall.baseY + d.top)} width={f.width} height={f.height} fill={picked ? '#9cc070' : colour} fillOpacity=".85" stroke="#26372c" strokeWidth=".006" />
        <text x={f.offset + f.width / 2} y={y(wall.baseY + d.top) - font * 0.4} fontSize={font * 0.85} textAnchor="middle" fill="#26372c" fontWeight="600">{i + 1}</text>
        {/* Height from the floor to the bottom, and the gap up to the ceiling or beam. */}
        <line x1={f.offset + f.width + 0.025} x2={f.offset + f.width + 0.025} y1={y(wall.baseY)} y2={y(wall.baseY + d.bottom)} stroke="#8a5a2b" strokeWidth=".006" strokeDasharray=".02 .015" />
        <text x={f.offset + f.width + font * 0.5} y={y(wall.baseY + d.bottom / 2)} fontSize={font * 0.8} fill="#8a5a2b">{cm(d.bottom)}</text>
        {d.toCeiling !== null ? <text x={f.offset + f.width + font * 0.5} y={y(wall.baseY + d.top) - font * 1.4} fontSize={font * 0.7} fill="#5d6b7b">↑{cm(d.toCeiling)}</text> : null}
      </g>;
    })}
    {/* Dimension chain along the floor, corner to corner, through every point's edges. */}
    {stops.slice(1).map((stop, i) => { const from = stops[i]; if (stop - from < 0.005) return null; return <g key={i}>
      <line x1={from} x2={stop} y1={y(wall.baseY) - chainY} y2={y(wall.baseY) - chainY} stroke="#26372c" strokeWidth=".006" />
      <line x1={from} x2={from} y1={y(wall.baseY) - chainY - font * 0.5} y2={y(wall.baseY) - chainY + font * 0.5} stroke="#26372c" strokeWidth=".006" />
      <text x={(from + stop) / 2} y={y(wall.baseY) - chainY - font * 0.4} fontSize={font * 0.75} textAnchor="middle" fill="#26372c">{cm(stop - from)}</text>
    </g>; })}
    <line x1={length} x2={length} y1={y(wall.baseY) - chainY - font * 0.5} y2={y(wall.baseY) - chainY + font * 0.5} stroke="#26372c" strokeWidth=".006" />
    <text x={length / 2} y={y(wall.baseY) - chainY + font * 2} fontSize={font} textAnchor="middle" fill="#657060">Wall {number} · {cm(length)} cm · centimetres, heights from the floor</text>
  </svg>;
}

// Every point of the room in a table, the same numbers as the drawing.
export function PointsTable({ room, selected = '', onSelect }: { room: Room; selected?: string; onSelect?: (id: string) => void }) {
  const rows = (room.fixtures ?? []).map((f, i) => ({ f, i, d: pointDimensions(room, f) })).filter(r => r.d);
  if (!rows.length) return <p className="muted empty-note">No switches, sockets or water points on this room's walls yet.</p>;
  return <div className="points-table-wrap"><table className="points-table">
    <thead><tr><th>#</th><th>Point</th><th>Wall</th><th>From start</th><th>From end</th><th>Bottom</th><th>Top</th><th>To ceiling</th><th>Size</th><th>±</th></tr></thead>
    <tbody>{rows.map(({ f, i, d }) => <tr key={f.id} className={selected === f.id ? 'selected' : ''} onClick={() => onSelect?.(f.id)}>
      <td>{i + 1}</td><td className="capitalize">{fixtureLabel(f.kind)}{f.review ? ' · check' : ''}</td><td>{d!.wall}</td><td>{cm(d!.fromStart)}</td><td>{cm(d!.fromEnd)}</td>
      <td>{cm(d!.bottom)}</td><td>{cm(d!.top)}</td><td>{d!.toCeiling === null ? '–' : `${cm(d!.toCeiling)}${d!.overhead === 'beam' ? ' (beam)' : ''}`}</td>
      <td>{cm(d!.width)} × {cm(d!.height)}</td><td>{d!.tolerance !== undefined ? `${(d!.tolerance * 1000).toFixed(0)} mm` : '–'}</td>
    </tr>)}</tbody>
  </table><p className="hint">Centimetres. Heights from the floor under each point; "to ceiling" is the gap above it to the ceiling or the beam over it.</p></div>;
}
