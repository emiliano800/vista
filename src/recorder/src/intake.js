import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { cloudRequest, workspaceURL } from './cloud.js';
import { readFiles } from './files.js';
import { checkLeakage, describeFailures, recordingContext } from './normalise.js';
import { applyPlanEdits, compilePlan, PLAN_EDITS_FILE, planSummary, recordingValues, recordingVocabulary } from './plan.js';
import { redactText } from './redact.js';
import { buildSections, parseEvents } from './sections.js';

export const SHARING_POLICY = 'activity-metadata-v1';
// The consent card shown at connect time. `computer-use-v2` states both halves — the full
// recording stays on the device; the cloud gets activity metadata and, when shared, the task's
// structure — and is the only version under which a plan may leave the device.
export const CONSENT_VERSION = 'computer-use-v2';
export const CONSENT_VERSIONS = new Set(['activity-metadata-v1', CONSENT_VERSION]);
export const DOCUMENT_TYPES = {
  '.csv': 'text/csv',
  '.tsv': 'text/tab-separated-values',
  '.txt': 'text/plain',
  '.pdf': 'application/pdf',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xlsm': 'application/vnd.ms-excel.sheet.macroenabled.12',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
};
const MAX_ACTIVITY = 4 * 1024 * 1024;
const MAX_PLAN = 1024 * 1024;
const MAX_FILE = 20 * 1024 * 1024;
const MAX_TOTAL = 50 * 1024 * 1024;
const EVENT_TYPES = new Set(['focus', 'click', 'key', 'scroll', 'copy', 'paste', 'shortcut']);
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const hash = (data) => createHash('sha256').update(data).digest('hex');

export function savePrivate(file, data) {
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(data, null, 2), { mode: 0o600 });
  fs.renameSync(temp, file);
}

export function deviceId(home) {
  const file = path.join(home, 'device.json');
  if (fs.existsSync(file)) {
    const id = JSON.parse(fs.readFileSync(file, 'utf8')).id;
    if (!UUID.test(id)) throw new Error('The recorder device identifier is invalid.');
    return id;
  }
  const id = randomUUID();
  savePrivate(file, { id });
  return id;
}

export async function discoverWorkspaces({ url, token }, fetchImpl = fetch) {
  if (typeof token !== 'string' || token.length < 32 || token.length > 256) throw new Error('Enter your personal access key.');
  const identity = await cloudRequest({ url: workspaceURL(url), token }, '/recorder/workspaces', {}, fetchImpl);
  if (!UUID.test(identity.user_id) || !UUID.test(identity.tenant_id) || !Array.isArray(identity.workspaces))
    throw new Error('The server returned an invalid workspace identity.');
  if (!identity.workspaces.length) throw new Error('This account has no upload-enabled workspace. Ask your administrator for access.');
  return identity;
}

export function selectWorkspace(identity, choice) {
  if (!choice && identity.workspaces.length !== 1) throw new Error('Choose the company you are recording for.');
  const workspace = choice
    ? identity.workspaces.find((w) => w.id === choice.id && w.kind === choice.kind)
    : identity.workspaces[0];
  if (!workspace || !UUID.test(workspace.id) || !['company', 'deal'].includes(workspace.kind))
    throw new Error('Choose an authorized workspace.');
  return workspace;
}

export function uploadBinding(config) {
  if (!UUID.test(config.userId) || !UUID.test(config.tenantId) || !UUID.test(config.workspace?.id))
    throw new Error('Reconnect your workspace before uploading.');
  return {
    url: workspaceURL(config.url), userId: config.userId, tenantId: config.tenantId,
    workspace: { id: config.workspace.id, kind: config.workspace.kind },
  };
}

function recordingDir(root, id) {
  if (!ID.test(String(id))) throw new Error('Invalid recording ID.');
  const base = fs.realpathSync(root);
  const dir = path.join(base, id);
  if (fs.realpathSync(dir) !== dir) throw new Error('Invalid recording directory.');
  return dir;
}

function readInside(dir, relative, maxBytes) {
  const base = fs.realpathSync(dir);
  const resolved = fs.realpathSync(path.join(base, relative));
  if (!resolved.startsWith(base + path.sep)) throw new Error('Upload file is outside this recording.');
  const stat = fs.statSync(resolved);
  if (!stat.isFile() || stat.size > maxBytes) throw new Error('Upload file exceeds the supported size limit.');
  return fs.readFileSync(resolved);
}

