// The desktop harness: performs one step at a time on the frontmost window of the
// employee's computer. It is Electron-free and platform-free: everything that touches the
// operating system comes through a `backend`
//
//   activeWindow()            { app, title, bounds:{x,y,width,height} } | null
//   elements(window)          [{ id, role, name, kind, x, y, width, height }]  (accessibility tree)
//   moveTo(x, y) / click()    the computer's own pointer
//   type(text) / press(key)   the computer's own keyboard
//   openApp(name)             bring an application to the front
//   screenshot()              base64 PNG of the frontmost window, or null
//
// (`desktop-macos.js` provides one; there is none for Windows or Linux yet, so `supported`
// is false there and the workspace never offers this device a desktop run.)
//
// Guards, all in code: a private or sign-in window (settings' private apps/titles plus the
// recorder's sensitive-title list) refuses every step; the observation lists only named
// controls of the *front* window; a targeted step must cite the current observation and
// the window must still be the one that was observed.
import { randomBytes } from 'node:crypto';

import { DEFAULT_SETTINGS } from '../recorder.js';
import { isPrivateWindow } from './policy.js';

export const MAX_CANDIDATES = 40;
export const SETTLE_MS = 700;

const ROLE_KIND = {
  'text field': 'field',
  'text area': 'field',
  'combo box': 'field',
  'search field': 'field',
  'secure text field': null, // never a target
  link: 'link',
  button: 'interactive',
  'pop up button': 'interactive',
  'menu button': 'interactive',
  'radio button': 'interactive',
  'check box': 'interactive',
  checkbox: 'interactive',
  'menu item': 'interactive',
  tab: 'interactive',
  'tab group': null,
  row: 'row',
  table: 'record',
  outline: 'record',
};

export function candidatesFromElements(elements, { limit = MAX_CANDIDATES } = {}) {
  const out = [];
  const seen = new Set();
  for (const el of elements ?? []) {
    const role = String(el.role ?? '').toLowerCase();
    const kind = ROLE_KIND[role];
    if (!kind) continue;
    const name = String(el.name ?? '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 200);
    if (!name) continue;
    if (!(Number.isFinite(el.width) && Number.isFinite(el.height) && el.width > 0 && el.height > 0)) continue;
    const key = `${role}\0${name.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ id: String(el.id ?? out.length), role: role.replace(/ /g, '_'), name, kind, attrs: el.enabled === false ? { disabled: true } : {} });
    if (out.length >= limit) break;
  }
  return out;
}

export class DesktopHarness {
  constructor({ backend = null, settings = () => DEFAULT_SETTINGS, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
    this.kind = 'desktop';
    this.backend = backend;
    this.supported = !!backend;
    this.settings = settings;
    this.sleep = sleep;
    this.current = null; // { id, window, boxes: Map<candidate id, {x,y,width,height}> }
  }

  capabilities() {
    return this.supported ? ['observe', 'navigate', 'open_app', 'click', 'type', 'press', 'screenshot', 'wait'] : [];
  }

  async perform(step) {
    try {
      const b = this._backend();
      switch (step.action) {
        case 'observe':
          return this._ok(step, 'Looked at the front window', { observation: await this._observe() });
        case 'open_app':
        case 'navigate': {
          await b.openApp(step.value);
          await this.sleep(SETTLE_MS);
          const observation = await this._observe();
          return this._ok(step, `Brought ${step.value} to the front`, { observation, result: { title_after: observation.window_title } });
        }
        case 'click': {
          const { window, box } = await this._target(step);
          await b.moveTo(Math.round(box.x + box.width / 2), Math.round(box.y + box.height / 2));
          await b.click();
          await this.sleep(SETTLE_MS);
          const observation = await this._observe();
          return this._ok(step, step.description || 'Clicked the control', { observation, result: { title_after: observation.window_title, window_changed: observation.window_title !== window.title } });
        }
        case 'type': {
          const { box } = await this._target(step);
          await b.moveTo(Math.round(box.x + box.width / 2), Math.round(box.y + box.height / 2));
          await b.click();
          if (step.replace !== false) {
            await b.press('SelectAll');
          }
          await b.type(step.value);
          return this._ok(step, step.description || 'Typed the value into the field', { observation: await this._observe(), result: { previous_value: null } });
        }
        case 'press': {
          await this._guard();
          await b.press(step.value);
          await this.sleep(SETTLE_MS);
          const observation = await this._observe();
          return this._ok(step, `Pressed ${step.value}`, { observation, result: { title_after: observation.window_title } });
        }
        case 'screenshot': {
          await this._guard();
          const data = await b.screenshot();
          return this._ok(step, 'Took a screenshot', { observation: await this._observe(), evidence: data ? { screenshot_base64: data, content_type: 'image/png' } : null });
        }
        case 'wait':
          await this.sleep(Number(step.value));
          return this._ok(step, `Waited ${Number(step.value)} ms`, { observation: await this._observe() });
        default:
          return this._fail(step, 'invalid_value', `The desktop harness cannot ${step.action}.`);
      }
    } catch (e) {
      return this._fail(step, e.code ?? 'harness_error', e.message);
    }
  }

  async close() {
    this.current = null;
  }

  // ---- internals ----

  _backend() {
    if (!this.backend) {
      const err = new Error('The desktop harness is not available on this computer.');
      err.code = 'harness_unsupported';
      throw err;
    }
    return this.backend;
  }

  _private(win) {
    return !win || isPrivateWindow(win.app, win.title, this.settings());
  }

  async _observe() {
    const b = this._backend();
    const win = await b.activeWindow();
    const id = `obs-${randomBytes(6).toString('hex')}`;
    if (this._private(win)) {
      this.current = { id, window: null, boxes: new Map() };
      return { observation_id: id, app: '', window_title: '', sensitive: true, candidates: [] };
    }
    const elements = await b.elements(win);
    const candidates = candidatesFromElements(elements);
    const boxes = new Map();
    for (const el of elements) if (candidates.some((c) => c.id === String(el.id))) boxes.set(String(el.id), { x: el.x, y: el.y, width: el.width, height: el.height });
    this.current = { id, window: win, boxes };
    return { observation_id: id, app: win.app, window_title: win.title, sensitive: false, candidates };
  }

  async _guard() {
    const win = await this._backend().activeWindow();
    if (this._private(win)) {
      const err = new Error('The front window looks private (sign-in, payment or a listed app); the step was not performed.');
      err.code = 'sensitive_window';
      throw err;
    }
    return win;
  }

  async _target(step) {
    const cur = this.current;
    const box = cur?.id === step.observation_id ? cur.boxes.get(String(step.target_id)) : undefined;
    if (!box || !cur.window) {
      const err = new Error('The target is not in the current observation.');
      err.code = 'stale_observation';
      throw err;
    }
    const win = await this._guard();
    if (win.app !== cur.window.app || win.title !== cur.window.title) {
      const err = new Error('The front window changed since it was observed; observe again.');
      err.code = 'stale_observation';
      throw err;
    }
    return { window: win, box };
  }

  _ok(step, description, { observation = null, result = null, evidence = null } = {}) {
    return { ok: true, description: description || step.description || step.action, observation, result, evidence, error: null };
  }

  _fail(step, code, message) {
    return { ok: false, description: `${step.action} refused: ${message}`, observation: null, result: null, evidence: null, error: { code, message } };
  }
}
