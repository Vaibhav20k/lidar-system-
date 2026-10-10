import { openDB, type DBSchema } from 'idb';
import { parseRoom, type Room } from '../domain/room';
interface RoomDB extends DBSchema { rooms: { key: string; value: Room }; sync: { key: string; value: { revision: number; server: string } }; }
let connection: ReturnType<typeof openDB<RoomDB>> | undefined;
const database = () => connection ??= openDB<RoomDB>('roomform-v1', 1, { upgrade(db) { db.createObjectStore('rooms', { keyPath: 'id' }); db.createObjectStore('sync'); } });
const portable = (room: Room) => ({ ...room, originalUsdzPath: undefined });
export async function listRooms() { const db = await database(); const rooms = await db.getAll('rooms'); return rooms.map(parseRoom).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)); }
export async function saveRoom(room: Room) { const validated = parseRoom(room); await (await database()).put('rooms', validated); }
export async function deleteRoom(id: string) { const db = await database(); const tx = db.transaction(['rooms', 'sync'], 'readwrite'); await tx.objectStore('rooms').delete(id); await tx.objectStore('sync').delete(id); await tx.done; }
export async function syncRoom(room: Room, server: string, token: string) {
  const db = await database(); const previous = await db.get('sync', room.id);
  const revision = previous?.server === server ? previous.revision : 0;
  const response = await fetch(`${server}/api/rooms/${encodeURIComponent(room.id)}`, { method: 'PUT', signal: AbortSignal.timeout(15000), headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify({ room: portable(room), revision }) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Could not sync this room.');
  await db.put('sync', { revision: result.revision, server }, room.id);
  return result.revision as number;
}
export async function pullRooms(server: string, token: string): Promise<Room[]> {
  const response = await fetch(`${server}/api/rooms`, { signal: AbortSignal.timeout(15000), headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Could not load server rooms.');
  if (!Array.isArray(result.rooms) || result.rooms.length > 500) throw new Error('Invalid server room list.');
  const records = result.rooms.map((record: { room: unknown; revision: unknown }) => {
    if (!Number.isSafeInteger(record.revision) || Number(record.revision) < 1) throw new Error('Invalid server revision.');
    return { room: parseRoom(record.room), revision: Number(record.revision) };
  });
  const db = await database(), imported: Room[] = [], tx = db.transaction(['rooms', 'sync'], 'readwrite');
  for (const { room, revision } of records) {
    const local = await tx.objectStore('rooms').get(room.id);
    const differs = local && JSON.stringify(portable(local)) !== JSON.stringify(portable(room));
    // Preserve local edits as a separate room; retain the server identity and revision for future sync.
    if (local && differs) {
      const copy = { ...local, id: crypto.randomUUID(), name: `${local.name.slice(0, 80)} (local copy)` };
      await tx.objectStore('rooms').put(copy);
    }
    const next = { ...room, originalUsdzPath: !differs ? local?.originalUsdzPath : undefined };
    await tx.objectStore('rooms').put(next); await tx.objectStore('sync').put({ revision, server }, room.id); imported.push(next);
  }
  await tx.done;
  return imported;
}
