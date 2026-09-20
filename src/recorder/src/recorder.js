// Capture engine: turns OS hooks into taskmining RawEvent lines.
//
//   uiohook-napi   -> mouse clicks / wheel / keys / shortcut combos (global, cross-platform)
//   get-windows    -> foreground app + window title (+ URL on macOS) polled every 500 ms
//   capture window -> screen frames + webm video, requested via `frameProvider`;
//                     a tiny grayscale thumbnail (`thumbProvider`) is polled so a
//                     screenshot is taken when the screen actually changes
//
// Copy and paste are linked: the clipboard text is hashed (salted per recording)
// on copy; a later paste with the same hash carries the source app/window, so
// "re-keyed from Outlook into QuickBooks" is a fact in the log, not a guess.
//
// Every event is redacted before it touches disk and stamped with the current
// foreground app/window so the pipeline can abstract it into a business step.
import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { FILES_FILE, FileTracker } from './files.js';
import { redactEvent, redactText } from './redact.js';

const MOD_KEYS = new Set(['ctrl', 'alt', 'shift', 'meta']);
const KEY_NAMES = {
  Enter: 'Enter', Tab: 'Tab', Backspace: 'Backspace', Delete: 'Delete', Escape: 'Esc',
  Space: 'Space', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
  Home: 'Home', End: 'End', PageUp: 'PgUp', PageDown: 'PgDn', Insert: 'Ins', PrintScreen: 'PrtSc',
};
const IS_MAC = process.platform === 'darwin';

export const DEFAULT_SETTINGS = {
  redact: false,              // mask emails/phones/cards/IBANs in titles and clipboard text before writing to disk
  keyContent: false,          // record typed characters (false = counts + named keys only)
  files: true,                // track documents on screen (macOS) and snapshot their last version at Stop
  clipboard: true,            // record clipboard text on copy/paste
  screenshots: true,          // JPEG frame on every focus change, on screen change + every `frameEverySec`
  video: true,                // low-fps webm of the screen alongside events
  frameEverySec: 15,
  changeDetect: true,         // compare a small thumbnail every `changePollMs`, shoot when it differs
  changePollMs: 750,
  changeThreshold: 0.04,      // fraction of thumbnail pixels that must change
  changeMinGapMs: 1500,       // never more than one change-shot per this window
  privateApps: ['1Password', 'Bitwarden', 'KeePass', 'LastPass', 'Keychain Access', 'Signal', 'WhatsApp'],
  privateTitles: ['password', 'bank', 'banking', 'incognito', 'private browsing'],
  ownApps: ['Vista', 'Electron'], // the recorder itself: time and clicks here are not the employee's work
  openaiApiKey: '',           // AI explanations after a session; OPENAI_API_KEY / VISTA_OPENAI_API_KEY env overrides
  openaiModel: '', // empty → provider default (explain.js); VISTA_OPENAI_MODEL env overrides
  clarifyScreenshots: false,  // also send up to 3 low-res frames per section to OpenAI
  demoMode: false,            // simulated apps/input instead of real hooks (same as --demo); needs a restart
};