export function documentOptions(root, id) {
  const dir = recordingDir(root, id);
  return readFiles(dir).filter((f) => f.include !== false && f.snapshot && DOCUMENT_TYPES[f.ext?.toLowerCase()])
    .map((f) => ({ id: f.id, name: String(f.name), size_bytes: f.size_bytes ?? 0 }));
}

// The plan graph as it would be uploaded: compiled from the same raw events, minus the
// struck-out sections and the moves the employee removed in review. States and moves
// carry roles, action classes, control labels and slot names — no values.
export function reviewedPlan(dir, id, raw, manifest, excluded, { ownApps = [] } = {}) {
  const own = new Set(ownApps.map((a) => String(a).toLowerCase()));
  const events = parseEvents(raw).filter((e) => !own.has(String(e.app ?? '').toLowerCase()));
  const edits = fs.existsSync(path.join(dir, PLAN_EDITS_FILE)) ? JSON.parse(readInside(dir, PLAN_EDITS_FILE, MAX_ACTIVITY).toString('utf8')) : {};
  const { graph } = compilePlan({ recordingId: id, events, files: readFiles(dir), excluded, manifest });
  return applyPlanEdits(graph, edits);
}

export function planPreview(root, id, opts = {}) {
  const dir = recordingDir(root, id);
  const m = JSON.parse(readInside(dir, 'manifest.json', MAX_ACTIVITY).toString('utf8'));
  if (!m.ended_at) return null;
  const raw = readInside(dir, 'events.jsonl', 128 * 1024 * 1024).toString('utf8');
  const edits = fs.existsSync(path.join(dir, 'sections.json')) ? JSON.parse(readInside(dir, 'sections.json', MAX_ACTIVITY).toString('utf8')) : {};
  const excluded = buildSections(parseEvents(raw), m).filter((section) => edits[section.id]?.excluded);
  return planSummary(reviewedPlan(dir, id, raw, m, excluded, opts));
}

export function metadataEvents(raw, manifest, excluded = []) {
  const events = [];
  const start = Date.parse(manifest.started_at), end = Date.parse(manifest.ended_at);
  const hidden = [...excluded, ...(manifest.pauses ?? [])].map((range) => {
    const from = Date.parse(range.start), to = Date.parse(range.end ?? manifest.ended_at);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) throw new Error('Invalid privacy exclusion interval.');
    return [from, to];
  });
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue;
    const e = JSON.parse(line);
    if (!EVENT_TYPES.has(e.event_type)) continue;
    const at = Date.parse(e.timestamp);
    if (!Number.isFinite(at)) throw new Error('A recording event has an invalid timestamp.');
    if (at < start || at > end || hidden.some(([from, to]) => at >= from && at < to)) continue;
    const app = redactText(String(e.app || 'Unknown app')).replace(/[\x00-\x1f]/g, '').trim().slice(0, 128);
    if (!app || /private|1password|bitwarden|keychain|lastpass|keepass/i.test(app)) continue;
    events.push({ timestamp: new Date(at).toISOString(), event_type: e.event_type, app, count: 1 });
    if (events.length > 50000) throw new Error('This session is too large to upload; record a shorter session.');
  }
  return { schema_version: 1, events };
}

// The device's half of the leakage test, run on the whole cloud-bound package: nothing the
// recording typed, copied, opened or showed in a title may survive into it. Documents the
// employee selected are uploaded in full by choice and are not part of the check.
export function packageLeakage(payload, raw, manifest, files = [], { ownApps = [] } = {}) {
  const own = new Set(ownApps.map((a) => String(a).toLowerCase()));
  const events = parseEvents(raw).filter((e) => !own.has(String(e.app ?? '').toLowerCase()));
  const { values, titles } = recordingValues(events, files, manifest);
  return checkLeakage(payload, recordingContext({ values, titles, vocab: recordingVocabulary(events) }));
}

