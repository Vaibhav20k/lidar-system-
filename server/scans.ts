import type express from 'express';
import { spawn } from 'node:child_process';
import { createWriteStream, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
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
    const timer=setTimeout(()=>{ child.kill('SIGKILL'); fail(new Error('Processing stage exceeded 30 minutes.')); },30*60*1000);
    timer.unref();
    let output = '';
    const collect = (chunk: Buffer) => { output = (output + chunk.toString()).slice(-4000); };
    child.stdout.on('data', collect); child.stderr.on('data', collect);
    child.on('error', error=>{clearTimeout(timer);fail(error);});
    child.on('close', code => {clearTimeout(timer);code === 0 ? done(output) : fail(new Error(output.trim().split('\n').pop() || `${command} exited with ${code}`));});
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
  const jobs = new Map<string, Job>();
  const uploads=new Set<string>();
  mkdirSync(root, { recursive: true });
  const persist=(id:string,job:Job)=>{
    const path=join(root,id,'job.json'),temporary=path+'.tmp';
    writeFileSync(temporary,JSON.stringify(job));renameSync(temporary,path);
  };
  for(const id of readdirSync(root).filter(id=>scanId.test(id))) {
    try {
      const job=JSON.parse(readFileSync(join(root,id,'job.json'),'utf8')) as Job;
      if(job.status==='processing') {job.status='failed';job.stage='Interrupted';job.error='Server restarted. Original bundle is retained; retry processing.';persist(id,job);}
      if(job.room)job.room=parseRoom(job.room);
      jobs.set(id,job);
    } catch { /* No completed job metadata for this capture. */ }
  }

  app.put('/api/scans/:id/raw', async (req, res) => {
    const id = req.params.id;
    if (!scanId.test(id)) { res.status(400).json({ error: 'Invalid scan ID.' }); return; }
    if(uploads.has(id)||jobs.get(id)?.status==='processing') {res.status(409).json({error:'Capture is busy.'});return;}
    if(existsSync(join(root,id))) {res.status(409).json({error:'Original bundle already exists. Process the retained bundle or use a new capture ID.'});return;}
    uploads.add(id);
    const folder = join(root, `${id}-upload-${randomUUID()}`), archive = join(folder, 'raw.zip');
    mkdirSync(folder, { recursive: true });
    let received = 0;
    const limit = new Transform({ transform(chunk, _encoding, next) { received += chunk.length; next(received > MAX_UPLOAD ? new Error('too large') : null, chunk); } });
    try {
      await pipeline(req, limit, createWriteStream(archive));
      await run(python, [join(scripts,'unpack_bundle.py'), archive, join(folder,'bundle')]);
      rmSync(archive, { force: true });
      if (!findBundle(join(folder, 'bundle'))) throw new Error('no manifest');
      renameSync(folder,join(root,id));
      res.json({ received });
    } catch (e) {
      rmSync(folder, { recursive: true, force: true });
      res.status(e instanceof Error && e.message === 'too large' ? 413 : 400).json({ error: 'The upload is not a readable raw scan bundle.' });
    } finally {uploads.delete(id);}
  });

  app.post('/api/scans/:id/process', (req, res) => {
    const id = req.params.id, folder = join(root, id);
    if (!scanId.test(id)) { res.status(400).json({ error: 'Invalid scan ID.' }); return; }
    const bundle = existsSync(join(folder, 'bundle')) ? findBundle(join(folder, 'bundle')) : undefined;
    if (!bundle) { res.status(409).json({ error: 'Upload the raw bundle for this scan first.' }); return; }
    let room: Room;
    try { room = parseRoom(req.body?.room); } catch { res.status(400).json({ error: 'Invalid room.' }); return; }
    if(room.captureId && room.captureId!==id) {res.status(409).json({error:'Room capture identity does not match the bundle.'});return;}
    room={...room,captureId:id};
    if (!req.body?.capture || typeof req.body.capture !== 'object') { res.status(400).json({ error: 'The capture is missing.' }); return; }
    if (jobs.get(id)?.status === 'processing') { res.status(202).json({ status: 'processing' }); return; }
    const capture = join(folder, 'capture.json'), plain = join(folder, 'room.json'), withCeiling = join(folder, 'room-ceiling.json'), refitted = join(folder, 'room-refit.json'), full = join(folder, 'room-full.json'), found = join(folder, 'detect'), refined = join(folder, 'room-refined.json');
    writeFileSync(capture, JSON.stringify(req.body.capture)); writeFileSync(plain, JSON.stringify({ ...room, originalUsdzPath: undefined }));
    const job: Job = { status: 'processing', stage: 'Checking repeated views and fitting geometry', notes: [] };
    jobs.set(id, job);
    persist(id,job);
    const stage=(message:string)=>{job.stage=message;persist(id,job);};
    res.status(202).json({ status: 'processing' });
    void (async () => {
      let latest = plain;
      // RoomPlan's furniture boxes run wide; pull broad-fronted furniture in to the edges seen on the photos.
      stage('Fitting furniture to its real edges');
      try { await run(python, [join(scripts, 'refit_objects.py'), capture, bundle, latest, refitted]); latest = refitted; job.notes.push('Furniture fronts fitted to photo edges.'); }
      catch (e) { job.notes.push(`Furniture not refitted: ${e instanceof Error ? e.message : 'unknown error'}`); }
      if (detect) {
        stage('Looking for switches, lights, holes and AC units');
        try {
          // Geometry runs last because photo projections use the original capture datum.
          // Ceiling detection is withheld until a validated ceiling exists in that datum.
          await run(python, [join(scripts, 'detect.py'), capture, bundle, found]);
          await run(python, [join(scripts, 'apply_fixtures.py'), latest, join(found, 'fixtures.json'), full]);
          latest = full; job.notes.push('Fixtures found on scan photos.');
          // The detector's boxes are a little loose; measure the plates' real edges on the photos.
          try { await run(python, [join(scripts, 'refine_fixtures.py'), capture, bundle, full, refined]); latest = refined; job.notes.push('Plate edges measured on photos.'); }
          catch (e) { job.notes.push(`Plate edges not measured: ${e instanceof Error ? e.message : 'unknown error'}`); }
        } catch (e) { job.notes.push(`Fixtures not detected: ${e instanceof Error ? e.message : 'unknown error'}`); }
      } else job.notes.push('Fixture detection is switched off on this server.');
      stage('Checking repeated views and structural candidates');
      // No unvalidated floor/ceiling/wall correction is applied. Geometry QA is
      // entirely local and never uses fixture detection or tape targets.
      try {
        await run(python, [join(scripts, 'geometry.py'), capture, bundle, latest, withCeiling, '--report', join(folder, 'geometry-quality.json')]);
        latest = withCeiling;
        job.notes.push('Geometry checked against repeat and held-out views; review accuracy status.');
      } catch (e) {
        const fallback = { ...JSON.parse(readFileSync(latest, 'utf8')), measurementQuality: { version: 'geometry-qa-v1', status: 'needs-rescan', targetMeters: .02,
          reasons: ['Geometry quality check could not complete. Accuracy is not verified.'], applied: [] } };
        writeFileSync(plain, JSON.stringify(fallback)); latest = plain;
        job.notes.push(`Geometry not refined: ${e instanceof Error ? e.message : 'unknown error'}`);
      }
      try {
        // Keep the phone's own identity and timestamps; take only what the scripts added.
        const result = parseRoom(JSON.parse(readFileSync(latest, 'utf8')));
        job.room = parseRoom({ ...room, originalUsdzPath: undefined, walls: result.walls, floor: result.floor, openings: result.openings, objects: result.objects,
          structures:result.structures, measurementQuality: result.measurementQuality, ceiling: result.ceiling, fixtures: result.fixtures, warnings: result.warnings, updatedAt: new Date().toISOString() });
        job.status = 'done'; job.stage = 'Done';
      } catch { job.status = 'failed'; job.error = 'The processed room was not valid.'; }
    })().catch(error=>{job.status='failed';job.error=error instanceof Error?error.message:'Processing failed';}).finally(()=>persist(id,job));
  });

  app.get('/api/scans/:id', (req, res) => {
    const job = scanId.test(req.params.id) ? jobs.get(req.params.id) : undefined;
    if (!job) { res.status(404).json({ error: 'No processing job for this scan.' }); return; }
    res.json(job);
  });
}