export function loadSettings(file) {
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(fs.readFileSync(file, 'utf8')) };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export class Recorder extends EventEmitter {
  /**
   * @param {object} opts
   * @param {string} opts.root  directory holding all recordings
   * @param {object} opts.settings
   * @param {object|null} opts.hook  uIOhook instance (null in demo mode)
   * @param {Function|null} opts.activeWindow  get-windows activeWindow (null in demo mode)
   * @param {Function|null} opts.frameProvider  async () => Buffer (JPEG) | null
   * @param {Function|null} opts.thumbProvider  async () => { width, height, gray: Uint8Array } | null
   * @param {Function|null} opts.readClipboard  () => string
   * @param {Function|null} opts.fileProbe  async (win) => string[]  document paths shown by the frontmost app
   */
  constructor(opts) {
    super();
    this.root = opts.root;
    this.settings = opts.settings;
    this.hook = opts.hook;
    this.activeWindow = opts.activeWindow;
    this.frameProvider = opts.frameProvider ?? null;
    this.thumbProvider = opts.thumbProvider ?? null;
    this.readClipboard = opts.readClipboard ?? (() => '');
    this.fileProbe = opts.fileProbe ?? null;
    this.files = null;
    this.keyNames = opts.keyNames ?? new Map(); // uiohook keycode -> UiohookKey name
    this.user = opts.user ?? os.userInfo().username;

    this.state = 'idle'; // idle | recording | paused | finishing
    this.dir = null;
    this.stream = null;
    this.current = { app: '', title: '', url: '', private: false };
    this.counts = this._zeroCounts();
    this.appSeconds = {};
    this.lastTick = 0;
    this.startedAt = null;
    this.intent = '';
    this.pausedMs = 0;
    this._pausedAt = null;
    this.frameNo = 0;
    this._lastFrameAt = 0;
    this._mods = new Set();
    this._timers = [];
    this._lastScroll = 0;
    this._log = [];
    this._thumb = null;
    this._thumbBusy = false;
    this._lastClip = null;
  }

  _zeroCounts() {
    return { click: 0, key: 0, focus: 0, copy: 0, paste: 0, transfer: 0, scroll: 0, shortcut: 0, screen: 0, redactions: 0, total: 0 };
  }

  // ---- lifecycle -----------------------------------------------------------

  start() {
    if (this.state !== 'idle') return this.status();
    const now = new Date();
    const id = now.toISOString().replace(/[:.]/g, '-').slice(0, 19);
    this.recordingId = id;
    this.dir = path.join(this.root, id);
    fs.mkdirSync(path.join(this.dir, 'shots'), { recursive: true });
    this.stream = fs.createWriteStream(path.join(this.dir, 'events.jsonl'), { flags: 'a' });
    this.startedAt = now;
    this.intent = '';
    this.pausedMs = 0;
    this.pauses = [];
    this.counts = this._zeroCounts();
    this.appSeconds = {};
    this.frameNo = 0;
    this._log = [];
    this._thumb = null;
    this._lastClip = null;
    this.files = this.settings.files ? new FileTracker() : null;
    this.state = 'recording';
    this._writeManifest();
    this._attachHooks();
    this._timers.push(setInterval(() => this._pollWindow(), 500));
    this._timers.push(setInterval(() => this._periodicFrame(), 1000));
    if (this.settings.changeDetect && this.thumbProvider) this._timers.push(setInterval(() => this._changeTick(), this.settings.changePollMs));
    if (this.files && this.fileProbe) this._timers.push(setInterval(() => this.state === 'recording' && this._probeFiles(this._lastWin, this.current.app, this.current.private), 5000));
    this._pollWindow(true);
    this._emitStatus();
    return this.status();
  }

  pause() {
    if (this.state !== 'recording') return this.status();
    this.state = 'paused';
    this._pausedAt = Date.now();
    this.pauses.push({ start: new Date(this._pausedAt).toISOString(), end: null });
    this.files?.closeAll(this._pausedAt);
    this._note('paused by employee');
    this._emitStatus();
    return this.status();
  }

  resume() {
    if (this.state !== 'paused') return this.status();
    this.pausedMs += Date.now() - this._pausedAt;
    this._pausedAt = null;
    this.pauses[this.pauses.length - 1].end = new Date().toISOString();
    this.state = 'recording';
    this._note('resumed');
    this._pollWindow(true);
    this._emitStatus();
    return this.status();
  }

  async stop() {
    if (this.state === 'idle' || this.state === 'finishing') return this.status();
    if (this.state === 'paused') {
      this.pausedMs += Date.now() - this._pausedAt;
      this.pauses[this.pauses.length - 1].end = new Date().toISOString();
    }
    this.state = 'finishing';
    this._emitStatus();
    this._detachHooks();
    for (const t of this._timers) clearInterval(t);
    this._timers = [];
    this._tickApp();
    this.endedAt = new Date();
    this._writeFiles();
    await new Promise((res) => this.stream.end(res));
    this.stream = null;
    const manifest = this._writeManifest(true);
    this.emit('finished', manifest);
    this.state = 'idle';
    const done = this.status();
    this.dir = null;
    this.startedAt = null;
    this._emitStatus();
    return done;
  }

  _redactText(text) {
    return this.settings.redact ? redactText(text) : String(text ?? '');
  }

  // What the employee says they are working on this session, asked right after
  // Start. Kept in the manifest as `summary_text` so it survives Stop and names
  // the recording.
  setIntent(text) {
    if (this.state === 'idle') return this.status();
    this.intent = String(text ?? '').trim().slice(0, 4000);
    this._writeManifest();
    return this.status();
  }

  status() {
    const elapsed = this.startedAt ? Date.now() - this.startedAt.getTime() - this.pausedMs - (this._pausedAt ? Date.now() - this._pausedAt : 0) : 0;
    return {
      state: this.state,
      recordingId: this.recordingId ?? null,
      dir: this.dir,
      startedAt: this.startedAt?.toISOString() ?? null,
      elapsedMs: Math.max(0, elapsed),
      current: { ...this.current },
      counts: { ...this.counts },
      apps: this._appSummary(),
      log: this._log.slice(-8),
      settings: this.settings,
    };
  }

  // ---- hooks ---------------------------------------------------------------

  _attachHooks() {
    if (!this.hook) return;
    this._h = {
      mousedown: (e) => this._onMouse(e),
      wheel: (e) => this._onWheel(e),
      keydown: (e) => this._onKeyDown(e),
      keyup: (e) => this._onKeyUp(e),
    };
    for (const [k, fn] of Object.entries(this._h)) this.hook.on(k, fn);
    try {
      this.hook.start();
    } catch (err) {
      this._note(`input hook unavailable: ${err.message}`);
    }
  }

  _detachHooks() {
    if (!this.hook || !this._h) return;
    for (const [k, fn] of Object.entries(this._h)) this.hook.off(k, fn);
    try {
      this.hook.stop();
    } catch {
      /* already stopped */
    }
    this._h = null;
  }

  _onMouse(e) {
    this._write('click', { payload: { button: e.button, x: e.x, y: e.y, clicks: e.clicks } });
  }

  _onWheel(e) {
    const now = Date.now();
    if (now - this._lastScroll < 750) return; // coalesce wheel bursts
    this._lastScroll = now;
    this._write('scroll', { payload: { direction: e.direction, amount: e.amount } });
  }

  _onKeyDown(e) {
    const mod = modifierOf(this.keyNames, e);
    if (mod) {
      this._mods.add(mod);
      return;
    }
    const mods = [...this._mods];
    const name = keyName(this.keyNames, e);
    const hasCombo = mods.some((m) => m !== 'shift');
    if (hasCombo) {
      const combo = [...mods.map(prettyMod), name].join('+');
      const primary = mods.includes(IS_MAC ? 'meta' : 'ctrl') && mods.length === 1;
      if (primary && name === 'C') return this._clip('copy', combo);
      if (primary && name === 'V') return this._clip('paste', combo);
      if (primary && name === 'X') return this._clip('copy', combo, { cut: true });
      return this._write('shortcut', { text: combo, payload: { modifiers: mods, key: name } });
    }
    const printable = name.length === 1;
    let text = '';
    if (printable && this.settings.keyContent) text = mods.includes('shift') ? name.toUpperCase() : name.toLowerCase();
    this._write('key', { text, payload: printable ? {} : { key: name } });
  }

  _onKeyUp(e) {
    const mod = modifierOf(this.keyNames, e);
    if (mod) this._mods.delete(mod);
  }

  _clip(type, combo, extra = {}) {
    // the OS updates the clipboard slightly after the key event; read it a beat later
    setTimeout(() => {
      let raw = '';
      try {
        raw = this.readClipboard() ?? '';
      } catch {
        raw = '';
      }
      const text = this.settings.clipboard ? this._redactText(raw).slice(0, 200) : '';
      const hash = raw ? clipHash(raw, this.recordingId) : '';
      const payload = { combo, ...extra, clip_hash: hash, chars: raw.length };
      let crossApp = false;
      if (type === 'copy') {
        this._lastClip = hash ? { hash, app: this.current.app, title: this.current.title, at: Date.now() } : null;
      } else if (type === 'paste' && hash && this._lastClip?.hash === hash) {
        const src = this._lastClip;
        crossApp = src.app !== this.current.app;
        Object.assign(payload, {
          source_app: src.app,
          source_title: src.title,
          transfer_ms: Date.now() - src.at,
          cross_app: crossApp,
        });
      }
      if (this._write(type, { text, payload }) && crossApp) this.counts.transfer += 1;
    }, 60);
  }

  // ---- foreground window ---------------------------------------------------

  async _pollWindow(force = false) {
    if (this.state !== 'recording') return;
    this._tickApp();
    let win = null;
    try {
      win = this.activeWindow ? await this.activeWindow() : null;
    } catch (err) {
      this._note(`active window unavailable: ${err.message}`);
      return;
    }
    if (!win) return;
    this._lastWin = win;
    const app = win.owner?.name ?? '';
    const title = win.title ?? '';
    const url = win.url ?? '';
    if (!force && app === this.current.app && title === this.current.title && url === this.current.url) return;
    const priv = this._isPrivate(app, title);
    const own = this._isOwn(app);
    this.current = { app, title: priv ? '(private)' : this._redactText(title), url: priv ? '' : url, private: priv, own };
    if (own) return this._emitStatus();
    this._write('focus', { payload: { window_id: win.id ?? null } });
    this._requestFrame('focus');
    this._probeFiles(win, app, priv);
    this._emitStatus();
  }

  // Which documents the frontmost app shows right now; closes the ones no longer seen.
  async _probeFiles(win, app, priv) {
    if (!this.files || !this.fileProbe || this._probing) return;
    if (priv) return this.files.closeAll(Date.now());
    this._probing = true;
    try {
      const res = await this.fileProbe(win);
      const docs = Array.isArray(res) ? res : res?.docs;
      if (docs && this.state === 'recording' && this.files) {
        const before = new Set([...this.files.files.values()].filter((f) => f.open).map((f) => f.path));
        this.files.observe(docs, Date.now(), { app, running: Array.isArray(res) ? null : res.running });
        for (const d of docs) {
          const p = typeof d === 'string' ? d : d.path;
          if (!before.has(p)) this._pushLog({ timestamp: new Date().toISOString(), event_type: 'file', app: (typeof d === 'string' ? app : d.app) || app, window_title: p.split('/').pop(), text: '', payload: {} });
        }
      }
    } catch (err) {
      this._note(`file probe failed: ${err.message}`);
    } finally {
      this._probing = false;
    }
  }

  _writeFiles() {
    if (!this.files || !this.dir) return;
    const t1 = this.endedAt.getTime();
    this.files.closeAll(t1);
    const files = this.files.finish({ t0: this.startedAt.getTime(), t1, pauses: this.pauses ?? [] });
    fs.writeFileSync(path.join(this.dir, FILES_FILE), JSON.stringify({ version: 1, files }, null, 2));
    this.counts.file = files.length;
  }

  _isPrivate(app, title) {
    const a = app.toLowerCase();
    const t = title.toLowerCase();
    return this.settings.privateApps.some((p) => a.includes(p.toLowerCase())) || this.settings.privateTitles.some((p) => t.includes(p.toLowerCase()));
  }

  _isOwn(app) {
    const a = app.toLowerCase();
    return (this.settings.ownApps ?? []).some((p) => a === p.toLowerCase());
  }

  _tickApp() {
    const now = Date.now();
    if (this.lastTick && this.state === 'recording' && this.current.app && !this.current.own) {
      const key = this.current.private ? '(private)' : this.current.app;
      this.appSeconds[key] = (this.appSeconds[key] ?? 0) + (now - this.lastTick) / 1000;
    }
    this.lastTick = now;
  }

  _appSummary() {
    return Object.entries(this.appSeconds)
      .map(([app, seconds]) => ({ app, seconds: Math.round(seconds) }))
      .sort((a, b) => b.seconds - a.seconds);
  }

  // ---- screen --------------------------------------------------------------

  _periodicFrame() {
    if (Date.now() - this._lastFrameAt >= this.settings.frameEverySec * 1000) this._requestFrame('interval');
  }

  async _changeTick() {
    if (this._thumbBusy || this.state !== 'recording' || this.current.private || this.current.own || !this.settings.screenshots) return;
    this._thumbBusy = true;
    try {
      const thumb = await this.thumbProvider();
      if (!thumb) return;
      const prev = this._thumb;
      this._thumb = thumb;
      if (!prev) return;
      const diff = frameDiff(prev, thumb);
      if (diff >= this.settings.changeThreshold && Date.now() - this._lastFrameAt >= this.settings.changeMinGapMs) {
        await this._requestFrame('change', { diff: Math.round(diff * 1000) / 1000 });
      }
    } catch (err) {
      this._note(`change detection failed: ${err.message}`);
    } finally {
      this._thumbBusy = false;
    }
  }

  async _requestFrame(reason, extra = {}) {
    if (!this.settings.screenshots || !this.frameProvider || this.state !== 'recording' || this.current.private || this.current.own) return;
    this._lastFrameAt = Date.now();
    let buf = null;
    try {
      buf = await this.frameProvider();
    } catch (err) {
      this._note(`frame capture failed: ${err.message}`);
      return;
    }
    if (!buf || !this.dir) return;
    const name = `shots/${String(++this.frameNo).padStart(6, '0')}.jpg`;
    fs.writeFile(path.join(this.dir, name), buf, () => {});
    this._write('screen', { payload: { image: name, reason, ...extra } });
  }

  // ---- output --------------------------------------------------------------

  _write(type, { text = '', payload = {}, element = '' } = {}) {
    if (this.state !== 'recording' || !this.stream) return false;
    if (this.current.private && type !== 'focus') return false; // nothing leaves a private app
    if (this.current.own) return false; // clicks in the recorder's own windows are not work
    const raw = {
      timestamp: new Date().toISOString(),
      user: this.user,
      event_type: type,
      app: this.current.app,
      window_title: this.current.title,
      url: this.current.url,
      element,
      text,
      payload: { recording_id: this.recordingId, ...payload },
    };
    const ev = this.settings.redact ? redactEvent(raw) : raw;
    if (ev.text !== raw.text || ev.window_title !== raw.window_title) this.counts.redactions += 1;
    this.stream.write(JSON.stringify(ev) + '\n');
    this.counts[type] = (this.counts[type] ?? 0) + 1;
    this.counts.total += 1;
    if (type !== 'key' && type !== 'scroll') this.emit('event', ev);
    if (type === 'focus' || type === 'copy' || type === 'paste' || type === 'shortcut') this._pushLog(ev);
    return true;
  }

  _pushLog(ev) {
    let label;
    if (ev.event_type === 'focus') label = `${ev.app} — ${ev.window_title}`;
    else if (ev.event_type === 'file') label = `file ${ev.window_title}`;
    else if (ev.event_type === 'paste' && ev.payload.source_app) label = `paste ← ${ev.payload.source_app} ${ev.text}`.trim();
    else label = `${ev.event_type} ${ev.text}`.trim();
    this._log.push({ t: ev.timestamp, type: ev.event_type, label: label.slice(0, 80) });
    if (this._log.length > 50) this._log.shift();
  }

  _note(msg) {
    this._log.push({ t: new Date().toISOString(), type: 'note', label: msg });
    this.emit('note', msg);
  }

  _writeManifest(final = false) {
    const manifest = {
      recording_id: this.recordingId,
      user: this.user,
      platform: process.platform,
      started_at: this.startedAt?.toISOString(),
      ended_at: final ? this.endedAt.toISOString() : null,
      active_seconds: final ? Math.round((this.endedAt - this.startedAt - this.pausedMs) / 1000) : null,
      counts: this.counts,
      apps: this._appSummary(),
      pauses: this.pauses ?? [],
      settings: { redact: !!this.settings.redact, keyContent: this.settings.keyContent, clipboard: this.settings.clipboard, screenshots: this.settings.screenshots, video: this.settings.video },
      files: { events: 'events.jsonl', shots: 'shots/', video: this.settings.video ? 'screen.webm' : null, documents: this.settings.files ? FILES_FILE : null },
      processing: final ? 'pending' : null,
      ...(this.intent ? { summary_text: this.intent } : {}),
    };
    if (this.dir) fs.writeFileSync(path.join(this.dir, 'manifest.json'), JSON.stringify(manifest, null, 2));
    return manifest;
  }

  _emitStatus() {
    this.emit('status', this.status());
  }
}

