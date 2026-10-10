import { Capacitor, registerPlugin } from '@capacitor/core';
import { normalizeCapture } from '../domain/native';
import type { Room } from '../domain/room';
interface ScannerPlugin {
  capabilities(): Promise<{ supported: boolean; platform: string; mode: string; reason?: string }>;
  scan(options: { height: number; recordRaw?: boolean }): Promise<{ capture?: unknown; captures?: unknown[] }>;
  shareOriginal(options: { path: string }): Promise<void>;
}
export const RoomScanner = registerPlugin<ScannerPlugin>('RoomScanner');
export async function captureCapabilities() {
  if (!Capacitor.isNativePlatform()) return { supported: false, platform: 'web', mode: 'review', reason: 'Open the installed iPhone or Android app for sensor scanning. You can create, import, and edit rooms here.' };
  try { return await RoomScanner.capabilities(); }
  catch { return { supported: false, platform: Capacitor.getPlatform(), mode: 'unavailable', reason: 'The native scanner is not available in this build. Sync and rebuild the native app.' }; }
}
// iOS returns one capture per scanned room; Android returns a single capture.
export async function scanRooms(name: string, height: number): Promise<{ rooms: Room[]; skipped: number }> {
  const result = await RoomScanner.scan({ height });
  return normalizeCaptures(result.captures ?? [result.capture], name);
}
export function normalizeCaptures(captures: unknown[], name: string): { rooms: Room[]; skipped: number } {
  const rooms: Room[] = []; let failure: unknown;
  captures.forEach((capture, i) => {
    // One unusable room must not discard the others from the same walk-through.
    try { rooms.push(normalizeCapture(capture, i ? `${name.slice(0, 90)} ${i + 1}` : name)); } catch (e) { failure ??= e; }
  });
  if (!rooms.length) throw failure ?? new Error('The scan returned no rooms.');
  return { rooms, skipped: captures.length - rooms.length };
}
