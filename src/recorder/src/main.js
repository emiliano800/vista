// Electron main process: owns the Recorder, the always-on-top overlay pill, the
// employee dashboard, a hidden screen-capture window (webm video) and the tray.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { BrowserWindow, Menu, Tray, app, clipboard, desktopCapturer, ipcMain, nativeImage, screen, session, shell, systemPreferences, safeStorage } from 'electron';

import { CONFIDENCE_THRESHOLD, RESOLVED_STATUSES, SESSION_ID, applyDecision, explainSection, openaiConfig, reviewSummary } from './explain.js';
import { DEMO_KEYS, DemoHook, demoActiveWindow, demoClipboard } from './demo.js';
import { DEFAULT_SETTINGS, Recorder, keyNamesFrom, loadSettings } from './recorder.js';
import { redactText } from './redact.js';
import { cloudRequest, companyID, uploadReport, workspaceURL } from './cloud.js';
import { buildSections, parseEvents, recordingName } from './sections.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI = path.join(__dirname, '..', 'ui');
const PRELOAD = path.join(__dirname, 'preload.cjs');
const DEMO = process.argv.includes('--demo');
const MAC = process.platform === 'darwin';
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
    readClipboard: DEMO ? demoClipboard : () => clipboard.readText(),
    frameProvider: grabFrame,
    thumbProvider: grabThumb,
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

// 64x36 grayscale fingerprint of the screen for change detection (~1 ms to compare)
async function grabThumb() {
  const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 64, height: 36 } });
  const img = sources[0]?.thumbnail;
  if (!img || img.isEmpty()) return null;
  const { width, height } = img.getSize();
  const bgra = img.toBitmap();
  const gray = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < gray.length; i++, p += 4) gray[i] = (bgra[p] * 29 + bgra[p + 1] * 150 + bgra[p + 2] * 77) >> 8;
  return { width, height, gray };
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
  const py = spawn(pythonFor(repo), args, { cwd: repo });
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

// Video sections: the long single-window stretches of a recording, computed
// once after Stop (and again when the employee asks) from events.jsonl.
function readEvents(dir) {
  try {
    return parseEvents(fs.readFileSync(path.join(dir, 'events.jsonl'), 'utf8'));
  } catch {
    return [];
  }
}

function readManifest(dir) {
  return JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
}

function readAnnotations(dir) {
  try {
    return parseEvents(fs.readFileSync(path.join(dir, 'annotations.jsonl'), 'utf8'));
  } catch {
    return [];
  }
}

// review.json: the AI's explanation per section (+ the whole session) and
// what the employee decided about each. Shape: { threshold, model, generated_at, items: { S1: {...}, session: {...} } }
const REVIEW_FILE = 'review.json';

function readReview(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, REVIEW_FILE), 'utf8'));
  } catch {
    return { threshold: CONFIDENCE_THRESHOLD, model: null, generated_at: null, generating: false, items: {} };
  }
}

function writeReview(dir, review) {
  fs.writeFileSync(path.join(dir, REVIEW_FILE), JSON.stringify(review, null, 2));
}

function sectionsFor(recordingId) {
  const dir = path.join(RECORDINGS, recordingId);
  const m = readManifest(dir);
  const events = readEvents(dir);
  const sections = buildSections(events, m);
  const anns = readAnnotations(dir);
  const review = readReview(dir);
  for (const s of sections) {
    // notes that cover each section so the UI can show "annotated"
    s.annotations = anns
      .filter((a) => a.scope !== 'session' && Date.parse(a.start) < Date.parse(s.end) && Date.parse(a.end) > Date.parse(s.start))
      .map((a) => ({ label: a.label, note: a.note, author: a.author }));
    s.review = review.items[s.id] ?? null;
  }
  const video = m.files?.video && fs.existsSync(path.join(dir, m.files.video)) ? path.join(dir, m.files.video) : null;
  return {
    recording_id: recordingId,
    video,
    video_url: video ? `file://${video}` : null,
    started_at: m.started_at,
    ended_at: m.ended_at,
    pauses: m.pauses ?? [],
    sections,
    shots_dir: `file://${path.join(dir, 'shots')}`,
    review: {
      enabled: !!openaiConfig(process.env, recorder.settings),
      generating: !!review.generating,
      generated_at: review.generated_at,
      model: review.model,
      session: review.items[SESSION_ID] ?? null,
      summary: reviewSummary(review.items, review.threshold ?? CONFIDENCE_THRESHOLD),
    },
  };
}

function broadcastSections(recordingId) {
  if (dashboard && !dashboard.isDestroyed()) dashboard.webContents.send('recordings:sections', sectionsFor(recordingId));
}

