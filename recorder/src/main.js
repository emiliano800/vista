// Electron main process: owns the Recorder, the always-on-top overlay pill, the
// employee dashboard, a hidden screen-capture window (webm video) and the tray.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { BrowserWindow, Menu, Tray, app, clipboard, desktopCapturer, ipcMain, nativeImage, screen, session, shell } from 'electron';

import { DEMO_KEYS, DemoHook, demoActiveWindow } from './demo.js';
import { DEFAULT_SETTINGS, Recorder, keyNamesFrom, loadSettings } from './recorder.js';
import { redactText } from './redact.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI = path.join(__dirname, '..', 'ui');
const PRELOAD = path.join(__dirname, 'preload.cjs');
const DEMO = process.argv.includes('--demo');
const HOME = process.env.VISTA_HOME ?? path.join(os.homedir(), 'Vista');
const RECORDINGS = path.join(HOME, 'recordings');
const SETTINGS_FILE = path.join(HOME, 'settings.json');

fs.mkdirSync(RECORDINGS, { recursive: true });

let overlay, dashboard, captureWin, tray, recorder;
let videoDone = null;

// ---- recorder wiring -------------------------------------------------------

async function buildRecorder() {
  const settings = loadSettings(SETTINGS_FILE);
  let hook = null;
  let activeWindow = null;
  let keyNames = new Map();
  if (DEMO) {
    hook = new DemoHook();
    activeWindow = demoActiveWindow();
    keyNames = DEMO_KEYS;
  } else {
    try {
      const m = await import('uiohook-napi');
      const { uIOhook, UiohookKey } = m.default ?? m;
      hook = uIOhook;
      keyNames = keyNamesFrom(UiohookKey);
    } catch (err) {
      console.error('uiohook-napi unavailable, input will not be recorded:', err.message);
    }
    try {
      activeWindow = (await import('get-windows')).activeWindow;
    } catch (err) {
      console.error('get-windows unavailable, foreground app will not be recorded:', err.message);
    }
  }
  const rec = new Recorder({
    root: RECORDINGS,
    settings,
    hook,
    activeWindow,
    keyNames,
    readClipboard: () => clipboard.readText(),
    frameProvider: grabFrame,
  });
  rec.on('status', broadcastStatus);
  rec.on('finished', postProcess);
  return rec;
}

async function grabFrame() {
  const { width } = screen.getPrimaryDisplay().size;
  const scale = Math.min(1, 1280 / width);
  const sources = await desktopCapturer.getSources({
    types: ['screen'],
    thumbnailSize: { width: Math.round(width * scale), height: Math.round(screen.getPrimaryDisplay().size.height * scale) },
  });
  const img = sources[0]?.thumbnail;
  return img && !img.isEmpty() ? img.toJPEG(70) : null;
}

function broadcastStatus(status) {
  for (const w of [overlay, dashboard]) if (w && !w.isDestroyed()) w.webContents.send('rec:status', status);
  updateTray(status);
}

// After a recording ends: run the taskmining pipeline if it's reachable so the
// dashboard can show steps/cases/open questions. Raw events are never modified.
function postProcess(manifest) {
  const dir = path.join(RECORDINGS, manifest.recording_id);
  const repo = findRepoRoot();
  const write = (patch) => {
    const m = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
    fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify({ ...m, ...patch }, null, 2));
    broadcastRecordings();
  };
  if (!repo) return write({ processing: 'skipped', processing_note: 'taskmining package not found; run `python -m taskmining run --input events.jsonl` later' });
  const args = ['-m', 'taskmining', 'run', '--input', path.join(dir, 'events.jsonl'), '--out', path.join(dir, 'processed')];
  if (fs.existsSync(path.join(dir, 'annotations.jsonl'))) args.push('--annotations', path.join(dir, 'annotations.jsonl'));
  write({ processing: 'running' });
  const py = spawn(process.env.VISTA_PYTHON ?? 'python3', args, { cwd: repo });
  let err = '';
  py.stderr.on('data', (d) => (err += d));
  py.on('error', (e) => write({ processing: 'failed', processing_note: e.message }));
  py.on('close', (code) => {
    if (code !== 0) return write({ processing: 'failed', processing_note: err.trim().split('\n').pop() });
    let summary = null;
    try {
      summary = pickSummary(JSON.parse(fs.readFileSync(path.join(dir, 'processed', 'summary.json'), 'utf8')));
    } catch (e) {
      return write({ processing: 'failed', processing_note: `summary unreadable: ${e.message}` });
    }
    write({ processing: 'done', summary });
  });
}

