import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { ScanLine, Plus, ArrowUpRight, Box, Layers2, Download, Upload, Settings2, Ruler, RotateCcw, Undo2, Save, Check, ChevronRight, Sofa, Eye, CloudUpload, FolderOpen, Trash2, LoaderCircle, Smartphone, Menu, X, PanelTop, Crosshair, Tag, Maximize2, Minimize2, Plug, CircleCheck, ListChecks, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, SquareDashed } from 'lucide-react';
import { addDrawn, confirmElement, describeElement, fixtureLabel, floorArea, needsCheck, newRoom, parseRoom, perimeter, removeElement, type DrawnKind, type Room } from './domain/room';
import { sampleRoom } from './domain/sample';
import { normalizeCapture } from './domain/native';
import { listRooms, saveRoom, deleteRoom, syncRoom, pullRooms } from './services/storage';
import { captureCapabilities, enhanceRoom, scanRooms, RoomScanner } from './services/capture';
import FloorPlan from './components/FloorPlan';
import WallElevation, { PointsTable } from './components/WallElevation';
import Inspector from './components/Inspector';
import Dialog from './components/Dialog';
import { NoticeContext } from './components/NoticeContext';
const RoomViewer = lazy(() => import('./components/RoomViewer'));
type ViewMode = import('./components/RoomViewer').ViewMode;
type Display = import('./components/RoomViewer').Display;
type DrawnBox = import('./components/RoomViewer').DrawnBox;
const DRAWN_KINDS: DrawnKind[] = ['window', 'door', 'opening', 'switch_board', 'socket', 'ac_unit', 'light', 'hole', 'distribution_box', 'other_electrical', 'water_point', 'drain'];
type Modal = 'new' | 'scan' | 'export' | 'settings' | 'rename' | null;

