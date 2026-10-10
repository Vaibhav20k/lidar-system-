import { Capacitor, registerPlugin } from '@capacitor/core';
import { normalizeCapture } from '../domain/native';
interface ScannerPlugin {
  capabilities(): Promise<{ supported: boolean; platform: string; mode: string; reason?: string }>;
  scan(options: { height: number }): Promise<{ capture: unknown }>;
  shareOriginal(options: { path: string }): Promise<void>;
}
export const RoomScanner = registerPlugin<ScannerPlugin>('RoomScanner');
export async function captureCapabilities() {
  if (!Capacitor.isNativePlatform()) return { supported: false, platform: 'web', mode: 'review', reason: 'Open the installed iPhone or Android app for sensor scanning. You can create, import, and edit rooms here.' };
  try { return await RoomScanner.capabilities(); }
  catch { return { supported: false, platform: Capacitor.getPlatform(), mode: 'unavailable', reason: 'The native scanner is not available in this build. Sync and rebuild the native app.' }; }
}
export async function scanRoom(name: string, height: number) {
  const { capture } = await RoomScanner.scan({ height });
  return normalizeCapture(capture, name);
}