function pickSummary(s) {
  return {
    raw_events: s.n_raw_events,
    clean_events: s.n_clean_events,
    steps: s.n_steps,
    cases: s.n_cases,
    annotations: s.n_annotations,
    open_questions: s.n_open_questions,
    off_screen_hours: s.off_screen_hours,
    top_activities: (s.activities ?? []).slice(0, 6),
    automation: (s.automation_potential ?? []).slice(0, 3),
    top_variant: s.variants?.[0] ?? null,
    rework: Object.entries(s.rework ?? {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([activity, count]) => ({ activity, count })),
  };
}

function findRepoRoot() {
  if (process.env.VISTA_REPO) return process.env.VISTA_REPO;
  let d = __dirname;
  for (let i = 0; i < 5; i++) {
    if (fs.existsSync(path.join(d, 'taskmining', '__main__.py'))) return d;
    d = path.dirname(d);
  }
  return null;
}

// ---- recordings store -------------------------------------------------------

function listRecordings() {
  const out = [];
  for (const id of fs.readdirSync(RECORDINGS)) {
    const f = path.join(RECORDINGS, id, 'manifest.json');
    if (!fs.existsSync(f)) continue;
    try {
      const m = JSON.parse(fs.readFileSync(f, 'utf8'));
      let annotations = 0;
      try {
        annotations = fs.readFileSync(path.join(RECORDINGS, id, 'annotations.jsonl'), 'utf8').trim().split('\n').filter(Boolean).length;
      } catch {
        /* none */
      }
      out.push({ ...m, dir: path.join(RECORDINGS, id), annotations });
    } catch {
      /* corrupt manifest */
    }
  }
  return out.sort((a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? ''));
}

function broadcastRecordings() {
  if (dashboard && !dashboard.isDestroyed()) dashboard.webContents.send('recordings:changed', listRecordings());
}

// Employee annotation: what the screen couldn't see (calls, paper, meetings).
// Same JSONL shape as taskmining.annotations.read_annotations.
function addAnnotation(recordingId, { label, note = '', start, end, case_id = '' }) {
  const dir = path.join(RECORDINGS, recordingId);
  const m = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  const row = {
    user: m.user,
    start: start ?? m.started_at,
    end: end ?? m.ended_at ?? new Date().toISOString(),
    label: redactText(label),
    note: redactText(note),
    case_id,
    author: 'employee',
  };
  fs.appendFileSync(path.join(dir, 'annotations.jsonl'), JSON.stringify(row) + '\n');
  if (m.ended_at) postProcess(m);
  else broadcastRecordings();
  return row;
}

// ---- windows -----------------------------------------------------------------

// Overlay sizes: `orb` is the idle blue circle, `pill` the recording bar, `panel` the expanded details.
const SIZES = { orb: { w: 64, h: 64 }, pill: { w: 380, h: 64 }, panel: { w: 380, h: 332 } };
let overlayMode = 'orb';

function setOverlayMode(mode) {
  if (!overlay || overlay.isDestroyed()) return;
  const from = SIZES[overlayMode], to = SIZES[mode];
  if (!to) return;
  const [x, y] = overlay.getPosition();
  // keep the pill centred on where the orb was
  const nx = Math.round(x + (from.w - to.w) / 2);
  overlay.setBounds({ x: Math.max(0, nx), y, width: to.w, height: to.h });
  overlayMode = mode;
}

function createOverlay() {
  const { workArea } = screen.getPrimaryDisplay();
  overlay = new BrowserWindow({
    width: SIZES.orb.w,
    height: SIZES.orb.h,
    x: Math.round(workArea.x + (workArea.width - SIZES.orb.w) / 2),
    y: workArea.y + 12,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: true,
    hasShadow: false,
    show: false,
    webPreferences: { preload: PRELOAD, contextIsolation: true, sandbox: false },
  });
  overlay.setAlwaysOnTop(true, 'screen-saver');
  overlay.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  overlay.loadFile(path.join(UI, 'overlay.html'));
  overlay.once('ready-to-show', () => overlay.show());
}

// After Stop: bring the dashboard back on the review screen for this recording,
// even if the window has to be created (and finish loading) first.
function openReview(recordingId) {
  const fresh = createDashboard();
  const send = () => dashboard.webContents.send('dashboard:review', recordingId);
  if (fresh) dashboard.webContents.once('did-finish-load', send);
  else send();
}

function createDashboard() {
  if (dashboard && !dashboard.isDestroyed()) {
    dashboard.show();
    dashboard.focus();
    return false;
  }
  dashboard = new BrowserWindow({
    width: 1080,
    height: 760,
    minWidth: 820,
    minHeight: 560,
    title: 'Vista',
    backgroundColor: '#F4F4F1',
    autoHideMenuBar: true,
    webPreferences: { preload: PRELOAD, contextIsolation: true, sandbox: false },
  });
  dashboard.loadFile(path.join(UI, 'dashboard.html'));
  return true;
}

function createCaptureWindow() {
  captureWin = new BrowserWindow({ show: false, webPreferences: { preload: PRELOAD, contextIsolation: true, sandbox: false } });
  captureWin.loadFile(path.join(UI, 'capture.html'));
  session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
    desktopCapturer.getSources({ types: ['screen'] }).then((sources) => callback({ video: sources[0] }));
  });
}

