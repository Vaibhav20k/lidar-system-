import type express from 'express';
import { spawn } from 'node:child_process';
import { createWriteStream, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { parseRoom, type Room } from '../src/domain/room';

// A phone uploads a scan's raw bundle, then asks for it to be processed. The Python scripts in
// scripts/scan_processing do the work; the phone polls until the enriched room is ready.
type Job = { status: 'processing' | 'done' | 'failed'; stage: string; notes: string[]; room?: Room; error?: string };
const MAX_UPLOAD = 400 * 1024 * 1024;
const scanId = /^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$/;

function run(command: string, args: string[], env: NodeJS.ProcessEnv = process.env): Promise<string> {
  return new Promise((done, fail) => {
    const child = spawn(command, args, { env });
    let output = '';
    const collect = (chunk: Buffer) => { output = (output + chunk.toString()).slice(-4000); };
    child.stdout.on('data', collect); child.stderr.on('data', collect);
    child.on('error', fail);
    child.on('close', code => code === 0 ? done(output) : fail(new Error(output.trim().split('\n').pop() || `${command} exited with ${code}`)));
  });
}

function findBundle(folder: string): string | undefined {
  for (const name of readdirSync(folder, { withFileTypes: true })) {
    if (!name.isDirectory()) continue;
    const path = join(folder, name.name);
    if (existsSync(join(path, 'manifest.json')) && existsSync(join(path, 'frames.jsonl'))) return path;
  }
  return existsSync(join(folder, 'manifest.json')) ? folder : undefined;
}

export function mountScans(app: express.Express, dataDirectory: string) {
  const root = join(dataDirectory, 'scans'), scripts = resolve('scripts/scan_processing');
  const python = process.env.ROOMFORM_PYTHON || 'python3';
  // Looking for fixtures sends wall and ceiling photos to OpenRouter and costs money, so it must be switched on deliberately.
  const detect = process.env.ROOMFORM_DETECT === '1' && Boolean(process.env.OPENROUTER_API_KEY);
  // Walls fitted to LiDAR replace RoomPlan's squared corners; ROOMFORM_REFIT_WALLS=0 keeps RoomPlan's walls.
  const refitWalls = process.env.ROOMFORM_REFIT_WALLS !== '0';
  const jobs = new Map<string, Job>();
  mkdirSync(root, { recursive: true });

  app.put('/api/scans/:id/raw', async (req, res) => {
    const id = req.params.id;
    if (!scanId.test(id)) { res.status(400).json({ error: 'Invalid scan ID.' }); return; }
    const folder = join(root, id), archive = join(folder, 'raw.zip');
    rmSync(folder, { recursive: true, force: true }); mkdirSync(folder, { recursive: true });
    let received = 0;
    const limit = new Transform({ transform(chunk, _encoding, next) { received += chunk.length; next(received > MAX_UPLOAD ? new Error('too large') : null, chunk); } });
    try {
      await pipeline(req, limit, createWriteStream(archive));
      await run('unzip', ['-q', '-o', archive, '-d', join(folder, 'bundle')]);
      rmSync(archive, { force: true });
      if (!findBundle(join(folder, 'bundle'))) throw new Error('no manifest');
      res.json({ received });
    } catch (e) {
      rmSync(folder, { recursive: true, force: true });
      res.status(e instanceof Error && e.message === 'too large' ? 413 : 400).json({ error: 'The upload is not a readable raw scan bundle.' });
    }
  });

  app.post('/api/scans/:id/process', (req, res) => {
    const id = req.params.id, folder = join(root, id);
    if (!scanId.test(id)) { res.status(400).json({ error: 'Invalid scan ID.' }); return; }
    const bundle = existsSync(join(folder, 'bundle')) ? findBundle(join(folder, 'bundle')) : undefined;
    if (!bundle) { res.status(409).json({ error: 'Upload the raw bundle for this scan first.' }); return; }
    let room: Room;
    try { room = parseRoom(req.body?.room); } catch { res.status(400).json({ error: 'Invalid room.' }); return; }
    if (!req.body?.capture || typeof req.body.capture !== 'object') { res.status(400).json({ error: 'The capture is missing.' }); return; }
    if (jobs.get(id)?.status === 'processing') { res.status(202).json({ status: 'processing' }); return; }
    const capture = join(folder, 'capture.json'), plain = join(folder, 'room.json'), withCeiling = join(folder, 'room-ceiling.json'), refitted = join(folder, 'room-refit.json'), full = join(folder, 'room-full.json'), found = join(folder, 'detect'), walled = join(folder, 'room-walls.json'), levelled = join(folder, 'room-floor.json');
    writeFileSync(capture, JSON.stringify(req.body.capture)); writeFileSync(plain, JSON.stringify({ ...room, originalUsdzPath: undefined }));
    const job: Job = { status: 'processing', stage: 'Measuring the ceiling', notes: [] };
    jobs.set(id, job);
    res.status(202).json({ status: 'processing' });
    void (async () => {
      let latest = plain;
      // A ceiling that cannot be measured must not stop the rest: the room is still usable without it.
      try { await run(python, [join(scripts, 'ceiling.py'), capture, bundle, plain, withCeiling]); latest = withCeiling; job.notes.push('Ceiling measured from depth.'); }
      catch (e) { job.notes.push(`Ceiling not measured: ${e instanceof Error ? e.message : 'unknown error'}`); }
      // RoomPlan's furniture boxes run wide; pull broad-fronted furniture in to the edges seen on the photos.
      job.stage = 'Fitting furniture to its real edges';
      try { await run(python, [join(scripts, 'refit_objects.py'), capture, bundle, latest, refitted]); latest = refitted; job.notes.push('Furniture fronts fitted to photo edges.'); }
      catch (e) { job.notes.push(`Furniture not refitted: ${e instanceof Error ? e.message : 'unknown error'}`); }
      if (detect) {
        job.stage = 'Looking for switches, lights, holes and AC units';
        try {
          await run(python, [join(scripts, 'detect.py'), capture, bundle, found, ...(latest === withCeiling ? ['--room', withCeiling] : [])]);
          await run(python, [join(scripts, 'apply_fixtures.py'), latest, join(found, 'fixtures.json'), full]);
          latest = full; job.notes.push('Fixtures found on scan photos.');
        } catch (e) { job.notes.push(`Fixtures not detected: ${e instanceof Error ? e.message : 'unknown error'}`); }
      } else job.notes.push('Fixture detection is switched off on this server.');
      // These two change the walls and heights the steps above measured against, so they run last.
      if (refitWalls) {
        job.stage = 'Fitting walls and corners to the scan';
        try { await run(python, [join(scripts, 'refit_walls.py'), capture, bundle, latest, walled]); latest = walled; job.notes.push('Walls and corners fitted to LiDAR.'); }
        catch (e) { job.notes.push(`Walls not refitted: ${e instanceof Error ? e.message : 'unknown error'}`); }
      }
      job.stage = 'Measuring heights from the real floor';
      try { await run(python, [join(scripts, 'floor_level.py'), capture, bundle, latest, levelled]); latest = levelled; job.notes.push('Heights measured from the LiDAR floor.'); }
      catch (e) { job.notes.push(`Floor not measured: ${e instanceof Error ? e.message : 'unknown error'}`); }
      try {
        // Keep the phone's own identity and timestamps; take only what the scripts added.
        const result = parseRoom(JSON.parse(readFileSync(latest, 'utf8')));
        job.room = parseRoom({ ...room, originalUsdzPath: undefined, walls: result.walls, floor: result.floor, openings: result.openings, objects: result.objects,
          ceiling: result.ceiling, fixtures: result.fixtures, warnings: result.warnings, updatedAt: new Date().toISOString() });
        job.status = 'done'; job.stage = 'Done';
      } catch { job.status = 'failed'; job.error = 'The processed room was not valid.'; }
    })();
  });

  app.get('/api/scans/:id', (req, res) => {
    const job = scanId.test(req.params.id) ? jobs.get(req.params.id) : undefined;
    if (!job) { res.status(404).json({ error: 'No processing job for this scan.' }); return; }
    res.json(job);
  });
}
