import { Capacitor } from '@capacitor/core';
import { Filesystem, Directory } from '@capacitor/filesystem';
import { Share } from '@capacitor/share';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { buildRoomGroup, disposeGroup } from '../scene/buildRoom';
import FloorPlan from '../components/FloorPlan';
import { floorArea, pointsCsv, type Room } from '../domain/room';
const filename = (room: Room) => room.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase() || 'room';
export async function deliverFile(blob: Blob, name: string) {
  if (Capacitor.isNativePlatform()) {
    const data = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(blob); });
    const result = await Filesystem.writeFile({ path: `exports/${name}`, data, directory: Directory.Cache, recursive: true });
    await Share.share({ title: name, files: [result.uri] }); return;
  }
  const url = URL.createObjectURL(blob), anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
}
export async function exportJson(room: Room) { await deliverFile(new Blob([JSON.stringify({ ...room, originalUsdzPath: undefined }, null, 2)], { type: 'application/json' }), `${filename(room)}.room.json`); }
export async function exportPointsCsv(room: Room) {
  // A byte-order mark so Excel reads the file as UTF-8.
  await deliverFile(new Blob(['\ufeff' + pointsCsv(room)], { type: 'text/csv' }), `${filename(room)}-points.csv`);
}
export async function exportGlb(room: Room) {
  const group = buildRoomGroup(room, { ceiling: true });
  try { const glb = await new GLTFExporter().parseAsync(group, { binary: true }); if (!(glb instanceof ArrayBuffer)) throw new Error('Invalid GLB export.'); await deliverFile(new Blob([glb], { type: 'model/gltf-binary' }), `${filename(room)}.glb`); }
  finally { disposeGroup(group); }
}
export async function exportSvg(room: Room) {
  const svg = renderToStaticMarkup(createElement(FloorPlan, { room })).replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ');
  await deliverFile(new Blob([svg], { type: 'image/svg+xml' }), `${filename(room)}-floorplan.svg`);
}
export function printFloorplan(room: Room) {
  const escape = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
  const win = window.open('', '_blank'); if (!win) throw new Error('Allow pop-ups to print the floorplan.');
  const svg = renderToStaticMarkup(createElement(FloorPlan, { room }));
  win.document.write(`<!doctype html><html><head><title>${escape(room.name)} — Floorplan</title><style>body{font:14px system-ui;margin:30px;color:#22352a}svg{width:100%;height:70vh}p{color:#59655a}@page{size:A4;margin:15mm}</style></head><body><h1>${escape(room.name)}</h1><p>${floorArea(room).toFixed(2)} m² · Source: ${escape(room.source)} · Dimensions in meters</p>${svg}<p>Review scan dimensions against independent measurements before installation. ${escape(room.notes)}</p></body></html>`);
  win.document.close(); win.focus(); setTimeout(() => win.print(), 300);
}
