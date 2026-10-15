import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { createReadStream, existsSync } from 'node:fs';
import { resolve, sep } from 'node:path';

// LiDAR point clouds are made from private scans and run to many MB, so they live in .data/clouds and are served
// to the local editor only. Keeping them out of public/ keeps them out of dist/ and so out of the phone app.
function localClouds(): Plugin {
  const folder = resolve('.data/clouds');
  const serve = (server: { middlewares: { use: (path: string, handler: (req: { url?: string }, res: NodeJS.WritableStream & { setHeader: (k: string, v: string) => void }, next: () => void) => void) => void } }) => {
    // Registered directly (not returned) so Vite does not mistake it for a post-middleware hook.
    server.middlewares.use('/clouds', (req, res, next) => {
      const file = resolve(folder, decodeURIComponent((req.url ?? '').split('?')[0].replace(/^\//, '')));
      if (!file.startsWith(folder + sep) || !file.endsWith('.bin') || !existsSync(file)) { next(); return; }
      res.setHeader('Content-Type', 'application/octet-stream');
      createReadStream(file).pipe(res);
    });
  };
  return { name: 'local-clouds', configureServer: serve, configurePreviewServer: serve };
}

export default defineConfig({
  plugins: [react(), localClouds()],
  server: { port: 5173, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8787' } },
  build: { rolldownOptions: { output: { manualChunks: (id: string) => id.includes('/three/') ? 'three' : undefined } } },
});
