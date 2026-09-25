// Electron main process: owns the Recorder, the always-on-top overlay pill, the
// employee dashboard, a hidden screen-capture window (webm video) and the tray.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { BrowserWindow, Menu, Tray, app, clipboard, desktopCapturer, globalShortcut, ipcMain, nativeImage, screen, session, shell, systemPreferences, safeStorage } from 'electron';

import { CONFIDENCE_THRESHOLD, RESOLVED_STATUSES, SESSION_ID, applyDecision, describeSection, explainSection, openaiConfig, reviewSummary } from './explain.js';
import { DEMO_KEYS, DemoHook, demoActiveWindow, demoClipboard, demoDocuments } from './demo.js';
import { DEFAULT_SETTINGS, Recorder, isOwnApp as isOwnAppName, keyNamesFrom, loadSettings, ownAppList } from './recorder.js';
import { withPermissionFallback } from './foreground.js';
import { FILES_DIR, FILES_FILE, FileTracker, axDocuments, documentFromTitle, lsofDocuments, publicFile, readFiles, snapshotFiles, spotlightSweep, writeFiles } from './files.js';
import { redactText } from './redact.js';
import { fetchReview, mergeReview, readSectionEdits, reportBundle, reviewItems, sendDecision, submitSections, uploadMedia, uploadReport, workspaceRecordingURL, workspaceRunState, workspaceURL } from './cloud.js';
import { appSpans, buildSections, parseEvents, recordingName } from './sections.js';
import { apiConfig, startApi } from './api.js';
import { JobStore } from './jobs.js';
import { reviewFiles } from './filereview.js';
import { FLAG_DECISIONS, INSIGHTS_FILE, buildInsights, insightsSummary, summarizeInsights } from './insights.js';
import { WORKFLOWS_FILE, refineSessionWorkflow, sessionDigest, suggestWorkflows, workflowsStub } from './workflows.js';
import { CONSENT_VERSION, deviceId, discoverWorkspaces, documentOptions, planPreview, selectWorkspace, SubmissionQueue, uploadBinding } from './intake.js';
import { ANCHORS_FILE, applyPlanEdits, compilePlan, PLAN_EDITS_FILE, PLAN_FILE, planSummary } from './plan.js';
import { ComputerUseClient } from './computer-use/client.js';
import { defaultHarnesses } from './computer-use/harnesses.js';
import { openSandboxPage } from './computer-use/browser-electron.js';
import { openChromePage } from './computer-use/browser-chrome.js';
import { linuxBackend } from './computer-use/desktop-linux.js';
import { macosBackend } from './computer-use/desktop-macos.js';
import { nutPointer } from './computer-use/pointer.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI = path.join(__dirname, '..', 'ui');
const PRELOAD = path.join(__dirname, 'preload.cjs');
// `--demo` or the hidden Settings toggle (settings.demoMode); fixed for the
// life of the process because the input hooks are built once.
let DEMO = process.argv.includes('--demo');
const MAC = process.platform === 'darwin';
const HOME = process.env.VISTA_HOME ?? path.join(os.homedir(), 'Vista');
const RECORDINGS = path.join(HOME, 'recordings'); // pending: everything still on this computer
const SUBMITTED = path.join(HOME, 'submitted'); // uploaded: only the metadata stub stays
const SETTINGS_FILE = path.join(HOME, 'settings.json');
const ADMIN = process.env.VISTA_ADMIN === '1';
const ID_RE = /^[a-zA-Z0-9_-]{1,128}$/;

fs.mkdirSync(RECORDINGS, { recursive: true });
fs.mkdirSync(SUBMITTED, { recursive: true });

// A recording lives in recordings/ until it is submitted, then in submitted/.
function recDir(id) {
  if (!ID_RE.test(String(id))) throw new Error('Invalid recording ID.');
  const pending = path.join(RECORDINGS, id);
  return fs.existsSync(path.join(pending, 'manifest.json')) ? pending : path.join(SUBMITTED, id);
}

// Pick up OPENAI_API_KEY / VISTA_OPENAI_* from ~/Vista/.env and the repo's .env so a key
// set once for the backend also works when the app is launched from Finder / `make start`.
// Variables already in the environment win.
function loadDotenv() {
  const repo = findRepoRoot();
  for (const f of [path.join(HOME, '.env'), repo && path.join(repo, '.env')]) {
    if (!f || !fs.existsSync(f)) continue;
    for (const line of fs.readFileSync(f, 'utf8').split('\n')) {
      const m = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/.exec(line);
      if (!m || m[1] in process.env) continue;
      process.env[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
    }
  }
}
loadDotenv();

let overlay, dashboard, captureWin, tray, recorder, apiServer;
let videoDone = null;

// ---- recorder wiring -------------------------------------------------------

async function buildRecorder() {
  const settings = loadSettings(SETTINGS_FILE);
  if (settings.demoMode) DEMO = true;
  let hook = null;
  let activeWindow = null;
  let runningApps = null;
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
      const gw = await import('get-windows');
      activeWindow = withPermissionFallback(gw.activeWindow, { warn: (m) => console.warn(m) });
      // Every app with a window, foreground or not: app start/stop and background apps.
      if (typeof gw.openWindows === 'function') runningApps = async () => [...new Set((await gw.openWindows()).map((w) => w.owner?.name).filter(Boolean))];
    } catch (err) {
      console.error('get-windows unavailable, foreground app will not be recorded:', err.message);
      // A probe that fails loudly: the recorder notes it on every poll, so the reason
      // reaches the session manifest instead of only this console.
      const reason = String(err?.message ?? err).slice(0, 200);
      activeWindow = async () => {
        throw new Error(`get-windows unavailable: ${reason}`);
      };
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
    fileProbe: DEMO ? null : probeDocuments,
    runningApps,
  });
  rec.on('status', broadcastStatus);
  rec.on('finished', (m) => postProcess(m).catch((e) => console.error('post-processing failed:', e.message)));
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

// Every document open right now: Accessibility across all running apps, plus the
// frontmost app's open handles and its window title for apps that expose no
// AXDocument. Returns null when nothing could be probed (keep the current state).
async function probeDocuments(win) {
  const app = win?.owner?.name ?? '';
  const [ax, handles, titled] = await Promise.all([axDocuments(), lsofDocuments(win?.owner?.processId), documentFromTitle(win?.title)]);
  if (!ax && !handles.length && !titled) return null;
  const docs = [...(ax?.docs ?? [])];
  for (const p of handles) docs.push({ path: p, app, wide: false, source: 'lsof' });
  if (titled) docs.push({ path: titled, app, wide: false, source: 'title' });
  return { docs, running: ax?.running ?? null };
}

// After Stop: add what Spotlight saw used/added during the session, then copy
// the last version of every document into files/ so it ships with the recording.
async function collectDocuments(dir, manifest) {
  if (!recorder.settings.files) return;
  const live = readFiles(dir);
  if (live.some((f) => 'snapshot' in f)) return; // already collected (a re-run after an annotation)
  const tracker = new FileTracker();
  for (const f of live) {
    for (const iv of f.intervals) tracker.addInterval(f.path, Date.parse(iv.start), Date.parse(iv.end), { app: iv.app, source: f.sources?.[0] ?? 'ax' });
    for (const t of f.used_at ?? []) tracker.touch(f.path, Date.parse(t), { source: f.sources?.[0] ?? 'ax' });
  }
  for (const hit of await spotlightSweep({ start: manifest.started_at, end: manifest.ended_at })) tracker.touch(hit.path, hit.at, { source: hit.source });
  const t0 = Date.parse(manifest.started_at), t1 = Date.parse(manifest.ended_at);
  if (DEMO) for (const d of demoDocuments(HOME)) tracker.addInterval(d.path, t0, t1, { app: d.app, source: 'demo' });
  const files = tracker.finish({ t0, t1, pauses: manifest.pauses ?? [] });
  const excluded = new Set(live.filter((f) => f.include === false).map((f) => f.path));
  for (const f of files) if (excluded.has(f.path)) f.include = false;
  writeFiles(dir, snapshotFiles(dir, files, { startedAt: manifest.started_at }));
}

