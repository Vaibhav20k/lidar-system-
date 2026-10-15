import { Capacitor, registerPlugin, type Plugin } from '@capacitor/core';
import { normalizeCapture } from '../domain/native';
import { parseRoom, type Room } from '../domain/room';
interface ScannerPlugin extends Plugin {
  capabilities(): Promise<{ supported: boolean; platform: string; mode: string; reason?: string }>;
  scan(options: { height: number; recordRaw?: boolean }): Promise<{ capture?: unknown; captures?: unknown[] }>;
  shareOriginal(options: { path: string }): Promise<void>;
  enhance(options: { rawPath: string; url: string; token: string; payload: string }): Promise<{ room: unknown; notes?: string[] }>;
}
export const RoomScanner = registerPlugin<ScannerPlugin>('RoomScanner');
export async function captureCapabilities() {
  if (!Capacitor.isNativePlatform()) return { supported: false, platform: 'web', mode: 'review', reason: 'Open the installed iPhone or Android app for sensor scanning. You can create, import, and edit rooms here.' };
  try { return await RoomScanner.capabilities(); }
  catch { return { supported: false, platform: Capacitor.getPlatform(), mode: 'unavailable', reason: 'The native scanner is not available in this build. Sync and rebuild the native app.' }; }
}
// iOS returns one capture per scanned room; Android returns a single capture.
export async function scanRooms(name: string, height: number): Promise<{ rooms: Room[]; skipped: number; sources: unknown[] }> {
  const result = await RoomScanner.scan({ height });
  return normalizeCaptures(result.captures ?? [result.capture], name);
}
export function normalizeCaptures(captures: unknown[], name: string): { rooms: Room[]; skipped: number; sources: unknown[] } {
  const rooms: Room[] = [], sources: unknown[] = []; let failure: unknown;
  captures.forEach((capture, i) => {
    // One unusable room must not discard the others from the same walk-through.
    try { rooms.push(normalizeCapture(capture, i ? `${name.slice(0, 90)} ${i + 1}` : name)); sources.push(capture); } catch (e) { failure ??= e; }
  });
  if (!rooms.length) throw failure ?? new Error('The scan returned no rooms.');
  return { rooms, skipped: captures.length - rooms.length, sources };
}
// Sends a scan's raw data to a Roomform server, which measures the ceiling and (if switched on there) looks for
// switches, lights, holes and AC units on the scan photos. Resolves with the same room plus what was found.
export async function enhanceRoom(room: Room, capture: unknown, server: string, token: string, onStage?: (stage: string) => void): Promise<{ room: Room; notes: string[] }> {
  const rawPath = (capture as { rawPath?: unknown } | null)?.rawPath;
  if (typeof rawPath !== 'string') throw new Error('This scan kept no raw data to process.');
  const listener = onStage ? await RoomScanner.addListener('enhanceProgress', (event: { stage: string }) => onStage(event.stage)) : undefined;
  try {
    const result = await RoomScanner.enhance({ rawPath, url: server.replace(/\/$/, ''), token, payload: JSON.stringify({ capture, room: { ...room, originalUsdzPath: undefined } }) });
    return { room: parseRoom({ ...(result.room as object), originalUsdzPath: room.originalUsdzPath }), notes: result.notes ?? [] };
  } finally { await listener?.remove(); }
}
