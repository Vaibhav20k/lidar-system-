import express from 'express';
import { DatabaseSync } from 'node:sqlite';
import { timingSafeEqual } from 'node:crypto';
import { parseRoom } from '../src/domain/room';
import { mountScans } from './scans';

export function createApp(database: string, token = '', scanDirectory?: string) {
  const db = new DatabaseSync(database);
  db.exec('PRAGMA journal_mode = WAL; CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL)');
  const app = express(); app.disable('x-powered-by');
  const allowed = new Set(['http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:4173', 'http://127.0.0.1:4173', 'capacitor://localhost', 'http://localhost', 'https://localhost', ...(process.env.ROOMFORM_ALLOWED_ORIGINS ?? '').split(',').filter(Boolean)]);
  app.use((req, res, next) => {
    const origin = req.headers.origin;
    if (origin && !allowed.has(origin)) { res.status(403).json({ error: 'Origin is not allowed.' }); return; }
    if (origin) { res.setHeader('Access-Control-Allow-Origin', origin); res.setHeader('Vary', 'Origin'); }
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization'); res.setHeader('Access-Control-Allow-Methods', 'GET, PUT, POST, OPTIONS'); res.setHeader('Cache-Control', 'no-store'); res.setHeader('X-Content-Type-Options', 'nosniff');
    if (req.method === 'OPTIONS') { res.sendStatus(204); return; }
    if (token) {
      const supplied = Buffer.from((req.headers.authorization ?? '').replace(/^Bearer /, '')), expected = Buffer.from(token);
      if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) { res.status(401).json({ error: 'A valid access token is required.' }); return; }
    }
    next();
  });
  app.use(express.json({ limit: '5mb' }));
  app.get('/api/health', (_req, res) => res.json({ status: 'ok', schemaVersion: 1 }));
  app.get('/api/rooms', (_req, res) => {
    const rows = db.prepare('SELECT body, revision FROM rooms ORDER BY updated_at DESC LIMIT 500').all();
    res.json({ rooms: rows.map(r => ({ room: JSON.parse(String(r.body)), revision: r.revision })) });
  });
  app.get('/api/rooms/:id', (req, res) => {
    const row = db.prepare('SELECT body, revision FROM rooms WHERE id = ?').get(req.params.id);
    if (!row) { res.status(404).json({ error: 'Room not found.' }); return; }
    res.json({ room: JSON.parse(String(row.body)), revision: row.revision });
  });
  app.put('/api/rooms/:id', (req, res) => {
    let room;
    try { room = parseRoom(req.body?.room); } catch { res.status(400).json({ error: 'Invalid room geometry or schema version.' }); return; }
    if (room.id !== req.params.id || !Number.isSafeInteger(req.body.revision) || req.body.revision < 0) { res.status(400).json({ error: 'Room ID and revision must match the request.' }); return; }
    db.exec('BEGIN IMMEDIATE');
    try {
      const existing = db.prepare('SELECT revision FROM rooms WHERE id = ?').get(room.id);
      const revision = Number(existing?.revision ?? 0);
      if (req.body.revision !== revision) { db.exec('ROLLBACK'); res.status(409).json({ error: 'The server has a newer version. Pull rooms to review it before syncing again.', revision }); return; }
      const next = revision + 1, body = JSON.stringify({ ...room, originalUsdzPath: undefined });
      db.prepare('INSERT INTO rooms (id, body, revision, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body, revision = excluded.revision, updated_at = excluded.updated_at').run(room.id, body, next, new Date().toISOString());
      db.exec('COMMIT'); res.json({ revision: next });
    } catch (e) { db.exec('ROLLBACK'); throw e; }
  });
  if (scanDirectory) mountScans(app, scanDirectory);
  app.use((_req, res) => res.status(404).json({ error: 'Endpoint not found.' }));
  app.use((err: Error & { status?: number }, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    const status = err.status === 413 ? 413 : err instanceof SyntaxError ? 400 : 500;
    res.status(status).json({ error: status === 413 ? 'Room payload is too large.' : status === 400 ? 'Invalid JSON.' : 'Storage request failed.' });
  });
  return { app, close: () => db.close() };
}
