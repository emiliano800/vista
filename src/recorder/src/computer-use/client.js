// The recorder's half of the computer-use protocol. The cloud never reaches this computer:
// on the idle tick the client announces the device and lists runs waiting for a harness; the
// employee accepts one (explicit consent, every time); the client then polls the session,
// performs each step the server files (policy check first), posts the result, and stops
// when the run ends, the lease is lost, the employee presses Stop or the app quits.
//
// Electron-free: `config()` returns the cloud connection (url + personal key) or null,
// `fetchImpl` is injectable, and everything the employee can see is written to a local log
// under <home>/computer-use/<session>/ (mode 0600) *before* anything is sent.
import fs from 'node:fs';
import path from 'node:path';

import { cloudRequest } from '../cloud.js';
import { capabilitiesOf, defaultHarnesses } from './harnesses.js';
import { CONSENT_VERSION, PolicyError, validateStep } from './policy.js';

export const POLL_MS = 3000;
export const BASE = '/recorder/computer-use';
const TERMINAL = new Set(['succeeded', 'failed', 'stopped', 'closed']);

export class ComputerUseClient {
  constructor({ home, deviceId, platform = process.platform, version = '', config, fetchImpl = fetch, harnesses = defaultHarnesses(), now = () => Date.now(), onChange = () => {}, pollMs = POLL_MS, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
    this.home = home;
    this.deviceId = deviceId;
    this.platform = platform;
    this.version = version;
    this.config = config;
    this.fetchImpl = fetchImpl;
    this.harnesses = harnesses;
    this.now = now;
    this.onChange = onChange;
    this.pollMs = pollMs;
    this.sleep = sleep;
    this.device = null;
    this.offers = [];
    this.interrupted = null; // a session the server thinks is ours but this process does not hold
    this.session = null; // the active local session
    this.lastError = null;
    this._loop = null;
  }

  capabilities() {
    return { ...capabilitiesOf(this.harnesses), recorder_version: this.version };
  }

  status() {
    return {
      enabled: !!this.config(),
      device: this.device,
      capabilities: this.capabilities(),
      offers: this.offers,
      interrupted: this.interrupted,
      active: this.session ? this._summary() : null,
      error: this.lastError,
    };
  }

  steps(sessionId) {
    if (this.session && this.session.id === sessionId) return this.session.log;
    try {
      return fs
        .readFileSync(path.join(this.home, 'computer-use', sessionId, 'steps.jsonl'), 'utf8')
        .split('\n')
        .filter(Boolean)
        .map((l) => JSON.parse(l).line);
    } catch {
      return [];
    }
  }

  // Presence + offers. Cheap, one request, safe to call every 30 s whether or not anything
  // is happening. Never throws: a workspace that is down simply means no offers.
  async tick() {
    const config = this.config();
    if (!config) {
      this.offers = [];
      return this.status();
    }
    const caps = this.capabilities();
    const qs = new URLSearchParams({ device_id: this.deviceId, platform: this.platform, browser: String(caps.browser), desktop: String(caps.desktop), recorder_version: this.version });
    try {
      const data = await cloudRequest(config, `${BASE}/sessions?${qs}`, { method: 'GET' }, this.fetchImpl);
      this.device = data.device ?? null;
      this.offers = data.offers ?? [];
      this.interrupted = data.active && (!this.session || this.session.id !== data.active.id) ? data.active : null;
      this.lastError = null;
    } catch (e) {
      this.lastError = e.message;
    }
    this.onChange(this.status());
    return this.status();
  }

  // Start = claim with consent. The consent object is built here from the employee's explicit
  // choices; nothing else in the recorder can construct one.
  async start(runId, options = {}) {
    if (options.consent !== true) throw new PolicyError('consent_required', 'Read the notice and tick the consent box before starting a session.');
    if (this.session) throw new PolicyError('busy', 'A computer-use session is already running on this computer.');
    const config = this.config();
    if (!config) throw new PolicyError('not_connected', 'Connect your company in Settings first.');
    const offer = this.offers.find((o) => o.run_id === runId);
    const caps = this.capabilities();
    const missing = (offer?.harness_kinds ?? []).filter((k) => !caps[k]);
    if (missing.length) throw new PolicyError('disallowed_harness', `This computer cannot provide the ${missing.join(', ')} harness.`);
    const consent = { version: CONSENT_VERSION, accepted_at: new Date(this.now()).toISOString(), screenshots: options.shareScreenshots === true };
    const view = await cloudRequest(config, `${BASE}/sessions/${runId}/claim`, { method: 'POST', body: JSON.stringify({ device_id: this.deviceId, consent, capabilities: caps }) }, this.fetchImpl);
    this.session = this._openSession(view, consent);
    this.offers = this.offers.filter((o) => o.run_id !== runId);
    this.interrupted = null;
    this._note('Session started — steps run only after this computer checks each one.');
    this._loop = this._run().catch((e) => this._end('failed', e.message));
    this.onChange(this.status());
    return this._summary();
  }

  async stop(reason = 'the employee pressed Stop') {
    const s = this.session;
    if (!s) return { ok: true, active: null };
    s.stopping = reason;
    const config = this.config();
    if (config) {
      try {
        await cloudRequest(config, `${BASE}/sessions/${s.id}/stop`, { method: 'POST', body: JSON.stringify({ device_id: this.deviceId, lease_token: s.token, reason }) }, this.fetchImpl);
      } catch (e) {
        this.lastError = e.message;
      }
    }
    await this._end('stopped', reason);
    return { ok: true, active: null };
  }

  // ---- internals ----

  _openSession(view, consent) {
    const dir = path.join(this.home, 'computer-use', view.id);
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    const session = { id: view.id, runId: view.run_id, view, token: view.lease?.token, consent, dir, startedAt: this.now(), stepsDone: 0, lastObservationId: null, log: [], stopping: null };
    this._write(session, 'session.json', { id: view.id, run_id: view.run_id, workflow: view.workflow, harness_kinds: view.harness_kinds, limits: view.limits, consent, started_at: new Date(session.startedAt).toISOString(), status: 'active' });
    return session;
  }

  _summary() {
    const s = this.session;
    return {
      id: s.id,
      run_id: s.runId,
      workflow: s.view.workflow,
      status: s.stopping ? 'stopping' : s.view.status,
      pause_reason: s.view.pause_reason ?? null,
      step: s.stepsDone,
      max_steps: s.view.limits?.max_steps ?? null,
      elapsed_s: Math.round((this.now() - s.startedAt) / 1000),
      harness_kinds: s.view.harness_kinds,
      screenshots_shared: !!s.consent.screenshots,
      log: s.log.slice(-50),
    };
  }

  async _run() {
    const s = this.session;
    while (this.session === s && !s.stopping) {
      const config = this.config();
      if (!config) return this._end('stopped', 'the workspace was disconnected');
      let view;
      try {
        const qs = new URLSearchParams({ device_id: this.deviceId, lease_token: s.token });
        view = await cloudRequest(config, `${BASE}/sessions/${s.id}?${qs}`, { method: 'GET' }, this.fetchImpl);
      } catch (e) {
        if (e.status === 409 || e.status === 403 || e.status === 404) return this._end('stopped', `the session lease was lost (${e.message})`);
        this.lastError = e.message; // transient: keep polling
        await this.sleep(this.pollMs);
        continue;
      }
      s.view = { ...s.view, ...view, lease: { ...view.lease, token: s.token } };
      if (TERMINAL.has(view.status)) return this._end(view.status, null);
      if (view.status === 'paused_for_approval' && !s.pausedNoted) {
        s.pausedNoted = true;
        this._note(`Waiting for a decision in the workspace${view.pause_reason ? `: ${view.pause_reason}` : ''}.`);
      } else if (view.status !== 'paused_for_approval') s.pausedNoted = false;
      if (view.pending_step) await this._perform(s, config, view.pending_step);
      else {
        this.onChange(this.status());
        await this.sleep(this.pollMs);
      }
    }
  }

  async _perform(s, config, step) {
    const startedAt = new Date(this.now()).toISOString();
    let r;
    try {
      validateStep(step, s.view, { capabilities: capabilitiesOf(this.harnesses), stepsDone: s.stepsDone, startedAt: s.startedAt, lastObservationId: s.lastObservationId, now: this.now });
      r = await this.harnesses[step.harness].perform(step);
    } catch (e) {
      r = { ok: false, description: `${step.action ?? 'step'} refused: ${e.message}`, observation: null, result: null, evidence: null, error: { code: e.code ?? 'harness_error', message: e.message } };
    }
    if (r.observation?.observation_id) s.lastObservationId = r.observation.observation_id;
    let evidence = r.evidence ?? null;
    if (evidence && !s.consent.screenshots) evidence = { ...evidence, screenshot_base64: undefined }; // stays on this computer
    const body = { device_id: this.deviceId, lease_token: s.token, ok: !!r.ok, description: String(r.description ?? '').slice(0, 500), observation: r.observation ?? null, result: r.result ?? null, evidence, error: r.error ?? null, started_at: startedAt, finished_at: new Date(this.now()).toISOString() };
    s.stepsDone += 1;
    const line = `${body.ok ? '✓' : '✗'} ${step.seq ?? s.stepsDone} ${step.harness} · ${body.description}`;
    s.log.push(line);
    this._append(s, 'steps.jsonl', { request: step, result: { ...body, lease_token: undefined, evidence: evidence ? { ...evidence, screenshot_base64: undefined } : null }, line, at: body.finished_at });
    this.onChange(this.status());
    try {
      await cloudRequest(config, `${BASE}/steps/${step.step_id}/result`, { method: 'POST', body: JSON.stringify(body) }, this.fetchImpl);
    } catch (e) {
      if (e.status === 409 || e.status === 403) return this._end('stopped', `the session lease was lost (${e.message})`);
      this.lastError = e.message; // the server will re-offer the step; the next poll retries
    }
  }

  _note(text) {
    const s = this.session;
    if (!s) return;
    const line = `⏸ ${text}`;
    s.log.push(line);
    this._append(s, 'steps.jsonl', { note: text, line, at: new Date(this.now()).toISOString() });
    this.onChange(this.status());
  }

  async _end(status, reason) {
    const s = this.session;
    if (!s) return;
    this.session = null;
    for (const h of Object.values(this.harnesses)) {
      try {
        await h.close();
      } catch {
        /* best effort */
      }
    }
    this._write(s, 'session.json', { id: s.id, run_id: s.runId, workflow: s.view.workflow, harness_kinds: s.view.harness_kinds, limits: s.view.limits, consent: s.consent, started_at: new Date(s.startedAt).toISOString(), ended_at: new Date(this.now()).toISOString(), status, reason, steps: s.stepsDone });
    this.lastSession = { id: s.id, status, reason, steps: s.stepsDone, workflow: s.view.workflow };
    this.onChange(this.status());
  }

  _write(s, name, obj) {
    fs.writeFileSync(path.join(s.dir, name), JSON.stringify(obj, null, 2), { mode: 0o600 });
  }

  _append(s, name, obj) {
    fs.appendFileSync(path.join(s.dir, name), JSON.stringify(obj) + '\n', { mode: 0o600 });
  }
}