// ---- clipboard / frame helpers ----------------------------------------------

export function clipHash(text, salt = '') {
  return createHash('sha256').update(`${salt}:${text}`).digest('hex').slice(0, 12);
}

/** Fraction of pixels whose gray level moved by more than 24/255 between two same-size thumbnails. */
export function frameDiff(a, b) {
  if (!a || !b || a.width !== b.width || a.height !== b.height) return 1;
  const n = a.gray.length;
  let changed = 0;
  for (let i = 0; i < n; i++) if (Math.abs(a.gray[i] - b.gray[i]) > 24) changed++;
  return n ? changed / n : 0;
}

// ---- key helpers -----------------------------------------------------------

export function keyNamesFrom(UiohookKey) {
  const m = new Map();
  for (const [name, code] of Object.entries(UiohookKey)) if (!m.has(code)) m.set(code, name);
  return m;
}

function modifierOf(table, e) {
  const n = table.get(e.keycode) ?? '';
  if (/^Ctrl/.test(n)) return 'ctrl';
  if (/^Alt/.test(n)) return 'alt';
  if (/^Shift/.test(n)) return 'shift';
  if (/^Meta/.test(n)) return 'meta';
  return null;
}

function keyName(table, e) {
  const n = table.get(e.keycode) ?? `#${e.keycode}`;
  if (KEY_NAMES[n]) return KEY_NAMES[n];
  if (/^F\d{1,2}$/.test(n)) return n;
  if (/^Numpad(\d)$/.test(n)) return n.slice(-1);
  if (n.length === 1) return n.toUpperCase();
  const punct = { Comma: ',', Period: '.', Slash: '/', Semicolon: ';', Quote: "'", Minus: '-', Equal: '=', BracketLeft: '[', BracketRight: ']', Backslash: '\\', Backquote: '`' };
  return punct[n] ?? n;
}

export function prettyMod(m) {
  return { ctrl: 'Ctrl', alt: IS_MAC ? 'Option' : 'Alt', shift: 'Shift', meta: IS_MAC ? 'Cmd' : 'Win' }[m] ?? m;
}