// The employee can leave a document out before Submit; its snapshot is deleted.
function requireSharingDraft(recordingId) {
  if (uploadStates()[recordingId]?.protocol === 2)
    throw new Error('This sharing package is already queued and cannot be changed. Local edits do not withdraw an uploaded package.');
}

function toggleFile(recordingId, fileId, include) {
  requireSharingDraft(recordingId);
  if (!/^[a-f0-9]{12}$/.test(String(fileId))) throw new Error('Invalid file.');
  const dir = recDir(recordingId);
  if (readManifest(dir).submitted) throw new Error('This session was submitted.');
  const files = readFiles(dir);
  const f = files.find((x) => x.id === fileId);
  if (!f) throw new Error('File not found.');
  f.include = !!include;
  if (!f.include && f.snapshot) {
    fs.rmSync(path.join(dir, FILES_DIR, f.id), { recursive: true, force: true });
    f.snapshot = null;
  } else if (f.include && !f.snapshot) snapshotFiles(dir, [f]);
  writeFiles(dir, files);
  return sectionsFor(recordingId);
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
// Resolves with the manifest patch once processing is done/failed/skipped.
async function postProcess(manifest) {
  const dir = path.join(RECORDINGS, manifest.recording_id);
  const repo = app.isPackaged || cloudSettings()?.protocol === 2 ? null : findRepoRoot();
  // Documents first: Submit is only offered once processing is 'done', so the
  // snapshots are in place before anything can leave the machine.
  await collectDocuments(dir, manifest).then(() => broadcastSections(manifest.recording_id)).catch((e) => console.error('document collection failed:', e.message));
  const ready = readManifest(dir);
  fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify({ ...ready, upload_ready: true }, null, 2));
  broadcastRecordings();
  // File agent + insights run alongside taskmining; both are idempotent (re-runs
  // after a decision only fill in what is missing).
  const agents = runAgents(manifest.recording_id).catch((e) => console.error('agents failed:', e.message));
  const write = (patch) => {
    const m = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
    fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify({ ...m, ...patch }, null, 2));
    broadcastRecordings();
    return patch;
  };
  if (!repo) {
    await agents;
    return write({ processing: 'awaiting_upload', processing_note: 'Ready to upload activity metadata and selected documents. Cloud analysis is not enabled in this version.' });
  }
  const args = ['-m', 'taskmining', 'run', '--input', path.join(dir, 'events.jsonl'), '--out', path.join(dir, 'processed')];
  if (!recorder.settings.redact) args.push('--no-redact', '--no-pseudonymize');
  if (fs.existsSync(path.join(dir, 'annotations.jsonl'))) args.push('--annotations', path.join(dir, 'annotations.jsonl'));
  write({ processing: 'running' });
  const result = await new Promise((resolve) => {
    const py = spawn(pythonFor(repo), args, { cwd: repo });
    let err = '';
    py.stderr.on('data', (d) => (err += d));
    py.on('error', (e) => resolve(write({ processing: 'failed', processing_note: e.message })));
    py.on('close', (code) => {
      if (code !== 0) return resolve(write({ processing: 'failed', processing_note: err.trim().split('\n').pop() }));
      let summary = null;
      try {
        summary = pickSummary(JSON.parse(fs.readFileSync(path.join(dir, 'processed', 'summary.json'), 'utf8')));
      } catch (e) {
        return resolve(write({ processing: 'failed', processing_note: `summary unreadable: ${e.message}` }));
      }
      resolve(write({ processing: 'done', summary }));
      if (cloudSettings()) explainRecording(manifest.recording_id).catch((e) => noteReviewError(manifest.recording_id, e));
    });
  });
  await agents;
  // Taskmining activities are in now: redo the suggestions and the deterministic
  // part of the insights (the model paragraph is kept).
  try {
    buildWorkflows(manifest.recording_id);
    await refineWorkflows(manifest.recording_id);
    refreshInsights(manifest.recording_id);
  } catch (e) {
    console.error('workflow suggestions failed:', e.message);
  }
  return result;
}

// ---- suggested workflows ---------------------------------------------------------

function readWorkflows(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, WORKFLOWS_FILE), 'utf8'));
  } catch {
    return null;
  }
}

// Deterministic suggestions from the environment + actions; stub on the manifest
// so later sessions can see which ones recur.
// The recording as a state graph (plan.json, metadata only) plus the anchors a run
// needs to find the same controls again (anchors.json: titles, URLs, coordinates —
// this file never leaves the folder). Recompiled whenever the sections change.
function buildPlan(recordingId) {
  const dir = recDir(recordingId);
  const m = readManifest(dir);
  if (!m.ended_at || m.submitted) return null;
  const events = readEvents(dir);
  const edits = readSectionEdits(dir);
  const excluded = buildSections(events, m).filter((s) => edits[s.id]?.excluded);
  const { graph, anchors } = compilePlan({ recordingId, events, files: readFiles(dir), excluded, manifest: m });
  fs.writeFileSync(path.join(dir, PLAN_FILE), JSON.stringify(graph, null, 2), { mode: 0o600 });
  fs.writeFileSync(path.join(dir, ANCHORS_FILE), JSON.stringify(anchors, null, 2), { mode: 0o600 });
  return graph;
}

function readPlanEdits(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, PLAN_EDITS_FILE), 'utf8'));
  } catch {
    return {};
  }
}

// What the dashboard shows: the graph after the employee's edits, with the moves in the
// order they were recorded so the list reads like the session did.
function planFor(recordingId) {
  const dir = recDir(recordingId);
  let graph = null;
  if (readManifest(dir).submitted) {
    try {
      graph = JSON.parse(fs.readFileSync(path.join(dir, PLAN_FILE), 'utf8'));
    } catch {
      graph = null;
    }
  } else graph = buildPlan(recordingId);
  if (!graph) return null;
  const edits = readPlanEdits(dir);
  const reviewed = applyPlanEdits(graph, edits);
  const first = (e) => Number(String(e.provenance[0]?.event_ids[0] ?? 'e0').slice(1));
  const moves = graph.edges
    .slice()
    .sort((a, b) => first(a) - first(b))
    .map((e) => ({
      id: e.id, action: e.action_class, control: e.control, slot: e.slot, effect: e.effect,
      role: graph.nodes.find((n) => n.key === e.frm)?.app_role ?? 'other',
      policy: reviewed.edges.find((r) => r.id === e.id)?.policy ?? e.policy, excluded: !!edits[e.id]?.excluded,
    }));
  return { summary: planSummary(reviewed), moves, submitted: !!readManifest(dir).submitted };
}

function editPlan(recordingId, edgeId, { excluded, policy } = {}) {
  if (!/^[0-9a-f]{16}$/.test(String(edgeId))) throw new Error('Invalid move.');
  const dir = recDir(recordingId);
  if (readManifest(dir).submitted) throw new Error('This session was submitted; edit it in the workspace.');
  const edits = readPlanEdits(dir);
  const cur = { ...(edits[edgeId] ?? {}) };
  if (excluded !== undefined) cur.excluded = !!excluded;
  if (policy !== undefined) {
    if (policy !== null && policy !== 'always_ask' && policy !== 'confirm') throw new Error('A move can only be made to ask more often.');
    if (policy === null) delete cur.policy;
    else cur.policy = policy;
  }
  if (!cur.excluded && !cur.policy) delete edits[edgeId];
  else edits[edgeId] = { ...cur, edited_at: new Date().toISOString() };
  fs.writeFileSync(path.join(dir, PLAN_EDITS_FILE), JSON.stringify(edits, null, 2), { mode: 0o600 });
  return planFor(recordingId);
}