// Ask the model to explain every section plus the whole session. Items the
// employee has already resolved are never redone; `force` redoes the open
// ones. Runs after Stop when a key is configured, and on demand from the dashboard.
const explaining = new Set();
async function explainRecording(recordingId, { force = false } = {}) {
  const api = openaiConfig(process.env, recorder.settings);
  if (!api) return { error: 'no_key', message: 'Add an OpenAI API key under Settings → AI explanations (or set OPENAI_API_KEY).' };
  if (explaining.has(recordingId)) return sectionsFor(recordingId);
  explaining.add(recordingId);
  const dir = path.join(RECORDINGS, recordingId);
  const review = readReview(dir);
  review.generating = true;
  review.threshold = CONFIDENCE_THRESHOLD;
  review.model = api.model;
  writeReview(dir, review);
  broadcastSections(recordingId);
  try {
    const m = readManifest(dir);
    const events = readEvents(dir);
    const sections = buildSections(events, m);
    const ctx = { manifest: { ...m, annotations_preview: readAnnotations(dir).map((a) => `${a.label}${a.note ? ': ' + a.note : ''}`).slice(0, 8) }, sections, events };
    const targets = [...sections, { whole: true, id: SESSION_ID }];
    for (const s of targets) {
      const prev = review.items[s.id];
      if (prev && (RESOLVED_STATUSES.has(prev.status) || (!force && prev.status !== 'failed'))) continue;
      try {
        const r = await explainSection(s, ctx, api, { dir, screenshots: !!recorder.settings.clarifyScreenshots });
        delete r.prompt; // stays in main; the UI never needs it
        review.items[s.id] = r;
      } catch (e) {
        review.items[s.id] = { id: s.id, label: '', explanation: '', confidence: 0, unclear: [], questions: [], status: 'failed', error: e.message, at: new Date().toISOString() };
      }
      writeReview(dir, review);
      broadcastSections(recordingId);
    }
  } finally {
    review.generating = false;
    review.generated_at = new Date().toISOString();
    writeReview(dir, review);
    explaining.delete(recordingId);
    broadcastSections(recordingId);
  }
  return sectionsFor(recordingId);
}

// Employee decision on one explanation: approve / fix / explain. Writes the
// annotation (with the AI's version kept alongside for provenance) and
// updates review.json.
function decide(recordingId, itemId, action, body = {}) {
  const dir = path.join(RECORDINGS, recordingId);
  const review = readReview(dir);
  const { item, annotation } = applyDecision(review.items[itemId], action, body);
  review.items[itemId] = item;
  writeReview(dir, review);
  const whole = itemId === SESSION_ID;
  const m = readManifest(dir);
  const section = whole ? null : buildSections(readEvents(dir), m).find((s) => s.id === itemId);
  addAnnotation(recordingId, {
    ...annotation,
    start: whole ? m.started_at : section?.start,
    end: whole ? m.ended_at : section?.end,
    scope: whole ? 'session' : 'section',
    section_id: whole ? null : itemId,
  });
  return sectionsFor(recordingId);
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
    data_flows: (s.data_flows ?? []).slice(0, 5),
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
    if (fs.existsSync(path.join(d, 'src', 'taskmining', '__main__.py'))) return d;
    d = path.dirname(d);
  }
  return null;
}

// VISTA_PYTHON wins; otherwise the repo's uv/venv interpreter; otherwise whatever python3 is on PATH.
function pythonFor(repo) {
  if (process.env.VISTA_PYTHON) return process.env.VISTA_PYTHON;
  const venv = process.platform === 'win32' ? ['.venv', 'Scripts', 'python.exe'] : ['.venv', 'bin', 'python'];
  const p = path.join(repo, ...venv);
  return fs.existsSync(p) ? p : 'python3';
}

// ---- macOS permissions --------------------------------------------------------
// uiohook needs Accessibility + Input Monitoring, get-windows/desktopCapturer need
// Screen Recording. Input Monitoring has no query API, so it is reported as
// `unknown` and the hook's silence is the only signal.
const MAC_PANES = {
  accessibility: 'Privacy_Accessibility',
  inputMonitoring: 'Privacy_ListenEvent',
  screen: 'Privacy_ScreenCapture',
};

function permissions(prompt = false) {
  if (!MAC || DEMO) return { needed: false, ok: true, accessibility: 'granted', inputMonitoring: 'granted', screen: 'granted' };
  const accessibility = systemPreferences.isTrustedAccessibilityClient(prompt) ? 'granted' : 'denied';
  const screenAccess = systemPreferences.getMediaAccessStatus('screen');
  return {
    needed: true,
    accessibility,
    inputMonitoring: 'unknown',
    screen: screenAccess,
    ok: accessibility === 'granted' && screenAccess === 'granted',
  };
}