export function buildSubmissionPackage(root, id, config, { selectedFileIds = [], consent = false, sharePlan = false, ownApps = [] } = {}) {
  if (consent !== true) throw new Error('Confirm the sharing package before uploading.');
  const consentVersion = config.consentVersion ?? 'activity-metadata-v1';
  if (!CONSENT_VERSIONS.has(consentVersion)) throw new Error('Reconnect your workspace to accept the current recording and sharing notice.');
  if (sharePlan === true && consentVersion !== CONSENT_VERSION)
    throw new Error('Sharing a plan needs the current recording and sharing notice. Reconnect your workspace to accept it.');
  const binding = uploadBinding(config);
  if (!UUID.test(config.deviceId)) throw new Error('Missing recorder device identifier.');
  if (!Array.isArray(selectedFileIds) || new Set(selectedFileIds).size !== selectedFileIds.length || selectedFileIds.length > 10)
    throw new Error('Select at most ten distinct document snapshots.');
  const dir = recordingDir(root, id);
  const m = JSON.parse(readInside(dir, 'manifest.json', MAX_ACTIVITY).toString('utf8'));
  if (m.recording_id !== id || !m.ended_at || !Number.isFinite(Date.parse(m.started_at)) || !Number.isFinite(Date.parse(m.ended_at)))
    throw new Error('Stop the recording before uploading.');
  const enrollment = path.join(dir, 'workspace-binding.json');
  if (fs.existsSync(enrollment) && JSON.stringify(JSON.parse(readInside(dir, 'workspace-binding.json', 4096))) !== JSON.stringify(binding))
    throw new Error('Reconnect to the workspace where this session was recorded.');
  const raw = readInside(dir, 'events.jsonl', 128 * 1024 * 1024).toString('utf8');
  const edits = fs.existsSync(path.join(dir, 'sections.json'))
    ? JSON.parse(readInside(dir, 'sections.json', MAX_ACTIVITY).toString('utf8')) : {};
  const excluded = buildSections(parseEvents(raw), m).filter((section) => edits[section.id]?.excluded);
  const events = metadataEvents(raw, m, excluded);
  const data = new Map();
  const artifacts = [];
  const add = (artifactId, kind, filename, contentType, bytes) => {
    const limit = kind === 'activity' ? MAX_ACTIVITY : kind === 'plan' ? MAX_PLAN : MAX_FILE;
    if (!bytes.length || bytes.length > limit) throw new Error('Upload artifact exceeds its size limit.');
    data.set(artifactId, bytes);
    artifacts.push({ id: artifactId, kind, filename, content_type: contentType, size_bytes: bytes.length, sha256: hash(bytes) });
  };
  const files = readFiles(dir);
  // Timestamps and event types cannot carry a recorded value; the app names and the plan can.
  const cloudBound = { apps: [...new Set(events.events.map((e) => e.app))] };
  if (sharePlan === true) {
    const plan = reviewedPlan(dir, id, raw, m, excluded, { ownApps });
    if (plan.nodes.length && plan.edges.length) cloudBound.plan = plan;
  }
  const leak = packageLeakage(cloudBound, raw, m, files, { ownApps });
  if (!leak.ok) throw new Error(`This package did not pass the privacy check and was not queued: ${describeFailures(leak)}`);
  add('activity', 'activity', 'activity.json', 'application/json', Buffer.from(JSON.stringify(events)));
  if (cloudBound.plan) add('plan', 'plan', 'plan.json', 'application/json', Buffer.from(JSON.stringify(cloudBound.plan)));
  for (const fileId of selectedFileIds) {
    const file = files.find((f) => f.id === fileId && f.include !== false && f.snapshot);
    if (!file || !/^[a-f0-9]{12}$/.test(fileId) || !DOCUMENT_TYPES[file.ext?.toLowerCase()] ||
      !new RegExp(`^files/${fileId}/[A-Za-z0-9][A-Za-z0-9._-]*$`).test(file.snapshot))
      throw new Error('A selected document snapshot is not available.');
    const filename = String(file.name).replace(/[\/\\\x00-\x1f]/g, '_').slice(0, 255);
    add(`document-${fileId}`, 'document', filename, DOCUMENT_TYPES[file.ext.toLowerCase()], readInside(dir, file.snapshot, MAX_FILE));
  }
  if (artifacts.reduce((n, a) => n + a.size_bytes, 0) > MAX_TOTAL) throw new Error('The approved package exceeds 50 MiB.');
  const elapsed = Math.floor((Date.parse(m.ended_at) - Date.parse(m.started_at)) / 1000);
  if (elapsed < 0) throw new Error('Invalid recording duration.');
  return {
    binding,
    manifest: {
      format_version: 2, sharing_policy: SHARING_POLICY, consent: true, consent_version: consentVersion,
      device_id: config.deviceId, source_id: id, workspace: binding.workspace,
      started_at: new Date(m.started_at).toISOString(), ended_at: new Date(m.ended_at).toISOString(),
      active_seconds: Math.max(0, Math.min(elapsed, Math.floor(Number(m.active_seconds) || 0))), artifacts,
    },
    data,
  };
}