function buildWorkflows(recordingId) {
  const dir = recDir(recordingId);
  const m = readManifest(dir);
  if (!m.ended_at || m.submitted) return readWorkflows(dir);
  try {
    buildPlan(recordingId);
  } catch (e) {
    console.error('Plan graph not compiled:', e?.message ?? e);
  }
  const wf = suggestWorkflows({ manifest: m, events: readEvents(dir), files: readFiles(dir), summary: m.summary ?? null });
  fs.writeFileSync(path.join(dir, WORKFLOWS_FILE), JSON.stringify(wf, null, 2));
  fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify({ ...readManifest(dir), workflows: workflowsStub(wf) }, null, 2));
  broadcastRecordings();
  broadcastSections(recordingId);
  return wf;
}

// When no rule fired, the single session workflow is rewritten by the model
// from the same digest (local key only; the cloud path has its own reviewer).
async function refineWorkflows(recordingId) {
  const dir = recDir(recordingId);
  const wf = readWorkflows(dir);
  const api = localModelConfig();
  if (!wf?.fallback || !api || cloudSettings()) return wf;
  const m = readManifest(dir);
  const digest = sessionDigest({ manifest: m, events: readEvents(dir), files: readFiles(dir), summary: m.summary ?? null });
  try {
    const next = await refineSessionWorkflow(wf, digest, api);
    if (next !== wf) {
      fs.writeFileSync(path.join(dir, WORKFLOWS_FILE), JSON.stringify(next, null, 2));
      broadcastRecordings();
      broadcastSections(recordingId);
    }
    return next;
  } catch (e) {
    console.error('session workflow rewrite failed:', e.message);
    return wf;
  }
}

function refreshInsights(recordingId) {
  const dir = recDir(recordingId);
  const m = readManifest(dir);
  const prior = readInsights(dir);
  if (!prior || m.submitted) return;
  const events = readEvents(dir);
  writeInsights(dir, buildInsights({ manifest: m, events, sections: buildSections(events, m), files: readFiles(dir), previous: previousManifests(recordingId), workflows: readWorkflows(dir) }, prior));
  broadcastSections(recordingId);
}

// ---- file agent + insights ------------------------------------------------------

function readInsights(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, INSIGHTS_FILE), 'utf8'));
  } catch {
    return null;
  }
}

function writeInsights(dir, insights) {
  fs.writeFileSync(path.join(dir, INSIGHTS_FILE), JSON.stringify(insights, null, 2));
}

// Parse, scan and (with a key) summarise every document snapshot. Each file is
// pushed to the dashboard as soon as it is done.
async function reviewDocuments(recordingId, { force = false } = {}) {
  const dir = recDir(recordingId);
  const files = readFiles(dir);
  if (!files.length) return files;
  const api = localModelConfig();
  await reviewFiles(dir, files, api, {
    force,
    onFile: () => {
      writeFiles(dir, files);
      broadcastSections(recordingId);
    },
  });
  writeFiles(dir, files);
  return files;
}

// Manifests of the employee's other recordings, for trends.
function previousManifests(recordingId) {
  const out = [];
  for (const root of [RECORDINGS, SUBMITTED]) {
    for (const id of fs.readdirSync(root)) {
      if (id === recordingId) continue;
      try {
        out.push(JSON.parse(fs.readFileSync(path.join(root, id, 'manifest.json'), 'utf8')));
      } catch {
        /* skip */
      }
    }
  }
  return out.filter((m) => m.ended_at).sort((a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? '')).slice(0, 20);
}

// Flags + keyboard/mouse analysis + trends (deterministic), then the model's
// paragraph once, kept across re-runs.
async function computeInsights(recordingId, { force = false } = {}) {
  const dir = recDir(recordingId);
  const m = readManifest(dir);
  if (!m.ended_at) return null;
  const events = readEvents(dir);
  const sections = buildSections(events, m);
  const prior = readInsights(dir);
  const workflows = readWorkflows(dir) ?? buildWorkflows(recordingId);
  const insights = buildInsights({ manifest: m, events, sections, files: readFiles(dir), previous: previousManifests(recordingId), workflows }, force ? { ...prior, summary: null } : prior);
  writeInsights(dir, insights);
  broadcastSections(recordingId);
  const api = localModelConfig();
  if (api && (!insights.summary || insights.summary.error)) {
    try {
      insights.summary = await summarizeInsights(insights, api);
    } catch (e) {
      insights.summary = { error: e.message, at: new Date().toISOString() };
    }
    writeInsights(dir, insights);
    broadcastSections(recordingId);
  }
  return insights;
}

const agentsRunning = new Set();
async function runAgents(recordingId, opts = {}) {
  if (agentsRunning.has(recordingId)) return;
  agentsRunning.add(recordingId);
  try {
    await reviewDocuments(recordingId, opts);
    await computeInsights(recordingId, opts);
  } finally {
    agentsRunning.delete(recordingId);
  }
}

function decideFlag(recordingId, flagId, decision) {
  if (!FLAG_DECISIONS.has(decision)) throw new Error('Decision must be confirmed or dismissed.');
  const dir = recDir(recordingId);
  if (readManifest(dir).submitted) throw new Error('This session was submitted.');
  const insights = readInsights(dir);
  const flag = insights?.flags?.find((f) => f.id === flagId);
  if (!flag) throw new Error('Flag not found.');
  flag.decision = decision;
  flag.decided_at = new Date().toISOString();
  writeInsights(dir, insights);
  broadcastSections(recordingId);
  return sectionsFor(recordingId);
}

function approveInsights(recordingId, approved = true) {
  const dir = recDir(recordingId);
  if (readManifest(dir).submitted) throw new Error('This session was submitted.');
  const insights = readInsights(dir);
  if (!insights) throw new Error('No analysis yet.');
  insights.approved_at = approved ? new Date().toISOString() : null;
  insights.approved_by = approved ? os.userInfo().username : null;
  writeInsights(dir, insights);
  broadcastSections(recordingId);
  return sectionsFor(recordingId);
}

// Keep a section out of the report and the workspace review (video is untouched).
function excludeSection(recordingId, sectionId, excluded) {
  requireSharingDraft(recordingId);
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(String(sectionId))) throw new Error('Invalid section.');
  const dir = recDir(recordingId);
  if (readManifest(dir).submitted) throw new Error('This session was submitted.');
  const edits = readSectionEdits(dir);
  const cur = edits[sectionId] ?? { name: '', note: '' };
  if (excluded) edits[sectionId] = { ...cur, excluded: true, edited_at: new Date().toISOString() };
  else if (cur.name || cur.note) edits[sectionId] = { name: cur.name, note: cur.note, edited_at: new Date().toISOString() };
  else delete edits[sectionId];
  fs.writeFileSync(path.join(dir, SECTIONS_FILE), JSON.stringify(edits, null, 2));
  broadcastSections(recordingId);
  return sectionsFor(recordingId);
}

// Where AI explanations come from, so the dashboard can say exactly why they are
// (not) running: company workspace, a local key from the environment, a key
// typed into Settings, or nothing. The key itself never leaves the main process.
function localModelConfig() {
  return app.isPackaged || cloudSettings()?.protocol === 2 ? null : openaiConfig(process.env, recorder.settings);
}

function aiStatus() {
  const cloud = cloudSettings();
  const api = localModelConfig();
  return {
    pendingCloudAnalysis: cloud?.protocol === 2,
    enabled: cloud?.protocol === 2 ? false : !!api,
    source: cloud ? 'cloud' : 'local',
    key_source: cloud ? 'cloud' : api?.source ?? 'none',
    model: cloud ? null : api?.model ?? null,
  };
}

function firstError(items) {
  const f = Object.values(items).find((i) => i.status === 'failed' && i.error);
  return f ? f.error : null;
}

// Video sections: the long single-window stretches of a recording, computed
// once after Stop (and again when the employee asks) from events.jsonl.
function isOwnApp(app) {
  return isOwnAppName(app, recorder?.settings.ownApps ?? DEFAULT_SETTINGS.ownApps);
}

/** The own-app list handed to intake, plan and section code: built-ins plus settings. */
function ownApps() {
  return ownAppList(recorder?.settings?.ownApps ?? DEFAULT_SETTINGS.ownApps);
}