function openPermissionPane(kind) {
  const pane = MAC_PANES[kind];
  if (!pane) return;
  if (kind === 'screen') grabFrame().catch(() => {}); // registers the app in the Screen Recording list
  return shell.openExternal(`x-apple.systempreferences:com.apple.preference.security?${pane}`);
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
      out.push({ ...m, dir: path.join(RECORDINGS, id), annotations, name: m.name ?? recordingName(m, { summary: m.summary_text ?? '' }) });
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
// `scope` is 'section' (a video section), 'session' (whole-recording summary)
// or 'manual'. A session summary also renames the recording.
// `author` is 'employee' for anything typed, 'ai' for an approved AI
// explanation; `ai` keeps the model's version when the employee fixed it.
function addAnnotation(recordingId, { label, note = '', start, end, case_id = '', scope = 'manual', section_id = null, qa = [], author = 'employee', ai = null }) {
  const dir = path.join(RECORDINGS, recordingId);
  const m = readManifest(dir);
  const row = {
    user: m.user,
    start: start ?? m.started_at,
    end: end ?? m.ended_at ?? new Date().toISOString(),
    label: redactText(label),
    note: redactText(note),
    case_id,
    author,
    scope,
    section_id,
    qa: qa.map((x) => ({ q: redactText(x.q), a: redactText(x.a) })),
    ...(ai ? { ai } : {}),
  };
  fs.appendFileSync(path.join(dir, 'annotations.jsonl'), JSON.stringify(row) + '\n');
  if (scope === 'session') {
    const summary_text = row.note || row.label;
    const patched = { ...m, summary_text, name: recordingName(m, { summary: summary_text }) };
    fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(patched, null, 2));
  }
  if (m.ended_at) postProcess(readManifest(dir));
  else broadcastRecordings();
  return row;
}

// ---- windows -----------------------------------------------------------------

// Overlay sizes: `orb` is the idle blue circle, `pill` the recording bar, `panel` the expanded details.
const SIZES = { orb: { w: 104, h: 104 }, pill: { w: 380, h: 64 }, panel: { w: 380, h: 332 } };
let overlayMode = 'orb';

function setOverlayMode(mode) {
  if (!overlay || overlay.isDestroyed()) return;
  const from = SIZES[overlayMode], to = SIZES[mode];
  if (!to) return;
  const [x, y] = overlay.getPosition();
  // keep the pill centred on where the orb was (the orb window is oversized so its glow isn't clipped)
  const nx = Math.round(x + (from.w - to.w) / 2);
  const ny = overlayMode === 'orb' || mode === 'orb' ? Math.round(y + (from.h - to.h) / 2) : y;
  overlay.setBounds({ x: Math.max(0, nx), y: Math.max(0, ny), width: to.w, height: to.h });
  overlayMode = mode;
}

function createOverlay() {
  const { workArea } = screen.getPrimaryDisplay();
  overlay = new BrowserWindow({
    width: SIZES.orb.w,
    height: SIZES.orb.h,
    x: Math.round(workArea.x + (workArea.width - SIZES.orb.w) / 2),
    y: workArea.y,
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
  // 16x16 dot, drawn in code so no asset pipeline is needed. On macOS it is a
  // template image (black + alpha) so the menu bar tints it for light/dark mode.
  const size = 16;
  const png = Buffer.alloc(size * size * 4);
  const [r, g, b] = MAC ? [0, 0, 0] : [0x3d, 0x63, 0xdd];
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x - 7.5, y - 7.5);
      const i = (y * size + x) * 4;
      const a = d < 6 ? 255 : d < 7 ? Math.round((7 - d) * 255) : 0;
      png[i] = r;
      png[i + 1] = g;
      png[i + 2] = b;
      png[i + 3] = a;
    }
  const img = nativeImage.createFromBitmap(png, { width: size, height: size });
  if (MAC) img.setTemplateImage(true);
  return img;
}

// ---- actions ----------------------------------------------------------------

function toggle() {
  if (recorder.state === 'idle') return startRecording();
  if (recorder.state === 'recording') return pauseRecording();
  if (recorder.state === 'paused') return resumeRecording();
  return recorder.status();
}

function pauseRecording() {
  const status = recorder.pause();
  if (recorder.settings.video && captureWin) captureWin.webContents.send('video:pause');
  return status;
}

function resumeRecording() {
  const status = recorder.resume();
  if (recorder.settings.video && captureWin) captureWin.webContents.send('video:resume');
  return status;
}

function startRecording() {
  const perms = permissions(true);
  if (perms.needed && !perms.ok) {
    if (!createDashboard()) dashboard.webContents.send('permissions:changed', perms);
    return recorder.status();
  }
  const status = recorder.start();
  if (dashboard && !dashboard.isDestroyed()) dashboard.hide();
  if (MAC) app.dock.hide();
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
  if (MAC) app.dock.show();
  openReview(status.recordingId);
  if (openaiConfig(process.env, recorder.settings)) explainRecording(status.recordingId).catch(() => {});
  return status;
}

