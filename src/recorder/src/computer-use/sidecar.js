// The Python device sidecar (`src/vista_device`, `python -m vista_device`): one `observe()` for
// recording and execution, on top of browser-use (pages) and macOS-use (the front window's
// accessibility tree). The recorder spawns it, speaks newline-delimited JSON over its
// stdin/stdout and never lets it touch the network — every `cloud` block the sidecar returns
// has already been through the leakage test, and `leakage.ok === false` means it stays here.
//
//   VISTA_DEVICE_PYTHON  interpreter of the sidecar's own venv (see requirements-device.txt);
//                        default `python3`. Unset or missing → no sidecar, the JS drivers stay.
//
// `SidecarHarness` is the harness shape (`kind, supported, capabilities(), perform(), close()`)
// over one sidecar driver; it is `supported` only after `selfTest()` observed a frame through
// the real library on this computer (design §9: harnesses are advertised only after a
// recorded device self-test).
import { spawn } from 'node:child_process';
import readline from 'node:readline';

const RPC_TIMEOUT_MS = 30_000;
const HEALTH_TIMEOUT_MS = 10_000;

export class SidecarError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export class SidecarClient {
  constructor({ command = process.env.VISTA_DEVICE_PYTHON || 'python3', args = ['-m', 'vista_device'], cwd, env, log = () => {}, healthTimeoutMs = HEALTH_TIMEOUT_MS } = {}) {
    this.command = command;
    this.healthTimeoutMs = healthTimeoutMs;
    this.args = args;
    this.cwd = cwd;
    this.env = env;
    this.log = log;
    this.proc = null;
    this.pending = new Map();
    this.nextId = 1;
    this.health = null;
  }

  get alive() {
    return !!this.proc && this.proc.exitCode === null && !this.proc.killed;
  }

  // Spawn and health-check; resolves to the health block or null when the sidecar cannot run
  // here (no interpreter, package missing). Never throws: a missing sidecar is a supported state.
  async start() {
    if (this.alive) return this.health;
    try {
      this.proc = spawn(this.command, this.args, { cwd: this.cwd, env: { ...process.env, PYTHONUNBUFFERED: '1', ...(this.env ?? {}) }, stdio: ['pipe', 'pipe', 'pipe'] });
    } catch (err) {
      this.log(`sidecar not started: ${err.message}`);
      this.proc = null;
      return null;
    }
    const proc = this.proc;
    proc.on('error', (err) => this.log(`sidecar error: ${err.message}`));
    proc.on('exit', (code) => {
      this.log(`sidecar exited (${code})`);
      for (const [, p] of this.pending) p.reject(new SidecarError('sidecar_down', 'The device sidecar stopped.'));
      this.pending.clear();
      if (this.proc === proc) this.proc = null;
    });
    proc.stderr.on('data', (d) => this.log(`sidecar: ${String(d).trim().slice(0, 300)}`));
    readline.createInterface({ input: proc.stdout }).on('line', (line) => this._onLine(line));
    try {
      this.health = await this.call('health', {}, this.healthTimeoutMs);
    } catch (err) {
      this.log(`sidecar health failed: ${err.message}`);
      await this.stop();
      return null;
    }
    return this.health;
  }

  _onLine(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      return;
    }
    const p = this.pending.get(msg.id);
    if (!p) return;
    this.pending.delete(msg.id);
    clearTimeout(p.timer);
    if (msg.error) p.reject(new SidecarError(msg.error.code ?? 'sidecar_error', msg.error.message ?? 'sidecar error'));
    else p.resolve(msg.result);
  }

  call(method, params = {}, timeoutMs = RPC_TIMEOUT_MS) {
    if (!this.alive) return Promise.reject(new SidecarError('sidecar_down', 'The device sidecar is not running.'));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new SidecarError('sidecar_timeout', `The device sidecar did not answer ${method} in time.`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.proc.stdin.write(JSON.stringify({ id, method, params }) + '\n');
    });
  }

  context({ values = [], titles = [], vocabulary = null } = {}) {
    return this.call('context', { values, titles, vocabulary });
  }

  // A frame from candidates the caller already has (the recorder's own AX walk, the CDP
  // driver) — the same state function, no driver needed.
  frame(params) {
    return this.call('frame', params);
  }

  async stop() {
    const proc = this.proc;
    if (!proc) return;
    this.proc = null;
    try {
      proc.stdin.end();
    } catch {}
    await new Promise((resolve) => {
      const t = setTimeout(() => {
        try {
          proc.kill();
        } catch {}
        resolve();
      }, 2000);
      proc.once('exit', () => {
        clearTimeout(t);
        resolve();
      });
    });
  }
}

export class SidecarHarness {
  constructor({ kind, client, options = {}, settings = null }) {
    this.kind = kind;
    this.client = client;
    this.options = options;
    this.settings = settings;
    this.supported = false;
    this.selfTest = null;
    this._caps = [];
  }

  // Open the driver and take one real observation. Only a frame that came back through the
  // library on this computer advertises the kind.
  async probe() {
    const at = new Date().toISOString();
    try {
      const opened = await this.client.call('open', { kind: this.kind, ...this.options });
      const obs = await this.client.call('observe', { kind: this.kind });
      this._caps = opened.capabilities ?? [];
      this.supported = Array.isArray(obs?.observation?.l0) && obs.observation.l0.length > 0 && obs.leakage?.ok !== false && obs.observation.settled !== false;
      this.selfTest = { at, ok: this.supported, l0: obs?.observation?.l0 ?? [], settled: obs?.observation?.settled ?? null };
    } catch (err) {
      this.supported = false;
      this.selfTest = { at, ok: false, error: { code: err.code ?? 'sidecar_error', message: err.message } };
    }
    return this.selfTest;
  }

  capabilities() {
    return this.supported ? [...this._caps] : [];
  }

  async perform(step) {
    if (step.action === 'screenshot' && this.settings && !this.settings().screenshots) {
      return refusal(step, 'consent_required', 'Screenshots are not covered by this session\'s consent.');
    }
    let res;
    try {
      res = await this.client.call('perform', { kind: this.kind, step });
    } catch (err) {
      return refusal(step, err.code ?? 'harness_error', err.message);
    }
    // Only the cloud block, and only when its own leakage test passed; the local observation
    // (raw url/title/text) never enters a step result.
    const observation = res.leakage?.ok && res.cloud ? { ...res.cloud, observation_id: res.observation?.observation_id ?? null, settled: res.observation?.settled ?? true } : null;
    return { ok: !!res.ok, description: res.description ?? '', observation, result: res.result ?? null, evidence: res.evidence ?? null, error: res.error ?? null };
  }

  async close() {
    try {
      await this.client.call('close', { kind: this.kind });
    } catch {}
  }
}

function refusal(step, code, message) {
  return { ok: false, description: `${step.action} refused: ${message}`, observation: null, result: null, evidence: null, error: { code, message } };
}