function readEvents(dir) {
  try {
    return parseEvents(fs.readFileSync(path.join(dir, 'events.jsonl'), 'utf8')).filter((e) => !isOwnApp(e.app));
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

// Just enough of review.json for the list: where it was explained, the workspace run, open count.
function reviewStub(review) {
  const stub = {
    source: review.source ?? 'local',
    run: review.run ?? null,
    summary: { open: reviewSummary(review.items ?? {}, review.threshold ?? CONFIDENCE_THRESHOLD).open },
  };
  return { ...stub, workspace: workspaceRunState(stub) };
}

function writeReview(dir, review) {
  fs.writeFileSync(path.join(dir, REVIEW_FILE), JSON.stringify(review, null, 2));
}

// Employee edits over a section (name, note) live in sections.json next to the
// recording and travel with it on submit. Applied on top of the computed sections.
const SECTIONS_FILE = 'sections.json';

function editSection(recordingId, sectionId, { name = '', note = '' } = {}) {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(String(sectionId))) throw new Error('Invalid section.');
  const dir = recDir(recordingId);
  if (readManifest(dir).submitted) throw new Error('This session was submitted; edit it in the workspace.');
  const edits = readSectionEdits(dir);
  const clean = { name: scrub(name).trim().slice(0, 200), note: scrub(note).trim().slice(0, 4000) };
  const excluded = edits[sectionId]?.excluded ? { excluded: true } : {};
  if (!clean.name && !clean.note && !excluded.excluded) delete edits[sectionId];
  else edits[sectionId] = { ...clean, ...excluded, edited_at: new Date().toISOString() };
  fs.writeFileSync(path.join(dir, SECTIONS_FILE), JSON.stringify(edits, null, 2));
  return sectionsFor(recordingId);
}

function sectionsFor(recordingId) {
  const dir = recDir(recordingId);
  const m = readManifest(dir);
  const events = readEvents(dir);
  const edits = readSectionEdits(dir);
  const sections = m.submitted ? m.submitted.sections ?? [] : buildSections(events, m);
  const anns = readAnnotations(dir);
  const review = readReview(dir);
  const insights = readInsights(dir);
  for (const s of sections) {
    const e = edits[s.id];
    if (e) {
      s.edited = !!(e.name || e.note);
      s.edited_at = e.edited_at ?? null;
      if (e.name) s.name = e.name;
      s.note = e.note ?? '';
      s.excluded = !!e.excluded;
    }
    s.flags = (insights?.flags ?? []).filter((f) => f.scope === 'section' && f.section_id === s.id);
    // notes that cover each section so the UI can show "annotated"
    s.annotations = anns
      .filter((a) => a.scope !== 'session' && Date.parse(a.start) < Date.parse(s.end) && Date.parse(a.end) > Date.parse(s.start))
      .map((a) => ({ label: a.label, note: a.note, author: a.author }));
    s.review = review.items[s.id] ?? null;
  }
  const video = m.files?.video && fs.existsSync(path.join(dir, m.files.video)) ? path.join(dir, m.files.video) : null;
  const files = m.submitted ? m.submitted.files_list ?? [] : readFiles(dir).map((f) => ({ ...publicFile(f), snapshot_url: f.snapshot ? `file://${path.join(dir, f.snapshot)}` : null }));
  for (const s of sections) {
    const a = Date.parse(s.start), b = Date.parse(s.end);
    s.files = files.filter((f) => f.intervals.some((iv) => Date.parse(iv.start) < b && Date.parse(iv.end) > a)).map((f) => f.name);
  }
  const apps = m.submitted ? m.submitted.apps ?? [] : appSpans(events, m, { ownApps: ownApps() });
  return {
    recording_id: recordingId,
    video,
    video_url: video ? `file://${video}` : null,
    files,
    apps,
    started_at: m.started_at,
    ended_at: m.ended_at,
    pauses: m.pauses ?? [],
    sections,
    submitted: m.submitted ?? null,
    upload: uploadStates()[recordingId] ?? null,
    shots_dir: `file://${path.join(dir, 'shots')}`,
    insights: insights ? { ...insights, summary_counts: insightsSummary(insights), running: agentsRunning.has(recordingId) } : { flags: [], input: null, trends: null, summary: null, approved_at: null, summary_counts: insightsSummary(null), running: agentsRunning.has(recordingId) },
    workflows: readWorkflows(dir),
    review: {
      ...aiStatus(),
      sync_error: review.sync_error ?? null,
      generating: !!review.generating,
      generated_at: review.generated_at,
      model: review.model,
      error: firstError(review.items),
      session: review.items[SESSION_ID] ?? null,
      summary: reviewSummary(review.items, review.threshold ?? CONFIDENCE_THRESHOLD),
      source: review.source ?? 'local',
      run: review.run ?? null,
      workspace: workspaceRunState({ ...review, summary: reviewSummary(review.items, review.threshold ?? CONFIDENCE_THRESHOLD) }),
    },
    workspace_url: workspaceRecordingURL(cloudSettings(), uploadStates()[recordingId]?.recordingId),
  };
}

function broadcastSections(recordingId) {
  if (dashboard && !dashboard.isDestroyed()) dashboard.webContents.send('recordings:sections', sectionsFor(recordingId));
}

// Ask the model to explain every section plus the whole session. Items the
// employee has already resolved are never redone; `force` redoes the open
// ones. Runs after Stop when a key is configured, and on demand from the dashboard.
const explaining = new Set();

function noteReviewError(recordingId, e) {
  const dir = recDir(recordingId);
  const review = readReview(dir);
  review.generating = false;
  review.sync_error = e?.message ?? String(e);
  writeReview(dir, review);
  console.error('AI review failed:', review.sync_error);
  broadcastSections(recordingId);
}

function reviewContext(dir) {
  const m = readManifest(dir);
  const events = readEvents(dir);
  const sections = buildSections(events, m);
  const edits = readSectionEdits(dir);
  const kept = sections.filter((s) => !edits[s.id]?.excluded);
  const flags = (readInsights(dir)?.flags ?? []).filter((f) => f.decision !== 'dismissed');
  const ctx = { manifest: { ...m, annotations_preview: readAnnotations(dir).map((a) => `${a.label}${a.note ? ': ' + a.note : ''}`).slice(0, 8) }, sections: kept, events, flags };
  return { m, sections: kept, ctx, targets: [...kept, { whole: true, id: SESSION_ID, seconds: m.active_seconds ?? 0 }] };
}

const REVIEW_POLL_MS = 2500;
const REVIEW_POLL_MAX_MS = 3 * 60 * 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Cloud-connected review: the section descriptions (redacted here, no
// keystrokes/screenshots) go to the workspace, whose worker asks the model with
// the company's key; results are mirrored into review.json for the dashboard.
async function explainViaCloud(recordingId, dir, review, { force }) {
  const cloud = cloudSettings();
  const token = safeStorage.decryptString(Buffer.from(cloud.encryptedToken, 'base64'));
  const config = { ...cloud, token };
  const cloudId = await uploadToCloud(recordingId, config);
  const { ctx, targets } = reviewContext(dir);
  const items = reviewItems(targets, (s) => describeSection(s, ctx));
  let remote = await submitSections(config, cloudId, items, force);
  const started = Date.now();
  while (remote.generating && Date.now() - started < REVIEW_POLL_MAX_MS) {
    Object.assign(review, mergeReview(review, remote));
    writeReview(dir, review);
    broadcastSections(recordingId);
    await sleep(REVIEW_POLL_MS);
    remote = await fetchReview(config, cloudId);
  }
  Object.assign(review, mergeReview(review, remote));
  if (remote.generating) review.sync_error = 'The workspace is still explaining this session; open it again in a minute.';
}

async function explainRecording(recordingId, { force = false } = {}) {
  const api = localModelConfig();
  const cloud = cloudSettings();
  if (cloud?.protocol === 2) return sectionsFor(recordingId);
  if (!api && !cloud) return { error: 'no_key', message: 'Connect your company workspace under Settings → Cloud workspace (or add an OpenAI API key) and the AI will explain each stretch.' };
  if (explaining.has(recordingId)) return sectionsFor(recordingId);
  const dir = recDir(recordingId);
  if (readManifest(dir).submitted) return sectionsFor(recordingId);
  explaining.add(recordingId);
  const review = readReview(dir);
  review.generating = true;
  review.threshold = CONFIDENCE_THRESHOLD;
  review.model = cloud ? review.model ?? null : api.model;
  review.sync_error = null;
  writeReview(dir, review);
  broadcastSections(recordingId);
  try {
    if (cloud) {
      try {
        await explainViaCloud(recordingId, dir, review, { force });
      } catch (e) {
        review.sync_error = e.message;
      }
      return sectionsFor(recordingId);
    }
    const { ctx, targets } = reviewContext(dir);
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
async function decide(recordingId, itemId, action, body = {}) {
  const dir = recDir(recordingId);
  if (readManifest(dir).submitted) throw new Error('This session was submitted; review it in the workspace.');
  const review = readReview(dir);
  const { item, annotation } = applyDecision(review.items[itemId], action, body);
  review.items[itemId] = item;
  const cloud = cloudSettings();
  const cloudId = uploadStates()[recordingId]?.recordingId;
  if (cloud && review.source === 'cloud' && cloudId) {
    try {
      const token = safeStorage.decryptString(Buffer.from(cloud.encryptedToken, 'base64'));
      Object.assign(review, mergeReview(review, await sendDecision({ ...cloud, token }, cloudId, itemId, action, body)));
      review.sync_error = null;
    } catch (e) {
      review.sync_error = `Saved on this computer only: ${e.message}`;
    }
  }
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

const scrub = (text) => (recorder?.settings.redact ? redactText(text) : String(text ?? ''));

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

// Earliest screenshot of a recording, as a file URL for the list thumbnail.
function firstShot(dir) {
  try {
    const shot = fs.readdirSync(path.join(dir, 'shots')).filter((f) => f.endsWith('.jpg')).sort()[0];
    return shot ? `file://${path.join(dir, 'shots', shot)}` : null;
  } catch {
    return null;
  }
}

function listRecordings() {
  const out = [];
  const uploads = uploadStates();
  for (const root of [RECORDINGS, SUBMITTED]) {
    for (const id of fs.readdirSync(root)) {
      const f = path.join(root, id, 'manifest.json');
      if (!fs.existsSync(f)) continue;
      try {
        const m = JSON.parse(fs.readFileSync(f, 'utf8'));
        let annotations = 0;
        try {
          annotations = fs.readFileSync(path.join(root, id, 'annotations.jsonl'), 'utf8').trim().split('\n').filter(Boolean).length;
        } catch {
          /* none */
        }
        out.push({
          ...m,
          apps: (m.apps ?? []).filter((a) => !isOwnApp(a.app)),
          dir: path.join(root, id),
          thumb: firstShot(path.join(root, id)),
          annotations,
          name: m.name ?? recordingName(m, { summary: m.summary_text ?? '' }),
          upload: uploads[id] ?? null,
          edits: Object.keys(readSectionEdits(path.join(root, id))).length,
          review: reviewStub(readReview(path.join(root, id))),
        });
      } catch {
        /* corrupt manifest */
      }
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
  const dir = recDir(recordingId);
  const m = readManifest(dir);
  if (m.submitted) throw new Error('This session was submitted; add notes in the workspace.');
  const row = {
    user: m.user,
    start: start ?? m.started_at,
    end: end ?? m.ended_at ?? new Date().toISOString(),
    label: scrub(label),
    note: scrub(note),
    case_id,
    author,
    scope,
    section_id,
    qa: qa.map((x) => ({ q: scrub(x.q), a: scrub(x.a) })),
    ...(ai ? { ai } : {}),
  };
  fs.appendFileSync(path.join(dir, 'annotations.jsonl'), JSON.stringify(row) + '\n');
  if (scope === 'session') {
    const summary_text = row.note || row.label;
    const patched = { ...m, summary_text, name: recordingName(m, { summary: summary_text }) };
    fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(patched, null, 2));
  }
  if (m.ended_at) postProcess(readManifest(dir)).catch((e) => console.error('post-processing failed:', e.message));
  else broadcastRecordings();
  return row;
}

// ---- windows -----------------------------------------------------------------

// Overlay sizes: `orb` is the idle blue circle, `pill` the recording bar, `panel` the expanded details.
const SIZES = { orb: { w: 104, h: 104 }, pill: { w: 380, h: 64 }, intent: { w: 380, h: 224 }, panel: { w: 380, h: 332 } };
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

// `ui: false` (agent API) never opens a window: missing permissions are
// reported through the returned status instead of the dashboard.
function startRecording({ ui = true } = {}) {
  const perms = permissions(ui);
  if (perms.needed && !perms.ok) {
    if (!ui) throw new Error('Grant Accessibility and Screen Recording permissions to the recorder first.');
    if (!createDashboard()) dashboard.webContents.send('permissions:changed', perms);
    return recorder.status();
  }
  const starting = recorder.state === 'idle';
  const status = recorder.start();
  const connection = cloudSettings();
  if (starting && connection) writePrivate(path.join(recorder.dir, 'workspace-binding.json'), uploadBinding(connection));
  if (dashboard && !dashboard.isDestroyed()) dashboard.hide();
  if (MAC) app.dock.hide();
  setOverlayMode('pill');
  if (recorder.settings.video && captureWin) {
    fs.writeFileSync(path.join(recorder.dir, 'screen.webm'), '');
    captureWin.webContents.send('video:start', { dir: recorder.dir, fps: 2 });
  }
  return status;
}

// "What are you working on today?" — asked by the overlay right after Start.
// Stored on the recording (manifest summary_text, names it) and as a session
// annotation so the review shows it as the general summary.
function setIntent(text) {
  const clean = scrub(String(text ?? '')).trim().slice(0, 4000);
  if (recorder.state === 'idle' || !clean) return recorder.status();
  const status = recorder.setIntent(clean);
  try {
    addAnnotation(recorder.recordingId, { label: clean, scope: 'session' });
  } catch (e) {
    console.error('intent annotation failed:', e.message);
  }
  return status;
}

async function stopRecording({ ui = true } = {}) {
  if (recorder.state === 'idle') return recorder.status();
  if (recorder.settings.video && captureWin) {
    videoDone = new Promise((res) => ipcMain.once('video:done', () => res()));
    captureWin.webContents.send('video:stop');
    await Promise.race([videoDone, new Promise((r) => setTimeout(r, 3000))]);
  }
  const status = await recorder.stop();
  broadcastRecordings();
  if (overlay && !overlay.isDestroyed()) overlay.webContents.send('overlay:collapse');
  setOverlayMode('orb');
  if (MAC) app.dock.show();
  if (ui) openReview(status.recordingId);
  // Cloud-connected: the review starts once the local analysis is done (postProcess).
  if (!cloudSettings() && localModelConfig()) explainRecording(status.recordingId).catch((e) => noteReviewError(status.recordingId, e));
  return status;
}

// ---- cloud workspace ----------------------------------------------------------
const CLOUD_FILE = path.join(HOME, 'cloud.json');
const UPLOADS_FILE = path.join(HOME, 'uploads.json');
const activeUploads = new Set();
function cloudSettings() {
  try {
    const c = JSON.parse(fs.readFileSync(CLOUD_FILE, 'utf8'));
    return c.protocol === 2 ? c : null;
  } catch { return null; }
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
  return {
    connected: !!c, protocol: c?.protocol ?? null, url: c?.url ?? '', companyId: c?.workspace?.id ?? '',
    workspace: c?.workspace ?? null, email: c?.email ?? '', companyName: c?.companyName ?? '',
    requiresReconnect: !c && fs.existsSync(CLOUD_FILE), uploads: uploadStates(),
  };
}
function requireDashboard(event) {
  if (!dashboard || event.sender !== dashboard.webContents || event.senderFrame !== dashboard.webContents.mainFrame) throw new Error('Cloud actions are available only in the dashboard.');
}
ipcMain.handle('cloud:status', event => { requireDashboard(event); return cloudStatus(); });
let pendingEnrollment = null;
ipcMain.handle('cloud:discover', async (event, input) => {
  requireDashboard(event); requireKeyStorage();
  pendingEnrollment = null;
  const url = workspaceURL(input.url || 'https://bumpsolutions.org');
  const identity = await discoverWorkspaces({ url, token: input.token });
  pendingEnrollment = { url, token: input.token, identity, expires: Date.now() + 300000 };
  return identity;
});
ipcMain.handle('cloud:connect', async (event, input) => {
  requireDashboard(event); requireKeyStorage();
  if (!pendingEnrollment || pendingEnrollment.expires < Date.now()) throw new Error('Sign in again to choose your company.');
  if (input.consent !== true) throw new Error('Confirm the recording and sharing notice before connecting.');
  const { url, token, identity } = pendingEnrollment;
  const workspace = selectWorkspace(identity, input.workspace);
  writePrivate(CLOUD_FILE, {
    protocol: 2, url, workspace: { id: workspace.id, kind: workspace.kind }, companyId: workspace.id,
    companyName: workspace.name, userId: identity.user_id, tenantId: identity.tenant_id, email: identity.email,
    deviceId: deviceId(HOME), consentVersion: CONSENT_VERSION, encryptedToken: safeStorage.encryptString(token).toString('base64'),
  });
  pendingEnrollment = null;
  resumeUploads(true);
  return cloudStatus();
});
ipcMain.handle('cloud:disconnect', event => {
  requireDashboard(event);
  pendingEnrollment = null;
  fs.rmSync(CLOUD_FILE,{force:true});
  return cloudStatus();
});
// Upload the local report (idempotent server-side) and return the workspace's
// recording id, which the review endpoints key on.
async function uploadToCloud(id, config) {
  if (config.protocol === 2) throw new Error('Use Upload session to review and confirm the sharing package.');
  if (typeof id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(id)) throw new Error('Invalid recording ID.');
  if (activeUploads.has(id)) throw new Error('This report is already uploading.');
  activeUploads.add(id);
  const saveState = state => { const states = uploadStates(); states[id] = {...state,url:config.url,companyId:config.companyId}; writePrivate(UPLOADS_FILE,states); };
  try {
    const result = await uploadReport(config,RECORDINGS,id);
    saveState({status:'uploaded',uploadedAt:new Date().toISOString(),recordingId:result.id});
    return result.id;
  } catch (error) {
    saveState({status:'failed',error:error.message});
    throw error;
  } finally { activeUploads.delete(id); }
}
function cloudConfig() {
  const c = cloudSettings();
  if (!c) throw new Error('Connect your company in Settings with your personal access key first.');
  requireKeyStorage();
  return { ...c, token: safeStorage.decryptString(Buffer.from(c.encryptedToken, 'base64')) };
}

const intakeQueue = new SubmissionQueue(HOME, {
  isCurrent: (binding) => {
    const c = cloudSettings();
    return !!c && JSON.stringify(uploadBinding(c)) === JSON.stringify(binding);
  },
  onChange: (state) => {
    const states = uploadStates();
    states[state.manifest.source_id] = {
      protocol: 2, status: state.status, url: state.binding.url, companyId: state.binding.workspace.id,
      submissionId: state.submission?.id ?? null, receipt: state.receipt, error: state.error,
      progress: { done: state.uploaded.length, total: state.manifest.artifacts.length },
      analysis_status: state.analysis?.status ?? 'not_started', publication_status: state.analysis?.publication ?? 'draft',
      analysis: state.analysis ?? null,
    };
    writePrivate(UPLOADS_FILE, states);
    if (recorder) {
      broadcastRecordings();
      if (fs.existsSync(path.join(RECORDINGS, state.manifest.source_id, 'manifest.json'))) broadcastSections(state.manifest.source_id);
    }
  },
});
// ---- computer use --------------------------------------------------------------
// The Computer Use Agent runs approved workflows through *this* computer, but only inside
// a session the employee starts here with explicit consent. The cloud cannot reach the
// recorder; the client polls on the same 30 s tick as uploads and, while a session is
// active, every 3 s. Cmd/Ctrl+Shift+Escape stops a session from anywhere.
const KILL_SWITCH = 'CommandOrControl+Shift+Escape';
const computerUse = new ComputerUseClient({
  home: HOME,
  deviceId: deviceId(HOME),
  version: app.getVersion(),
  harnesses: defaultHarnesses({ demo: DEMO }),
  config: () => {
    try {
      return cloudSettings() ? cloudConfig() : null;
    } catch {
      return null;
    }
  },
  onChange: (status) => {
    if (dashboard && !dashboard.isDestroyed()) dashboard.webContents.send('cu:status', status);
    if (status.active && !globalShortcut.isRegistered(KILL_SWITCH)) globalShortcut.register(KILL_SWITCH, () => computerUse.stop('the employee pressed the kill switch'));
    if (!status.active && globalShortcut.isRegistered(KILL_SWITCH)) globalShortcut.unregister(KILL_SWITCH);
  },
});
// The drivers need Electron ready and the recorder's window probe; until then the client
// holds placeholders and advertises nothing. Demo mode keeps the placeholders on purpose.
async function buildHarnesses() {
  if (DEMO) return defaultHarnesses({ demo: true });
  const settings = () => recorder?.settings ?? DEFAULT_SETTINGS;
  const pointer = await nutPointer();
  const desktopBackend = (await macosBackend({ activeWindow: recorder?.activeWindow ?? null })) ?? (await linuxBackend());
  // VISTA_CU_BROWSER=chrome drives a tab in the employee's own Chrome (DevTools port) instead
  // of the recorder's isolated window — the employee's profile, so opt-in only.
  const openPage = process.env.VISTA_CU_BROWSER === 'chrome' ? openChromePage() : openSandboxPage();
  return defaultHarnesses({ openPage, pointer, desktopBackend, settings });
}
ipcMain.handle('cu:status', (event) => { requireDashboard(event); return computerUse.status(); });
ipcMain.handle('cu:list', (event) => { requireDashboard(event); return computerUse.tick(); });
ipcMain.handle('cu:start', (event, runId, options) => {
  requireDashboard(event);
  if (recorder.state !== 'idle') throw new Error('Stop the recording first.');
  return computerUse.start(runId, { consent: options?.consent === true, shareScreenshots: options?.shareScreenshots === true });
});
ipcMain.handle('cu:stop', (event, reason) => { requireDashboard(event); return computerUse.stop(reason || 'the employee pressed Stop'); });
ipcMain.handle('cu:steps', (event, sessionId) => { requireDashboard(event); return computerUse.steps(sessionId); });

let uploadTimer = null;
function resumeUploads(force = false) {
  computerUse.tick().catch(() => {});
  if (!cloudSettings()) return;
  try {
    const config = cloudConfig();
    intakeQueue.flush(config, { force })
      .then(() => intakeQueue.refresh(config, { force }))
      .catch(() => console.error('Upload queue paused; reconnect your workspace to retry.'));
  } catch {
    console.error('Unlock your system keychain to resume pending uploads.');
  }
}
async function queueSubmission(id, options) {
  if (recorder.status().recordingId === id && recorder.state !== 'idle') throw new Error('Stop the recording first.');
  const config = cloudConfig();
  if (options?.consent !== true || JSON.stringify(options.expectedBinding) !== JSON.stringify(uploadBinding(config)))
    throw new Error('Review and confirm the upload destination and selected files again.');
  intakeQueue.enqueue(RECORDINGS, id, config, { ...options, ownApps: ownApps() });
  resumeUploads(true);
  return sectionsFor(id);
}
ipcMain.handle('cloud:retry', event => {
  requireDashboard(event);
  resumeUploads(true);
  return cloudStatus();
});
// Cloud analysis of an uploaded session: status, the employee's answers, and the
// explicit publish step. Every call talks to the server with the personal key and
// re-checks that the session is bound to the connected workspace.
async function withAnalysis(event, id, work) {
  requireDashboard(event);
  if (!ID_RE.test(String(id))) throw new Error('Invalid recording ID.');
  await work(cloudConfig());
  broadcastRecordings();
  return sectionsFor(id);
}
ipcMain.handle('recordings:analysis', (event, id) => withAnalysis(event, id, (config) => intakeQueue.status(config, id)));
ipcMain.handle('recordings:answer', (event, id, answers) => withAnalysis(event, id, (config) => intakeQueue.answer(config, id, answers)));
ipcMain.handle('recordings:publish', (event, id, options) => withAnalysis(event, id, (config) => intakeQueue.publish(config, id, options)));
ipcMain.handle('recordings:reanalyze', (event, id) => withAnalysis(event, id, (config) => intakeQueue.reanalyze(config, id)));
ipcMain.handle('recordings:upload-preview', (event, id) => {
  requireDashboard(event);
  const c = cloudConfig();
  let plan = null;
  try {
    plan = planPreview(RECORDINGS, id, { ownApps: ownApps() });
  } catch (e) {
    console.error('Plan graph preview failed:', e?.message ?? e);
  }
  return {
    companyName: c.companyName, email: c.email, binding: uploadBinding(c), documents: documentOptions(RECORDINGS, id), plan,
    planAllowed: (c.consentVersion ?? 'activity-metadata-v1') === CONSENT_VERSION,
  };
});
ipcMain.handle('cloud:upload', async (event, id) => {
  requireDashboard(event);
  await uploadToCloud(id, cloudConfig());
  return cloudStatus();
});

// Submit = the whole recording goes to the workspace, then leaves this computer.
// Order: report (idempotent) → every media file via signed URLs → move the
// metadata stub to submitted/ → delete the recording folder. A failure at any
// step leaves the folder in place with status 'failed' so Submit can be retried.
const STUB_FILES = ['manifest.json', REVIEW_FILE, SECTIONS_FILE, 'annotations.jsonl', WORKFLOWS_FILE, PLAN_FILE, PLAN_EDITS_FILE];
// files.json travels too, without the absolute paths.
function writeFilesStub(dir, stub) {
  const files = readFiles(dir).map(publicFile);
  if (files.length) fs.writeFileSync(path.join(stub, FILES_FILE), JSON.stringify({ version: 1, files }, null, 2));
  return files;
}
// Resolved section metadata (time span, video offsets, name, employee note,
// AI explanation + decision, annotations) written as a sidecar next to the
// video so the labels travel with screen.webm wherever the media goes.
const VIDEO_SECTIONS_FILE = 'screen.sections.json';
function writeVideoSidecar(dir, id, m) {
  const all = sectionsFor(id);
  const sections = all.sections.map((s) => ({ ...s, annotations: s.annotations ?? [], review: s.review ?? null }));
  const { running: _r, ...insights } = all.insights;
  fs.writeFileSync(
    path.join(dir, VIDEO_SECTIONS_FILE),
    JSON.stringify({ recording_id: id, video: m.files?.video ?? null, started_at: m.started_at, ended_at: m.ended_at, pauses: m.pauses ?? [], sections, insights, documents: readFiles(dir).filter((f) => f.include !== false).map(publicFile) }, null, 2),
  );
  return sections;
}
// Demo mode without a workspace: the same local bookkeeping as a real submit
// (stub in submitted/, upload state), with a short simulated upload.
async function demoSubmit(id, dir, m) {
  const saveState = (state) => {
    const states = uploadStates();
    states[id] = { ...(states[id] ?? {}), ...state, url: 'demo', companyId: 'demo' };
    writePrivate(UPLOADS_FILE, states);
    broadcastRecordings();
  };
  activeUploads.add(id);
  try {
    const total = 5;
    for (let done = 0; done <= total; done++) {
      saveState({ status: 'uploading', progress: { done, total } });
      await new Promise((r) => setTimeout(r, 180));
    }
    const sections = writeVideoSidecar(dir, id, m);
    const stub = path.join(SUBMITTED, id);
    fs.mkdirSync(stub, { recursive: true });
    for (const f of STUB_FILES) if (fs.existsSync(path.join(dir, f))) fs.copyFileSync(path.join(dir, f), path.join(stub, f));
    const files_list = writeFilesStub(dir, stub);
    const cloudId = `demo-${id}`;
    fs.writeFileSync(path.join(stub, 'manifest.json'), JSON.stringify({ ...readManifest(dir), files: {}, submitted: { at: new Date().toISOString(), recording_id: cloudId, files: files_list.length, sections, files_list, apps: appSpans(readEvents(dir), m, { ownApps: recorder.settings.ownApps ?? [] }), demo: true } }, null, 2));
    fs.rmSync(dir, { recursive: true, force: true });
    saveState({ status: 'submitted', submittedAt: new Date().toISOString(), recordingId: cloudId, files: files_list.length, progress: null });
  } catch (error) {
    saveState({ status: 'failed', error: error.message, progress: null });
    throw error;
  } finally {
    activeUploads.delete(id);
  }
  broadcastSections(id);
  return sectionsFor(id);
}

// The record check Submit / Upload session runs, for the agent API's batch
// reviewer. Protocol 2 has no local analysis to wait for: the capture must be
// saved and not already queued, uploading or accepted.
function validateSubmission(id) {
  if (cloudSettings()?.protocol !== 2) {
    reportBundle(RECORDINGS, id);
    return { protocol: 1, consent_required: false };
  }
  const m = readManifest(recDir(id));
  if (!m.ended_at) throw new Error('Stop the recording first.');
  const up = uploadStates()[id];
  if (up?.protocol === 2 && up.status === 'accepted') throw new Error('This session was already uploaded.');
  if (up?.protocol === 2 && ['queued', 'uploading'].includes(up.status)) throw new Error('This session is already uploading.');
  return { protocol: 2, consent_required: true };
}

async function submitRecording(id, options = {}) {
  if (cloudSettings()?.protocol === 2) return queueSubmission(id, options);
  if (DEMO && !cloudSettings()) {
    if (!ID_RE.test(String(id))) throw new Error('Invalid recording ID.');
    const dir = path.join(RECORDINGS, id);
    if (!fs.existsSync(path.join(dir, 'manifest.json'))) throw new Error(fs.existsSync(path.join(SUBMITTED, id)) ? 'Already submitted.' : 'Recording not found.');
    if (recorder.status().recordingId === id && recorder.state !== 'idle') throw new Error('Stop the recording first.');
    const m = readManifest(dir);
    if (m.processing !== 'done') throw new Error('Wait until this session has finished analysis.');
    if (activeUploads.has(id)) throw new Error('This session is already uploading.');
    return demoSubmit(id, dir, m);
  }
  const config = cloudConfig();
  if (!ID_RE.test(String(id))) throw new Error('Invalid recording ID.');
  const dir = path.join(RECORDINGS, id);
  if (!fs.existsSync(path.join(dir, 'manifest.json'))) throw new Error(fs.existsSync(path.join(SUBMITTED, id)) ? 'Already submitted.' : 'Recording not found.');
  if (fs.realpathSync(dir) !== path.join(fs.realpathSync(RECORDINGS), id)) throw new Error('Invalid recording directory.');
  if (recorder.status().recordingId === id && recorder.state !== 'idle') throw new Error('Stop the recording first.');
  const m = readManifest(dir);
  if (m.processing !== 'done') throw new Error('Wait until this session has finished analysis.');
  if (activeUploads.has(id)) throw new Error('This session is already uploading.');
  activeUploads.add(id);
  const saveState = (state, { sections = true } = {}) => {
    const states = uploadStates();
    states[id] = { ...(states[id] ?? {}), ...state, url: config.url, companyId: config.companyId };
    writePrivate(UPLOADS_FILE, states);
    broadcastRecordings();
    if (sections) broadcastSections(id);
  };
  try {
    saveState({ status: 'uploading', progress: { done: 0, total: 0 } });
    const cloudId = (await uploadReport(config, RECORDINGS, id)).id;
    saveState({ status: 'uploading', recordingId: cloudId, progress: { done: 0, total: 0 } }, { sections: false });
    const sections = writeVideoSidecar(dir, id, m);
    const files = await uploadMedia(config, RECORDINGS, id, cloudId, { onProgress: (p) => saveState({ status: 'uploading', progress: p }, { sections: false }) });
    const stub = path.join(SUBMITTED, id);
    fs.mkdirSync(stub, { recursive: true });
    for (const f of STUB_FILES) if (fs.existsSync(path.join(dir, f))) fs.copyFileSync(path.join(dir, f), path.join(stub, f));
    const files_list = writeFilesStub(dir, stub);
    fs.writeFileSync(
      path.join(stub, 'manifest.json'),
      JSON.stringify({ ...m, files: {}, submitted: { at: new Date().toISOString(), recording_id: cloudId, files: files.length, sections, files_list, apps: appSpans(readEvents(dir), m, { ownApps: ownApps() }) } }, null, 2),
    );
    fs.rmSync(dir, { recursive: true, force: true });
    saveState({ status: 'submitted', submittedAt: new Date().toISOString(), recordingId: cloudId, files: files.length, progress: null });
  } catch (error) {
    saveState({ status: 'failed', error: error.message, progress: null });
    throw error;
  } finally {
    activeUploads.delete(id);
  }
  return sectionsFor(id);
}
ipcMain.handle('recordings:submit', (event, id, options) => {
  requireDashboard(event);
  return submitRecording(id, options);
});
ipcMain.handle('recordings:edit-section', (_e, id, sectionId, patch) => editSection(id, sectionId, patch ?? {}));
ipcMain.handle('recordings:exclude-section', (_e, id, sectionId, excluded) => excludeSection(id, sectionId, !!excluded));
ipcMain.handle('recordings:flag', (_e, id, flagId, decision) => decideFlag(id, String(flagId), String(decision)));
ipcMain.handle('recordings:approve-insights', (_e, id, approved) => approveInsights(id, approved !== false));
ipcMain.handle('recordings:rerun-agents', async (_e, id) => {
  await runAgents(id, { force: true });
  return sectionsFor(id);
});
ipcMain.handle('recordings:toggle-file', (_e, id, fileId, include) => toggleFile(id, fileId, include));
ipcMain.handle('recordings:workflows', (_e, id) => readWorkflows(recDir(id)) ?? buildWorkflows(id));
ipcMain.handle('recordings:plan', (_e, id) => planFor(id));
ipcMain.handle('recordings:edit-plan', (event, id, edgeId, patch) => {
  requireDashboard(event);
  return editPlan(id, String(edgeId), patch ?? {});
});
ipcMain.handle('recordings:open-file', (_e, id, fileId) => {
  const dir = recDir(id);
  const f = readFiles(dir).find((x) => x.id === fileId && x.snapshot);
  return f ? shell.openPath(path.join(dir, f.snapshot)) : 'No copy of this file.';
});

// ---- IPC ----------------------------------------------------------------------

ipcMain.handle('rec:start', () => startRecording());
ipcMain.handle('rec:pause', () => pauseRecording());
ipcMain.handle('rec:resume', () => resumeRecording());
ipcMain.handle('rec:stop', () => stopRecording());
ipcMain.handle('rec:intent', (_e, text) => setIntent(text));
ipcMain.handle('rec:done', (_e, note) => recorder.markDone(scrub(String(note ?? ''))));
ipcMain.handle('rec:toggle', () => toggle());
ipcMain.handle('rec:status', () => recorder.status());
ipcMain.handle('recordings:list', () => listRecordings());
ipcMain.handle('recordings:open', (_e, id) => shell.openPath(id ? recDir(id) : RECORDINGS));
ipcMain.handle('recordings:open-workspace', (event, id) => {
  requireDashboard(event);
  const url = workspaceRecordingURL(cloudSettings(), uploadStates()[id]?.recordingId);
  if (!url) throw new Error('This session is not in your workspace yet.');
  return shell.openExternal(url);
});
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
// Demo mode swaps the input hooks, so it only takes effect on restart.
ipcMain.handle('app:relaunch', async () => {
  if (recorder.state === 'recording' || recorder.state === 'paused') await recorder.stop();
  app.relaunch({ args: process.argv.slice(1).filter((a) => a !== '--demo').concat('--dashboard') });
  app.exit(0);
});
ipcMain.handle('app:info', () => ({ demo: DEMO, admin: ADMIN, home: HOME, platform: process.platform, user: os.userInfo().username, openai: !!localModelConfig(), ai: aiStatus(), cloud: !!cloudSettings(), ownApps: recorder.settings.ownApps ?? DEFAULT_SETTINGS.ownApps }));
ipcMain.handle('permissions:get', () => permissions(false));
ipcMain.handle('permissions:open', (_e, kind) => openPermissionPane(kind));
ipcMain.on('video:chunk', (_e, dir, buf) => {
  try {
    fs.appendFileSync(path.join(dir, 'screen.webm'), Buffer.from(buf));
  } catch {
    /* recording dir gone */
  }
});

// ---- agent API ------------------------------------------------------------------
// The same functions the IPC handlers above call, handed to the HTTP API so an
// agent gets exactly the dashboard's behaviour. Off unless VISTA_RECORDER_API_TOKEN is set.
const apiActions = {
  version: app.getVersion(),
  status: () => recorder.status(),
  startRecording,
  pauseRecording,
  resumeRecording,
  stopRecording,
  setIntent,
  listRecordings,
  sectionsFor,
  recDir,
  readManifest,
  readReview,
  reviewSummary,
  analyze: postProcess,
  explainRecording,
  collectDocuments,
  readFiles,
  publicFile,
  validateSubmission,
  // Agent-driven submits never share by default: under Upload session (protocol 2)
  // the item must carry the employee's explicit consent and document selection,
  // the same contract the dashboard's consent dialog fulfils.
  submitRecording: async (id, options = {}) => {
    if (cloudSettings()?.protocol !== 2) return submitRecording(id, options);
    if (options.consent !== true) throw new Error('Employee consent is required before an upload: pass consent: true with the approved selected_file_ids.');
    await queueSubmission(id, { consent: true, selectedFileIds: options.selectedFileIds ?? [], expectedBinding: uploadBinding(cloudConfig()) });
    return { submitted: null, upload: uploadStates()[id] ?? null };
  },
  runAgents,
  decideFlag,
  approveInsights,
  excludeSection,
  computerUse,
};

async function startAgentApi() {
  const { token, host, port } = apiConfig(process.env);
  if (!token) return;
  const jobs = new JobStore({ home: HOME });
  try {
    apiServer = await startApi({ host, port, token, actions: apiActions, jobs });
    console.log(`agent API listening on http://${host}:${port}`);
  } catch (e) {
    console.error('agent API failed to start:', e.message);
  }
}

// ---- app ----------------------------------------------------------------------

app.commandLine.appendSwitch('enable-transparent-visuals');
app.whenReady().then(async () => {
  recorder = await buildRecorder();
  computerUse.harnesses = await buildHarnesses();
  tray = new Tray(trayIcon());
  updateTray(recorder.status());
  tray.on('click', () => createDashboard());
  createOverlay();
  createCaptureWindow();
  if (process.argv.includes('--dashboard') || !cloudSettings()) createDashboard();
  resumeUploads();
  uploadTimer = setInterval(() => resumeUploads(), 30000);
  await startAgentApi();
});

app.on('window-all-closed', () => {
  /* keep running in the tray/overlay */
});
app.on('before-quit', async (e) => {
  clearInterval(uploadTimer);
  apiServer?.close();
  if (computerUse.session) {
    e.preventDefault();
    await computerUse.stop('the recorder is quitting');
    app.quit();
    return;
  }
  if (recorder && recorder.state !== 'idle') {
    e.preventDefault();
    await stopRecording();
    app.quit();
  }
});