export default function App() {
  const [room, setRoom] = useState<Room>(sampleRoom), [rooms, setRooms] = useState<Room[]>([]), [selected, setSelected] = useState('wall-1');
  const [dirty, setDirty] = useState(false), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [history, setHistory] = useState<Room[]>([]);
  const [view, setView] = useState<'3d' | 'plan' | 'walls'>('3d'), [furniture, setFurniture] = useState(true), [cutaway, setCutaway] = useState(true), [ceiling, setCeiling] = useState(true), [camera, setCamera] = useState<ViewMode>('orbit'), [viewKey, setViewKey] = useState(0), [focusKey, setFocusKey] = useState(0), [fixtures, setFixtures] = useState(true), [display, setDisplay] = useState<Display>('model'), [labels, setLabels] = useState(false), [measure, setMeasure] = useState(false), [marking, setMarking] = useState(false), [drawn, setDrawn] = useState<DrawnBox | null>(null), [full, setFull] = useState(false), [resetKey, setResetKey] = useState(0);
  // The wall shown in the Walls view: the selected wall, the wall of the selected point, else the wall with most points.
  const elevationWall = room.walls.some(w => w.id === selected) ? selected : room.fixtures?.find(f => f.id === selected)?.wallId
    ?? [...room.walls].sort((a, b) => (room.fixtures ?? []).filter(f => f.wallId === b.id).length - (room.fixtures ?? []).filter(f => f.wallId === a.id).length)[0]?.id ?? '';
  const [modal, setModal] = useState<Modal>(null), [notice, setNotice] = useState<{ text: string; error?: boolean; busy?: boolean } | null>(null), [navOpen, setNavOpen] = useState(false);
  const [capability, setCapability] = useState<Awaited<ReturnType<typeof captureCapabilities>> | null>(null);
  const [server, setServer] = useState(import.meta.env.VITE_API_URL ?? ''), [token, setToken] = useState(import.meta.env.VITE_API_TOKEN ?? '');
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true;
    listRooms().then(saved => { if (!active) return; setRooms(saved); if (saved.length) { setRoom(saved[0]); setSelected(saved[0].walls[0].id); } }).catch(() => { if (active) setNotice({ text: 'Local storage could not be opened. Export your room to keep a copy.', error: true }); }).finally(() => { if (active) setLoading(false); });
    captureCapabilities().then(c => { if (active) setCapability(c); });
    return () => { active = false; };
  }, []);
  useEffect(() => { if (!full) return; const leave = (e: KeyboardEvent) => { if (e.key === 'Escape') setFull(false); }; window.addEventListener('keydown', leave); return () => window.removeEventListener('keydown', leave); }, [full]);
  useEffect(() => { if (!notice || notice.error || notice.busy) return; const timer = setTimeout(() => setNotice(null), 4500); return () => clearTimeout(timer); }, [notice]);
  useEffect(() => { const handler = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); }; window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler); }, [dirty]);
  const error = (message: string) => setNotice({ text: message, error: true });
  const change = (next: Room) => {
    const geometryChanged = JSON.stringify([next.floor, next.walls, next.ceiling]) !== JSON.stringify([room.floor, room.walls, room.ceiling]);
    const measurementQuality = geometryChanged && next.measurementQuality ? { ...next.measurementQuality, status: 'needs-rescan' as const, reasons: ['Geometry edited after validation. Recheck dimensions.'], applied: [] } : next.measurementQuality;
    try { const valid = parseRoom({ ...next, measurementQuality, updatedAt: new Date().toISOString() }); setHistory(h => [...h.slice(-29), room]); setRoom(valid); setDirty(true); setNotice(null); return true; }
    catch (e) { error(e instanceof Error ? (e.message.startsWith('[') ? 'Please check your values. Some dimensions are outside the supported range.' : e.message) : 'Invalid room.'); return false; }
  };
  const save = useCallback(async () => {
    setBusy(true); try { await saveRoom(room); setRooms(await listRooms()); setDirty(false); setNotice({ text: 'Room saved on this device.' }); } catch { setNotice({ text: 'Could not save locally. Export a JSON copy to preserve your work.', error: true }); } finally { setBusy(false); }
  }, [room]);
  const undo = useCallback(() => { if (history.length) { setRoom(history.at(-1)!); setHistory(h => h.slice(0, -1)); setDirty(true); } }, [history]);
  useEffect(() => { const handler = (e: KeyboardEvent) => { const input = e.target instanceof HTMLElement && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName); if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (!busy && !loading) void save(); } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !input && !modal && !busy && !loading) { e.preventDefault(); undo(); } }; window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler); }, [save, undo, busy, loading, modal]);
  const canLeave = () => !dirty || window.confirm('You have unsaved changes. Continue without saving them?');
  const activate = (next: Room) => { setRoom(next); setSelected(next.walls[0].id); setHistory([]); setDirty(false); setNavOpen(false); setCamera('orbit'); setMeasure(false); setMarking(false); setDrawn(null); };
  // Items a person should look at before trusting them, and the card that walks through them one at a time.
  const toCheck = needsCheck(room), info = describeElement(room, selected), flagged = toCheck.includes(selected);
  const lookAt = (id: string) => { setView('3d'); setSelected(id); setFocusKey(k => k + 1); };
  const nextCheck = () => { if (toCheck.length) lookAt(toCheck[(toCheck.indexOf(selected) + 1) % toCheck.length]); };
  const settle = (keep: boolean) => {
    const rest = toCheck.filter(id => id !== selected), following = rest.length ? rest[toCheck.indexOf(selected) % rest.length] : room.walls[0].id;
    if (!change(keep ? confirmElement(room, selected) : removeElement(room, selected))) return;
    if (rest.length) lookAt(following); else setSelected(following);
  };
  // The on-screen walk pad reuses the keyboard path so there is one walking rule.
  const hold = useRef(0);
  const walkStart = (key: string) => { const step = () => window.dispatchEvent(new KeyboardEvent('keydown', { key })); step(); window.clearInterval(hold.current); hold.current = window.setInterval(step, 110); };
  const walkStop = () => window.clearInterval(hold.current);
  // After a scan, the server adds what the phone cannot work out alone: the measured ceiling and fixtures found on photos.
  const enhance = async (scanned: Room[], sources: unknown[]) => {
    for (const [index, original] of scanned.entries()) {
      const label = scanned.length > 1 ? ` (room ${index + 1} of ${scanned.length})` : '';
      try {
        setNotice({ text: `Sending scan to the server${label}…`, busy: true });
        const result = await enhanceRoom(original, sources[index], server, token, stage => setNotice({ text: `${stage}${label}…`, busy: true }));
        await saveRoom(result.room); setRooms(await listRooms());
        // Only swap the room on screen if the user is still looking at this one and has not started editing it.
        setRoom(current => current.id === result.room.id && current.updatedAt === original.updatedAt ? result.room : current);
        const found = [result.room.ceiling ? 'ceiling measured' : '', result.room.fixtures?.length ? `${result.room.fixtures.length} wall fixture(s)` : '', result.room.ceiling?.items?.length ? `${result.room.ceiling.items.length} ceiling item(s)` : ''].filter(Boolean).join(', ');
        setNotice({ text: `${original.name}: ${found || 'nothing new found'}. ${result.notes.filter(note => /not /i.test(note)).join(' ')}`.trim() });
      } catch (e) { error(`${original.name}: could not process on the server. ${e instanceof Error ? e.message : ''} The scanned room is saved without ceiling or fixtures.`); }
    }
  };
  const create = async (data: FormData, scan = false) => {
    if (!canLeave()) return;
    setBusy(true);
    try {
      const name = String(data.get('name')).trim() || 'Untitled room', height = Number(data.get('height'));
      const made = scan ? await scanRooms(name, height) : { rooms: [newRoom(name, Number(data.get('width')), Number(data.get('depth')), height)], skipped: 0, sources: [] };
      for (const room of made.rooms) await saveRoom(room);
      const captured = `${made.rooms.length > 1 ? `${made.rooms.length} rooms captured` : 'Capture saved'}. Review the dimensions before using them.${made.skipped ? ` ${made.skipped} room${made.skipped > 1 ? 's' : ''} could not be read and ${made.skipped > 1 ? 'were' : 'was'} skipped.` : ''}`;
      setRooms(await listRooms()); activate(made.rooms[0]); setModal(null); setNotice({ text: scan ? captured : 'New room created.' });
      if (scan && server && made.sources.length) void enhance(made.rooms, made.sources);
    } catch (e) { const message = e instanceof Error ? e.message : 'Capture failed.'; if (!/cancel/i.test(message)) error(message); }
    finally { setBusy(false); }
  };
  const importFile = async (file?: File) => {
    if (!file || !canLeave()) return;
    if (file.size > 5 * 1024 * 1024) { error('Room files must be smaller than 5 MB.'); return; }
    setBusy(true); try {
      const data = JSON.parse(await file.text());
      const parsed = data.kind ? normalizeCapture(data, file.name.replace(/\.json$/i, '')) : parseRoom(data);
      const imported = { ...parsed, id: crypto.randomUUID(), originalUsdzPath: undefined, updatedAt: new Date().toISOString() };
      await saveRoom(imported); setRooms(await listRooms()); activate(imported); setNotice({ text: 'Room imported as a new copy.' });
    } catch (e) { error(e instanceof Error ? (e.message.startsWith('[') ? 'This is not a valid Roomform v1 room file.' : e.message) : 'Could not import room.'); } finally { setBusy(false); if (fileInput.current) fileInput.current.value = ''; }
  };
  const exportRoom = async (format: string) => {
    setBusy(true); try {
      const { exportJson, exportGlb, exportSvg, exportPointsCsv, printFloorplan } = await import('./services/export');
      if (format === 'json') await exportJson(room); else if (format === 'csv') await exportPointsCsv(room); else if (format === 'glb') await exportGlb(room); else if (format === 'svg') await exportSvg(room); else if (format === 'pdf') printFloorplan(room); else if (room.originalUsdzPath) await RoomScanner.shareOriginal({ path: room.originalUsdzPath });
      if (format !== 'pdf') setNotice({ text: 'Export prepared.' });
    } catch (e) { error(e instanceof Error ? e.message : 'Export failed.'); } finally { setBusy(false); }
  };
  const sync = async (pull = false) => {
    setBusy(true); try {
      if (server) { const url = new URL(server); if (url.username || url.password || (!['localhost', '127.0.0.1'].includes(url.hostname) && url.protocol !== 'https:')) throw new Error('Use HTTPS for a remote server, or localhost for development.'); }
      if (pull) { const received = await pullRooms(server.replace(/\/$/, ''), token); setRooms(await listRooms()); setNotice({ text: `${received.length} room(s) imported from the server. Conflicting local rooms were preserved.` }); }
      else { await saveRoom(room); await syncRoom(room, server.replace(/\/$/, ''), token); setRooms(await listRooms()); setDirty(false); setNotice({ text: 'Room saved and synced.' }); }
    } catch (e) { error(e instanceof Error ? e.message : 'Sync failed.'); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!window.confirm(`Delete the local copy of “${room.name}”? Export a copy first if you need it.`)) return;
    setBusy(true); try { await deleteRoom(room.id); const rest = await listRooms(); setRooms(rest); activate(rest[0] ?? sampleRoom()); setNotice({ text: 'Local room deleted.' }); } catch { error('Could not delete room.'); } finally { setBusy(false); }
  };
  return <NoticeContext.Provider value={notice}><div className="app-shell">
    <aside className={`sidebar ${navOpen ? 'open' : ''}`}>
      <a className="brand" href="#" onClick={e => e.preventDefault()}><span className="brand-symbol"><Box size={23} /></span>roomform<span className="brand-dot">.</span></a>
      <button className="button primary scan-button" onClick={() => { setModal('scan'); setNavOpen(false); }} disabled={loading || busy}><ScanLine size={18} />Scan a room<Plus size={16} /></button>
      <div className="nav-label">WORKSPACE</div><button className="nav-item active" onClick={() => setNavOpen(false)}><Layers2 size={18} />My rooms<span>{rooms.length}</span></button>
      <button className="nav-item" onClick={() => fileInput.current?.click()} disabled={busy || loading}><Upload size={18} />Import room</button>
      <div className="room-list-heading"><span>YOUR ROOMS</span><button className="icon-button" aria-label="Create a room" onClick={() => setModal('new')} disabled={busy || loading}><Plus size={16} /></button></div>
      <div className="room-list">{loading ? <p className="muted">Loading rooms…</p> : rooms.length ? rooms.map(r => <button key={r.id} className={`room-link ${r.id === room.id ? 'current' : ''}`} onClick={() => { if (canLeave()) activate(r); }} disabled={busy}><span className="mini-room"><Box size={20} /></span><span>{r.name}<small>{r.floor.length ? `${floorArea(r).toFixed(1)} m²` : 'Floor incomplete'} · {r.source === 'sample' ? 'Sample' : r.source === 'manual' ? 'Manual' : 'Scan'}</small></span><ChevronRight size={14} /></button>) : <div className="room-list-empty"><FolderOpen size={23} /><p>Your saved rooms<br />will appear here.</p></div>}</div>
      <div className="sidebar-bottom"><div className="device-note"><span className="status-dot" /><div>{capability?.supported ? 'Native capture ready' : 'Room review studio'}<small>{capability?.supported ? capability.mode : 'Your rooms stay on this device'}</small></div></div><button className="nav-item" onClick={() => setModal('settings')}><Settings2 size={17} />Settings & sync</button><div className="version">ROOMFORM <span>EARLY ACCESS · 0.1</span></div></div>
    </aside>
    {navOpen ? <button className="nav-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} /> : null}
    <main className="workspace">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setNavOpen(true)}><Menu size={20} /></button><span>My rooms</span><ChevronRight size={14} /><strong>{room.name}</strong></div><div className="top-actions"><span className={`save-status ${dirty ? 'unsaved' : ''}`}>{dirty ? <span className="status-dot" /> : <Check size={13} />}{dirty ? 'Unsaved changes' : room.source === 'sample' && !rooms.some(r => r.id === room.id) ? 'Sample room' : 'Saved locally'}</span><button className="button secondary" onClick={() => void save()} disabled={busy || loading}><Save size={15} /><span>Save</span></button><button className="button dark" onClick={() => setModal('export')} disabled={busy || loading}><Download size={16} />Export</button></div></header>
      <section className="room-heading"><div><button className="room-title" disabled={busy || loading} onClick={() => setModal('rename')} title="Rename room"><h1>{room.name}</h1><ArrowUpRight size={21} /></button>{room.source === 'sample' ? <p>An example space to make your own. Start a scan or create a room.</p> : null}</div><div className="room-metrics"><div><span>FLOOR AREA</span><strong>{room.floor.length ? floorArea(room).toFixed(2) : '—'}<small>m²</small></strong></div><div><span>PERIMETER</span><strong>{perimeter(room).toFixed(2)}<small>m</small></strong></div><div><span>WALLS</span><strong>{room.walls.length.toString().padStart(2, '0')}</strong></div></div></section>
      <div className="studio" inert={busy || loading}><section className="viewer-panel"><div className="viewer-toolbar"><div className="tabs"><button className={view === '3d' ? 'active' : ''} onClick={() => setView('3d')}><Box size={15} />3D model</button><button className={view === 'plan' ? 'active' : ''} onClick={() => setView('plan')}><Layers2 size={15} />Floorplan</button><button className={view === 'walls' ? 'active' : ''} onClick={() => setView('walls')}><PanelTop size={15} />Walls</button></div><div className="view-actions"><button className="icon-button" onClick={undo} aria-label="Undo last edit" title="Undo" disabled={!history.length}><Undo2 size={17} /></button><button className="icon-button" onClick={() => setResetKey(v => v + 1)} aria-label="Reset camera" title="Reset camera"><RotateCcw size={17} /></button></div></div>
        <div className={`viewport${full ? ' full' : ''}${view === 'walls' ? ' walls-mode' : ''}`}>{view === 'walls' ? <div className="walls-view">
          <div className="wall-picker">{room.walls.map((w, i) => { const count = (room.fixtures ?? []).filter(f => f.wallId === w.id).length; return <button key={w.id} className={elevationWall === w.id ? 'active' : ''} onClick={() => setSelected(w.id)}>Wall {i + 1}{count ? ` · ${count}` : ''}</button>; })}
            <button className="text-button" onClick={() => void exportRoom('csv')} disabled={busy}><Download size={14} />Points as CSV</button></div>
          <WallElevation room={room} wallId={elevationWall} selected={selected} onSelect={setSelected} />
          <PointsTable room={room} selected={selected} onSelect={setSelected} />
        </div> : view === '3d' ? <Suspense fallback={<div className="viewer-loading"><LoaderCircle className="spin" />Preparing your room…</div>}><RoomViewer room={room} furniture={furniture} cutaway={cutaway} ceiling={ceiling && camera !== 'top'} selected={selected} onSelect={setSelected} resetKey={resetKey} view={camera} viewKey={viewKey} display={display} focusKey={focusKey} labels={labels} measure={measure} fixtures={fixtures} marking={marking} onDraw={setDrawn} /></Suspense> : <FloorPlan room={{ ...room, objects: furniture ? room.objects : [], fixtures: fixtures ? room.fixtures : [], ceiling: room.ceiling && { ...room.ceiling, items: fixtures ? room.ceiling.items : [] } }} selected={selected} onSelect={setSelected} />}
          <div className="viewport-tag"><span className="status-dot" />{room.source === 'sample' ? 'SAMPLE · NOT A SCAN' : room.source === 'manual' ? 'MANUAL ROOM' : room.source === 'arcore-depth' ? 'EXPERIMENTAL DEPTH LAYOUT' : 'CAPTURED ROOM'}</div>
          <div className="viewport-controls">{view === '3d' ? <span className="display-switch" role="group" aria-label="What to show">{([['model', 'Model'], ['lidar', 'LiDAR scan']] as const).map(([id, name]) => <button key={id} className={display === id ? 'enabled' : ''} aria-pressed={display === id} onClick={() => setDisplay(id)}>{name}</button>)}</span> : null}<button className={furniture ? 'enabled' : ''} aria-pressed={furniture} onClick={() => setFurniture(v => !v)}><Sofa size={16} />Furniture</button><button className={fixtures ? 'enabled' : ''} aria-pressed={fixtures} onClick={() => setFixtures(v => !v)}><Plug size={16} />Fixtures</button>{view === '3d' ? <button className={cutaway ? 'enabled' : ''} aria-pressed={cutaway} onClick={() => setCutaway(v => !v)}><Eye size={16} />Cutaway</button> : null}{view === '3d' ? <button className={ceiling ? 'enabled' : ''} aria-pressed={ceiling} onClick={() => setCeiling(v => !v)}><PanelTop size={16} />Ceiling</button> : null}</div>
          {view === '3d' ? <div className="viewport-views" role="group" aria-label="Camera view">{([['orbit', 'Orbit'], ['top', 'Top'], ['inside', 'Walk in']] as const).map(([id, name]) => <button key={id} className={camera === id ? 'enabled' : ''} aria-pressed={camera === id} onClick={() => { setCamera(id); setViewKey(k => k + 1); }}>{name}</button>)}</div> : null}
          <div className="viewport-tools">{view === '3d' ? <><button title="Look at the selected item" aria-label="Look at the selected item" onClick={() => setFocusKey(k => k + 1)}><Crosshair size={16} /></button>
            <button title="Measure between two points" aria-label="Measure between two points" className={measure ? 'enabled' : ''} aria-pressed={measure} onClick={() => { setMeasure(v => !v); setMarking(false); }}><Ruler size={16} /></button>
            <button title="Draw a box on a wall for something the scan missed" aria-label="Draw a box on a wall for something the scan missed" className={marking ? 'enabled' : ''} aria-pressed={marking} onClick={() => { setMarking(v => !v); setMeasure(false); setDrawn(null); }}><SquareDashed size={16} /></button>
            <button title="Show wall lengths" aria-label="Show wall lengths" className={labels ? 'enabled' : ''} aria-pressed={labels} onClick={() => setLabels(v => !v)}><Tag size={16} /></button></> : null}
            {toCheck.length ? <button title={`Go through ${toCheck.length} item${toCheck.length > 1 ? 's' : ''} that need a check`} aria-label="Go through the items that need a check" className="with-count" onClick={nextCheck}><ListChecks size={16} /><b>{toCheck.length}</b></button> : null}
            <button title={full ? 'Exit full screen' : 'Full screen'} aria-label={full ? 'Exit full screen' : 'Full screen'} onClick={() => setFull(v => !v)}>{full ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button></div>
          {drawn ? <div className="viewport-card"><strong>What is this? {(drawn.width * 100).toFixed(1)} × {(drawn.height * 100).toFixed(1)} cm on wall {room.walls.findIndex(w => w.id === drawn.wallId) + 1}, {(drawn.offset * 100).toFixed(1)} cm from its start, {(drawn.sill * 100).toFixed(1)} cm up</strong>
            <div className="kind-choices">{DRAWN_KINDS.map(kind => <button key={kind} className="capitalize" onClick={() => { try { const made = addDrawn(room, drawn, kind); if (change(made.room)) { setSelected(made.id); setDrawn(null); } } catch (e) { error(e instanceof Error ? e.message : 'Could not add it.'); } }}>{kind === 'window' || kind === 'door' || kind === 'opening' ? kind : fixtureLabel(kind)}</button>)}<button onClick={() => setDrawn(null)}>Cancel</button></div></div>
          : info ? <div className={`viewport-card${flagged ? ' flagged' : ''}`}><strong>{info}</strong>
            {flagged ? <><span>Not confirmed yet{toCheck.length > 1 ? ` · ${toCheck.indexOf(selected) + 1} of ${toCheck.length}` : ''}. Is it really there, and in the right place?</span>
              <div><button className="yes" onClick={() => settle(true)}><CircleCheck size={14} />Correct</button><button className="no" onClick={() => settle(false)}><Trash2 size={14} />Remove</button>{toCheck.length > 1 ? <button onClick={nextCheck}>Skip</button> : null}</div></>
              : toCheck.length ? <div><button onClick={nextCheck}><ListChecks size={14} />Check {toCheck.length} unconfirmed item{toCheck.length > 1 ? 's' : ''}</button></div> : null}</div> : null}
          {view === '3d' && camera === 'inside' ? <div className="walk-pad" aria-label="Walk">{([['ArrowUp', ArrowUp, 'forward'], ['ArrowLeft', ArrowLeft, 'left'], ['ArrowDown', ArrowDown, 'back'], ['ArrowRight', ArrowRight, 'right']] as const).map(([key, Icon, name]) => <button key={key} className={name} aria-label={`Walk ${name}`} onPointerDown={() => walkStart(key)} onPointerUp={walkStop} onPointerLeave={walkStop} onPointerCancel={walkStop}><Icon size={18} /></button>)}</div> : null}
          <div className="orientation" title="Model axis, not compass north"><span>−Z</span><div>↑</div></div>
        </div><div className="viewer-footer"><span><span className="status-dot" />{view === '3d' ? '' + (marking ? 'Drag on a wall to draw a box around something the scan missed; the LiDAR scan view shows the real colours' : measure ? 'Click two points to measure the distance between them' : camera === 'inside' ? 'Drag to look around · Arrow keys or double-click the floor to walk · Scroll to zoom the lens' : 'Drag to orbit · Scroll to zoom · Right-drag or two fingers to pan · Double-click to move in') : view === 'walls' ? 'Pick a wall · tap a point to select it · all values in cm, heights from the floor' : 'Tap a wall to select it'}</span></div>
        {room.measurementQuality ? <div className="review-note" role="status"><Ruler size={18} /><div><strong>{room.measurementQuality.status === 'needs-rescan' ? '2 cm accuracy not verified · rescan needed' : 'Repeat views consistent · absolute accuracy unverified'}</strong><p>{room.measurementQuality.reasons.join('. ')}</p></div></div> : null}
        {room.source === 'sample' ? <div className="review-note"><Ruler size={18} /><div><strong>A starting point, not a measurement</strong><p>This sample lets you try editing and exporting before your first scan.</p></div></div>
          : <details className="scan-notes"><summary><Ruler size={15} />Scan notes{room.warnings.length ? ` · ${room.warnings.length}` : ''}<span>Check key dimensions with a tape before building</span></summary>
            {room.warnings.length ? <ul>{room.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : <p>No notes for this room.</p>}</details>}
      </section><Inspector room={room} selected={selected} onSelect={setSelected} onChange={change} onError={error} /></div>
      <footer className="workspace-footer"><span /><button className="text-button" onClick={() => setModal('new')} disabled={busy || loading}><Plus size={14} />Create a room manually</button></footer>
    </main>
    <input ref={fileInput} className="sr-only" type="file" accept=".json,application/json" aria-label="Import room JSON" onChange={e => void importFile(e.target.files?.[0])} />
    {notice ? <div className={`toast ${notice.error ? 'error' : ''}`} role={notice.error ? 'alert' : 'status'}><span>{notice.text}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice(null)}><X size={16} /></button></div> : null}
    {modal === 'new' || modal === 'scan' ? <Dialog title={modal === 'scan' ? 'Capture your room' : 'Create a room'} onClose={() => { if (!busy) setModal(null); }}><div className="capture-info"><Smartphone size={28} /><div><strong>{modal === 'scan' ? capability?.supported ? capability.platform === 'ios' ? 'LiDAR room capture' : 'Android guided capture' : 'Scan from the installed mobile app' : 'Start with your dimensions'}</strong><p>{modal === 'scan' ? capability?.supported ? 'Move slowly around the room and cover walls, floor corners, doors, and windows. Review the result before saving.' : capability?.reason ?? 'Checking device support…' : 'Enter measured dimensions. You can edit corners and add openings afterwards.'}</p></div></div><form onSubmit={e => { e.preventDefault(); void create(new FormData(e.currentTarget), modal === 'scan'); }}><label>Room name<input name="name" placeholder="e.g. Living room" required maxLength={100} defaultValue="Living room" /></label>{modal === 'new' ? <div className="field-grid"><label>Width (m)<input name="width" type="number" min="0.5" max="50" step=".01" defaultValue="5" required /></label><label>Depth (m)<input name="depth" type="number" min="0.5" max="50" step=".01" defaultValue="4" required /></label></div> : null}<label>{modal === 'scan' && capability?.platform === 'ios' ? 'Fallback wall height (iOS measures automatically)' : 'Wall height (m)'}<input name="height" type="number" min="0.5" max="12" step=".01" defaultValue="2.7" required /></label><button className="button primary full" disabled={busy || loading || (modal === 'scan' && !capability?.supported)}>{busy ? <LoaderCircle className="spin" size={17} /> : modal === 'scan' ? <ScanLine size={17} /> : <Plus size={17} />}{busy ? 'Working…' : modal === 'scan' ? 'Start scanning' : 'Create room'}</button></form>{modal === 'scan' && !capability?.supported ? <button className="text-button full" onClick={() => setModal('new')}>Create a room with measurements instead</button> : null}<p className="hint">Captures stay on this device unless you export or choose to sync.</p>{capability?.platform === 'android' ? <p className="hint">This application runs on Google Play Services for AR (ARCore), which is provided by Google LLC and governed by the <a href="https://policies.google.com/privacy" target="_blank" rel="noreferrer">Google Privacy Policy</a>.</p> : null}</Dialog> : null}
    {modal === 'rename' ? <Dialog title="Room information" onClose={() => setModal(null)}><form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); if (change({ ...room, name: String(data.get('name')), notes: String(data.get('notes')) })) setModal(null); }}><label>Name<input name="name" defaultValue={room.name} required maxLength={100} /></label><label>Notes<textarea name="notes" defaultValue={room.notes} rows={4} maxLength={5000} /></label><button className="button primary full">Apply changes</button></form><button className="text-button danger" onClick={() => { setModal(null); void remove(); }} disabled={busy}><Trash2 size={15} />Delete local room</button></Dialog> : null}
    {modal === 'export' ? <Dialog title="Take your room with you" onClose={() => setModal(null)}><p className="muted">Export “{room.name}” with your current edits.</p><div className="export-options">{[['json', 'Editable room', 'JSON · Reopen in Roomform'], ['glb', '3D model', 'GLB · Geometry with furniture volumes'], ['svg', 'Vector floorplan', 'SVG · Dimensions and layout'], ['csv', 'Points list', 'CSV · Switches, sockets and water points with dimensions'], ['pdf', 'Print / Save as PDF', 'Opens the system print dialog']].filter(([key]) => key !== 'pdf' || capability?.platform === 'web').map(([key, title, subtitle]) => <button key={key} onClick={() => void exportRoom(key)} disabled={busy}><span className="format-icon">{key.toUpperCase()}</span><span><strong>{title}</strong><small>{subtitle}</small></span><Download size={17} /></button>)}{room.originalUsdzPath ? <button onClick={() => void exportRoom('usdz')} disabled={busy}><span className="format-icon">USDZ</span><span><strong>Original Apple capture</strong><small>Original scan · Editor changes are not included</small></span><Download size={17} /></button> : null}</div><p className="hint">Exports use meters. Furniture is represented by simplified volumes.</p></Dialog> : null}
    {modal === 'settings' ? <Dialog title="Storage & sync" onClose={() => setModal(null)}><div className="capture-info"><Save size={25} /><div><strong>Local by default</strong><p>Rooms are saved on this device. Keep a JSON export as a portable backup.</p></div></div><label>Optional sync server<input value={server} onChange={e => setServer(e.target.value)} placeholder="Same-origin local API, or https://your-server" type="text" /></label><label>Access token<input value={token} onChange={e => setToken(e.target.value)} placeholder="Required for a remote server" type="password" autoComplete="off" /></label><p className="hint">The token is kept in memory for this session. Sync sends the room model, not camera images.</p><div className="button-row"><button className="button primary" onClick={() => void sync()} disabled={busy}><CloudUpload size={16} />Sync this room</button><button className="button secondary" onClick={() => void sync(true)} disabled={busy}><Download size={16} />Pull rooms</button></div><div className="settings-device"><strong>Capture support</strong><p>{capability?.supported ? `${capability.platform} · ${capability.mode}` : capability?.reason}</p></div></Dialog> : null}
  </div></NoticeContext.Provider>;
}
