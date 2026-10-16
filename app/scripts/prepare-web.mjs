// Copies ../web into ./web for packaging; the remote API is the fallback value in config.js
// (the app's local server overrides it at runtime anyway).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '../../web');
const dst = path.resolve(here, '../web');
fs.rmSync(dst, { recursive: true, force: true });
fs.cpSync(src, dst, { recursive: true });
fs.writeFileSync(path.join(dst, 'config.js'), "// Overridden by the wrapper's local server; this value is only the fallback.\nwindow.WL_API = 'https://www.depes.ch/wl-api';\n");
console.log('web ->', dst);
