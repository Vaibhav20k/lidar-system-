import { existsSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { createApp } from './app';
if (existsSync('.env')) process.loadEnvFile('.env');
const host = process.env.ROOMFORM_HOST || '127.0.0.1', port = Number(process.env.ROOMFORM_PORT || 8787), token = process.env.ROOMFORM_API_TOKEN || '';
if (!['127.0.0.1', 'localhost', '::1'].includes(host) && token.length < 24) throw new Error('Set ROOMFORM_API_TOKEN to at least 24 characters before exposing the API to a network.');
const directory = resolve(process.env.ROOMFORM_DATA_DIR || '.data'); mkdirSync(directory, { recursive: true });
const { app, close } = createApp(resolve(directory, 'rooms.sqlite'), token);
const server = app.listen(port, host, () => process.stdout.write(`Roomform API ready at http://${host}:${port}\n`));
function shutdown() { server.close(() => { close(); process.exit(0); }); }
process.on('SIGINT', shutdown); process.on('SIGTERM', shutdown);
