import { app, BrowserWindow, Menu, shell } from 'electron';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Updater } from './updater.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.join(here, 'web');
// The page is served from 127.0.0.1 (the API only allows that origin besides fmatsch.ist), data comes from the public API.
const API = process.env.WL_API || 'https://www.depes.ch/wl-api';

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml',
};

if (!app.requestSingleInstanceLock()) app.quit();

let win = null;
let updater = null;
let port = 0;

function appJson() {
  return JSON.stringify({ app: 'electron', platform: process.platform, version: app.getVersion(), update: updater?.status || { state: 'idle' } });
}

async function handle(req, res) {
  const url = new URL(req.url, 'http://127.0.0.1');
  const send = (code, body, type = 'application/json; charset=utf-8') => {
    res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-cache' });
    res.end(body);
  };
  if (url.pathname === '/_app.json') return send(200, appJson());
  if (req.method === 'POST' && url.pathname === '/_app/check') { await updater.check(true); return send(200, appJson()); }
  if (req.method === 'POST' && url.pathname === '/_app/install') { updater.install(); return send(200, appJson()); }
  if (url.pathname === '/config.js') return send(200, `window.WL_API = ${JSON.stringify(API)};\n`, MIME['.js']);
  const rel = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname);
  const file = path.normalize(path.join(WEB, rel));
  if (!file.startsWith(WEB + path.sep)) return send(403, '{}');
  try {
    send(200, await fs.readFile(file), MIME[path.extname(file)] || 'application/octet-stream');
  } catch {
    send(404, '{"error":"not found"}');
  }
}

// A fixed port keeps the page's origin (and with it the saved view/line selection) the same between starts;
// if it is taken we fall back to a random one.
function startServer() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => handle(req, res).catch(() => { res.writeHead(500); res.end(); }));
    srv.once('error', () => srv.listen(0, '127.0.0.1', () => resolve(srv.address().port)));
    srv.listen(47233, '127.0.0.1', () => resolve(srv.address().port));
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1180, height: 820, minWidth: 420, minHeight: 480, show: false,
    title: 'Wiener Linien Live', backgroundColor: '#f6f6f4', autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, sandbox: true },
  });
  win.once('ready-to-show', () => win.show());
  // external links (data source, GitHub) open in the browser, never inside the app window
  const local = (u) => u.startsWith(`http://127.0.0.1:${port}/`);
  win.webContents.setWindowOpenHandler(({ url }) => { if (!local(url)) shell.openExternal(url); return { action: 'deny' }; });
  win.webContents.on('will-navigate', (e, url) => { if (!local(url)) { e.preventDefault(); shell.openExternal(url); } });
  win.loadURL(`http://127.0.0.1:${port}/`);
  win.on('closed', () => { win = null; });
}

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{ role: 'appMenu' }] : []),
    { label: 'Datei', submenu: [
      { label: 'Nach Updates suchen …', click: () => updater.check(true) },
      { label: 'Projektseite', click: () => shell.openExternal('https://fmatsch.github.io/wienerlinien-live/') },
      isMac ? { role: 'close' } : { role: 'quit' },
    ] },
    { role: 'editMenu' },
    { label: 'Ansicht', submenu: [{ role: 'reload' }, { role: 'togglefullscreen' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'resetZoom' }] },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
app.on('window-all-closed', () => app.quit());
app.on('activate', () => { if (!win && port) createWindow(); });

app.whenReady().then(async () => {
  updater = new Updater({});
  port = await startServer();
  buildMenu();
  createWindow();
  updater.schedule();
});