// ---- cloud workspace ----------------------------------------------------------
const CLOUD_FILE = path.join(HOME, 'cloud.json');
const UPLOADS_FILE = path.join(HOME, 'uploads.json');
const activeUploads = new Set();
function cloudSettings() {
  try { return JSON.parse(fs.readFileSync(CLOUD_FILE, 'utf8')); } catch { return null; }
}
function uploadStates() {
  try { return JSON.parse(fs.readFileSync(UPLOADS_FILE, 'utf8')); } catch { return {}; }
}
function writePrivate(file, data) {
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(data, null, 2), {mode:0o600});
  fs.renameSync(temp,file);
}
function requireKeyStorage() {
  if (!safeStorage.isEncryptionAvailable() || (process.platform === 'linux' && safeStorage.getSelectedStorageBackend() === 'basic_text')) {
    throw new Error('Unlock your system keychain before connecting a cloud workspace.');
  }
}
function cloudStatus() {
  const c = cloudSettings();
  return {connected:!!c, url:c?.url ?? '', companyId:c?.companyId ?? '', email:c?.email ?? '', companyName:c?.companyName ?? '', uploads:uploadStates()};
}
function requireDashboard(event) {
  if (!dashboard || event.sender !== dashboard.webContents || event.senderFrame !== dashboard.webContents.mainFrame) throw new Error('Cloud actions are available only in the dashboard.');
}
ipcMain.handle('cloud:status', event => { requireDashboard(event); return cloudStatus(); });
ipcMain.handle('cloud:connect', async (event, input) => {
  requireDashboard(event); requireKeyStorage();
  const url = workspaceURL(input.url), companyId = companyID(input.companyId);
  if (typeof input.token !== 'string' || input.token.length < 32 || input.token.length > 256) throw new Error('Enter your personal access key.');
  const config = {url,companyId,token:input.token};
  const me = await cloudRequest(config,'/auth/me');
  const companies = await cloudRequest(config,'/deals');
  const company = companies.find(c => c.id === companyId);
  if (!company) throw new Error('Your access key does not have access to this company.');
  writePrivate(CLOUD_FILE,{url,companyId,email:me.email,companyName:company.name, encryptedToken:safeStorage.encryptString(input.token).toString('base64')});
  return cloudStatus();
});
ipcMain.handle('cloud:disconnect', event => {
  requireDashboard(event);
  fs.rmSync(CLOUD_FILE,{force:true});
  return cloudStatus();
});
ipcMain.handle('cloud:upload', async (event, id) => {
  requireDashboard(event); requireKeyStorage();
  if (typeof id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id)) throw new Error('Invalid recording ID.');
  const c = cloudSettings();
  if (!c) throw new Error('Connect your cloud workspace in Settings first.');
  if (activeUploads.has(id)) throw new Error('This report is already uploading.');
  activeUploads.add(id);
  const saveState = state => { const states = uploadStates(); states[id] = {...state,url:c.url,companyId:c.companyId}; writePrivate(UPLOADS_FILE,states); };
  try {
    const token = safeStorage.decryptString(Buffer.from(c.encryptedToken,'base64'));
    const result = await uploadReport({...c,token},RECORDINGS,id);
    saveState({status:'uploaded',uploadedAt:new Date().toISOString(),recordingId:result.id});
    return cloudStatus();
  } catch (error) {
    saveState({status:'failed',error:error.message});
    throw error;
  } finally { activeUploads.delete(id); }
});

// ---- IPC ----------------------------------------------------------------------

ipcMain.handle('rec:start', () => startRecording());
ipcMain.handle('rec:pause', () => pauseRecording());
ipcMain.handle('rec:resume', () => resumeRecording());
ipcMain.handle('rec:stop', () => stopRecording());
ipcMain.handle('rec:toggle', () => toggle());
ipcMain.handle('rec:status', () => recorder.status());
ipcMain.handle('recordings:list', () => listRecordings());
ipcMain.handle('recordings:open', (_e, id) => shell.openPath(id ? path.join(RECORDINGS, id) : RECORDINGS));
ipcMain.handle('recordings:annotate', (_e, id, ann) => addAnnotation(id, ann));
ipcMain.handle('recordings:sections', (_e, id) => sectionsFor(id));
ipcMain.handle('recordings:explain', (_e, id, opts) => explainRecording(id, opts ?? {}));
ipcMain.handle('recordings:decide', (_e, id, itemId, action, body) => decide(id, itemId, action, body ?? {}));
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
ipcMain.handle('app:info', () => ({ demo: DEMO, home: HOME, platform: process.platform, user: os.userInfo().username, openai: !!openaiConfig(process.env, recorder.settings) }));
ipcMain.handle('permissions:get', () => permissions(false));
ipcMain.handle('permissions:open', (_e, kind) => openPermissionPane(kind));
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
