// Never mutate a stored room or silently reinterpret unsupported experimental
// geometry. Legacy wall/ceiling geometry remains available as originally saved.
export function migrateRoom(value: unknown): unknown {
  if (!value || typeof value!=='object' || Array.isArray(value)) return value;
  const room=value as Record<string,unknown>;
  if ('structure' in room) throw new Error('Experimental structure output needs conversion to the versioned structures format before import.');
  if (room.schemaVersion===1) return {...room,schemaVersion:2};
  return value;
}