export class SubmissionQueue {
  constructor(home, { fetchImpl = fetch, onChange = () => {}, now = () => Date.now(), isCurrent = () => true } = {}) {
    this.root = path.join(home, 'upload-queue');
    fs.mkdirSync(this.root, { recursive: true, mode: 0o700 });
    this.fetch = fetchImpl;
    this.onChange = onChange;
    this.now = now;
    this.isCurrent = isCurrent;
    this.running = null;
  }

  entries() {
    const entries = [];
    for (const entry of fs.readdirSync(this.root, { withFileTypes: true })) {
      if (!entry.isDirectory() || !/^[0-9a-f]{64}$/.test(entry.name)) continue;
      const file = path.join(this.root, entry.name, 'state.json');
      if (!fs.existsSync(file)) continue;
      const state = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (state.queueId !== entry.name) throw new Error('Upload queue identity mismatch.');
      entries.push(state);
    }
    return entries;
  }

  save(state) {
    savePrivate(path.join(this.root, state.queueId, 'state.json'), state);
    this.onChange(state);
  }

  queueIdFor(config, id) {
    const binding = uploadBinding(config);
    return hash(JSON.stringify([binding.url, binding.userId, config.deviceId, id]));
  }

  // The accepted entry for one local recording, bound to the connected workspace.
  acceptedEntry(config, id) {
    const state = this.entries().find((entry) => entry.queueId === this.queueIdFor(config, id));
    if (!state || state.status !== 'accepted' || !UUID.test(state.submission?.id ?? '')) throw new Error('Upload this session before working with its report.');
    if (JSON.stringify(state.binding) !== JSON.stringify(uploadBinding(config))) throw new Error('Reconnect the workspace this session was uploaded to.');
    return state;
  }

  // Cloud analysis and the draft report live on the server; the local copy is a
  // cache for the dashboard. `detail` is a GET/POST /recorder/submissions/{id} body.
  applyAnalysis(state, detail) {
    if (detail.id !== state.submission.id) throw new Error('The server answered for a different submission.');
    state.analysis = {
      status: detail.analysis_status ?? 'not_started',
      error: detail.analysis_error ?? null,
      run_id: detail.analysis_run_id ?? null,
      publication: detail.publication_status ?? 'draft',
      report: detail.report ?? null,
      checked_at: this.now(),
    };
    this.save(state);
    return state;
  }

  // Poll accepted uploads whose analysis is still in flight (every 30 s at most).
  async refresh(config, { force = false } = {}) {
    for (const state of this.entries()) {
      if (state.status !== 'accepted' || JSON.stringify(state.binding) !== JSON.stringify(uploadBinding(config))) continue;
      const pending = !state.analysis || ['not_started', 'queued', 'running'].includes(state.analysis.status);
      if (!force && (!pending || (state.analysis?.checked_at ?? 0) + 30000 > this.now())) continue;
      try {
        this.applyAnalysis(state, await cloudRequest(config, `/recorder/submissions/${state.submission.id}`, {}, this.fetch));
      } catch {
        if (state.analysis) { state.analysis.checked_at = this.now(); this.save(state); }
      }
    }
  }

  async submissionAction(config, id, action, body) {
    const state = this.acceptedEntry(config, id);
    const endpoint = `/recorder/submissions/${state.submission.id}${action ? `/${action}` : ''}`;
    const options = action ? { method: 'POST', body: JSON.stringify(body ?? {}) } : {};
    return this.applyAnalysis(state, await cloudRequest(config, endpoint, options, this.fetch)).analysis;
  }

  status(config, id) { return this.submissionAction(config, id, null); }
  answer(config, id, answers) {
    if (!answers || typeof answers !== 'object' || !Object.keys(answers).length) throw new Error('Answer at least one question.');
    return this.submissionAction(config, id, 'answers', { answers });
  }
  // The employee's second, explicit consent: the draft becomes visible to the workspace.
  publish(config, id, { consent } = {}) {
    if (consent !== true) throw new Error('Confirm that the report may be shared with your workspace.');
    return this.submissionAction(config, id, 'publish', { consent: true });
  }
  reanalyze(config, id) { return this.submissionAction(config, id, 'analyze', {}); }