function updateTray(status) {
  if (!tray) return;
  const rec = status.state === 'recording';
  tray.setToolTip(rec ? `Vista — recording ${status.current.app}` : `Vista — ${status.state}`);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: rec ? 'Pause recording' : status.state === 'paused' ? 'Resume recording' : 'Start recording', click: () => toggle() },
      { label: 'Stop recording', enabled: status.state !== 'idle', click: () => stopRecording() },
      { type: 'separator' },
      { label: 'Open dashboard', click: () => createDashboard() },
      { label: 'Open recordings folder', click: () => shell.openPath(RECORDINGS) },
      { type: 'separator' },
      { label: 'Quit Vista', click: () => app.quit() },
    ])
  );
}

function trayIcon() {
  // 16x16 accent dot, drawn in code so no asset pipeline is needed
  const size = 16;
  const png = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x - 7.5, y - 7.5);
      const i = (y * size + x) * 4;
      const a = d < 6 ? 255 : d < 7 ? Math.round((7 - d) * 255) : 0;
      png[i] = 0x3d;
      png[i + 1] = 0x63;
      png[i + 2] = 0xdd;
      png[i + 3] = a;
    }
  return nativeImage.createFromBitmap(png, { width: size, height: size });
}

// ---- actions ----------------------------------------------------------------

function toggle() {
  if (recorder.state === 'idle') return startRecording();
  if (recorder.state === 'recording') return recorder.pause();
  if (recorder.state === 'paused') return recorder.resume();
  return recorder.status();
}

function startRecording() {
  const status = recorder.start();
  if (dashboard && !dashboard.isDestroyed()) dashboard.hide();
  setOverlayMode('pill');
  if (recorder.settings.video && captureWin) {
    fs.writeFileSync(path.join(recorder.dir, 'screen.webm'), '');
    captureWin.webContents.send('video:start', { dir: recorder.dir, fps: 2 });
  }
  return status;
}

async function stopRecording() {
  if (recorder.state === 'idle') return recorder.status();
  if (recorder.settings.video && captureWin) {
    videoDone = new Promise((res) => ipcMain.once('video:done', () => res()));
    captureWin.webContents.send('video:stop');
    await Promise.race([videoDone, new Promise((r) => setTimeout(r, 3000))]);
  }
  const status = await recorder.stop();
  broadcastRecordings();
  setOverlayMode('orb');
  openReview(status.recordingId);
  return status;
}

// ---- IPC ----------------------------------------------------------------------

ipcMain.handle('rec:start', () => startRecording());
ipcMain.handle('rec:pause', () => recorder.pause());
ipcMain.handle('rec:resume', () => recorder.resume());
ipcMain.handle('rec:stop', () => stopRecording());
ipcMain.handle('rec:toggle', () => toggle());
ipcMain.handle('rec:status', () => recorder.status());
ipcMain.handle('recordings:list', () => listRecordings());
ipcMain.handle('recordings:open', (_e, id) => shell.openPath(id ? path.join(RECORDINGS, id) : RECORDINGS));
ipcMain.handle('recordings:annotate', (_e, id, ann) => addAnnotation(id, ann));
ipcMain.handle('dashboard:open', () => createDashboard());
ipcMain.handle('overlay:resize', (_e, mode) => setOverlayMode(mode));
ipcMain.handle('settings:get', () => recorder.settings);
ipcMain.handle('settings:set', (_e, patch) => {
  recorder.settings = { ...recorder.settings, ...patch };
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(recorder.settings, null, 2));
  broadcastStatus(recorder.status());
  return recorder.settings;
});
ipcMain.handle('settings:defaults', () => DEFAULT_SETTINGS);
ipcMain.handle('app:info', () => ({ demo: DEMO, home: HOME, platform: process.platform, user: os.userInfo().username }));
ipcMain.on('video:chunk', (_e, dir, buf) => {
  try {
    fs.appendFileSync(path.join(dir, 'screen.webm'), Buffer.from(buf));
  } catch {
    /* recording dir gone */
  }
});

// ---- app ----------------------------------------------------------------------

app.commandLine.appendSwitch('enable-transparent-visuals');
app.whenReady().then(async () => {
  recorder = await buildRecorder();
  tray = new Tray(trayIcon());
  updateTray(recorder.status());
  tray.on('click', () => createDashboard());
  createOverlay();
  createCaptureWindow();
  if (process.argv.includes('--dashboard')) createDashboard();
});

app.on('window-all-closed', () => {
  /* keep running in the tray/overlay */
});
app.on('before-quit', async (e) => {
  if (recorder && recorder.state !== 'idle') {
    e.preventDefault();
    await stopRecording();
    app.quit();
  }
});
