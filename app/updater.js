import { app, shell } from 'electron';
import { createRequire } from 'node:module';
import { latestRelease } from './release.js';

const require = createRequire(import.meta.url);
const SIX_HOURS = 6 * 60 * 60 * 1000;

// Windows: real auto-update through electron-updater (download in the background, install on restart).
// macOS: Squirrel.Mac only accepts apps signed with a paid Apple certificate, so we notify and
// offer the download instead.
export class Updater {
  constructor({ onChange }) {
    this.onChange = onChange;
    this.status = { state: 'idle' };
    this.native = process.platform === 'win32' && app.isPackaged;
    if (this.native) {
      const { autoUpdater } = require('electron-updater');
      this.au = autoUpdater;
      autoUpdater.autoDownload = true;
      autoUpdater.autoInstallOnAppQuit = true;
      autoUpdater.on('update-available', (i) => this.set({ state: 'downloading', version: i.version, percent: 0 }));
      autoUpdater.on('download-progress', (p) => this.set({ ...this.status, state: 'downloading', percent: Math.round(p.percent) }));
      autoUpdater.on('update-downloaded', (i) => this.set({ state: 'ready', version: i.version }));
      autoUpdater.on('update-not-available', () => this.set({ state: 'current' }));
      autoUpdater.on('error', (e) => this.set({ state: 'error', message: e?.message || String(e) }));
    }
  }

  set(status) {
    this.status = status;
    this.onChange?.(status);
  }

  // The update loop: first check 10 s after start, then every 6 hours.
  schedule() {
    clearTimeout(this.first);
    clearInterval(this.timer);
    this.first = setTimeout(() => this.check(false), 10000);
    this.timer = setInterval(() => this.check(false), SIX_HOURS);
  }

  async check(manual) {
    if (['downloading', 'ready'].includes(this.status.state)) return this.status;
    try {
      if (this.native) {
        await this.au.checkForUpdates();
      } else {
        const found = await latestRelease(app.getVersion());
        this.set(found ? { state: 'available', ...found } : { state: 'current' });
      }
    } catch (e) {
      // Background checks fail quietly (offline etc.); manual ones report the error.
      this.set(manual ? { state: 'error', message: e.message } : { state: 'idle' });
    }
    return this.status;
  }

  install() {
    if (this.status.state === 'ready' && this.native) this.au.quitAndInstall();
    else if (this.status.url) shell.openExternal(this.status.url);
  }
}