  enqueue(root, id, config, options) {
    const binding = uploadBinding(config);
    const queueId = this.queueIdFor(config, id);
    const previous = this.entries().find((state) => state.queueId === queueId);
    if (previous) {
      if (JSON.stringify(previous.binding) !== JSON.stringify(binding)) throw new Error('This upload is already bound to another workspace.');
      return previous;
    }
    const { manifest, data } = buildSubmissionPackage(root, id, config, options);
    const dir = path.join(this.root, queueId);
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    for (const [artifactId, bytes] of data) fs.writeFileSync(path.join(dir, `${artifactId}.bin`), bytes, { mode: 0o600 });
    const state = {
      queueId, binding, manifest, status: 'queued', attempts: 0, nextAttempt: 0,
      uploaded: [], submission: null, receipt: null, error: null,
    };
    this.save(state);
    return state;
  }

  async flush(config, { force = false } = {}) {
    if (this.running) return this.running;
    this.running = this.drain(config, force).finally(() => { this.running = null; });
    return this.running;
  }

  async drain(config, force) {
    const binding = JSON.stringify(uploadBinding(config));
    for (const state of this.entries()) {
      if (JSON.stringify(state.binding) !== binding || state.status === 'accepted' || (!force && state.nextAttempt > this.now())) continue;
      state.status = 'uploading';
      state.error = null;
      state.attempts += 1;
      this.save(state);
      try {
        const checkConnection = () => {
          if (!this.isCurrent(state.binding)) throw new Error('Upload paused; reconnect the original workspace to continue.');
        };
        checkConnection();
        state.submission = await cloudRequest(config, '/recorder/submissions', { method: 'POST', body: JSON.stringify(state.manifest) }, this.fetch);
        if (!UUID.test(state.submission.id)) throw new Error('The server returned an invalid submission identifier.');
        const expectedId = state.submission.id, expectedHash = state.submission.manifest_hash;
        this.save(state);
        if (state.submission.upload_status !== 'accepted') {
          const endpoint = `/recorder/submissions/${state.submission.id}`;
          checkConnection();
          const { uploads } = await cloudRequest(config, `${endpoint}/upload-urls`, { method: 'POST', body: '{}' }, this.fetch);
          for (const spec of state.manifest.artifacts) {
            checkConnection();
            if (state.uploaded.includes(spec.id)) continue;
            if (!/^[a-z0-9_-]{1,64}$/.test(spec.id)) throw new Error('Invalid artifact identifier in the upload queue.');
            const upload = uploads.find((u) => u.artifact_id === spec.id);
            if (!upload) throw new Error('The server did not authorize a required artifact.');
            const uploadUrl = new URL(upload.url);
            if (uploadUrl.protocol !== 'https:' && !(uploadUrl.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(uploadUrl.hostname)))
              throw new Error('The server returned an insecure upload address.');
            const bytes = readInside(path.join(this.root, state.queueId), `${spec.id}.bin`, MAX_FILE);
            if (bytes.length !== spec.size_bytes || hash(bytes) !== spec.sha256) throw new Error('The local upload package has changed.');
            const res = await this.fetch(upload.url, {
              method: 'PUT', headers: upload.headers, body: bytes, redirect: 'error', signal: AbortSignal.timeout(120000),
            });
            if (!res.ok) throw new Error('An artifact upload failed; it will be retried.');
            state.uploaded.push(spec.id);
            this.save(state);
          }
          try {
            checkConnection();
            state.submission = await cloudRequest(config, `${endpoint}/complete`, { method: 'POST', body: '{}' }, this.fetch);
          } catch (error) {
            state.uploaded = [];
            throw error;
          }
        }
        const result = state.submission;
        if (result.id !== expectedId || result.manifest_hash !== expectedHash || !/^[0-9a-f]{64}$/.test(expectedHash) ||
          result.upload_status !== 'accepted' || result.receipt?.submission_id !== result.id ||
          result.receipt?.manifest_hash !== result.manifest_hash || result.receipt?.artifact_count !== state.manifest.artifacts.length ||
          result.source_id !== state.manifest.source_id || result.device_id !== state.manifest.device_id ||
          result.workspace?.id !== state.manifest.workspace.id || result.workspace?.kind !== state.manifest.workspace.kind)
          throw new Error('The server has not verified this upload package yet.');
        state.receipt = result.receipt;
        state.status = 'accepted';
        state.nextAttempt = 0;
      } catch (error) {
        state.status = 'failed';
        state.error = error.message;
        state.nextAttempt = [401, 403, 404, 422].includes(error.status)
          ? Number.MAX_SAFE_INTEGER
          : this.now() + Math.min(300000, 5000 * 2 ** Math.min(state.attempts, 6));
      }
      this.save(state);
    }
  }
}
