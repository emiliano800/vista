// The browser harness: performs one step at a time in a page this recorder owns.
//
// The page lives in a BrowserWindow of its own (`browser-electron.js`), in a storage
// partition the employee's normal browsing never touches, and is driven over the Chrome
// DevTools Protocol. This module is Electron-free: it speaks to a `page` adapter
//
//   send(method, params)   CDP call
//   loadURL(url)           navigate and settle
//   url() / title()        what is loaded now
//   screenPoint(x, y)      page CSS pixel → screen pixel (for a real pointer), or null
//   close()
//
// and an optional `pointer` ({ moveTo(x, y), click() }) that moves the computer's own
// cursor; without one, clicks are dispatched to the page over CDP.
//
// What the server sees is what the accessibility tree says: a bounded list of named,
// interactive elements (`candidates`), the page's URL and title, and a text excerpt. A
// step may target only an element from the observation it cites; the harness keeps the
// mapping from candidate id to DOM node for the current observation only.
import { randomBytes } from 'node:crypto';

import { DEFAULT_SETTINGS } from '../recorder.js';
import { KEYS, isPrivateWindow } from './policy.js';

export const MAX_CANDIDATES = 40;
export const MAX_TEXT = 4000;
export const MAX_ROWS = 200;
export const MAX_COLUMNS = 40;
export const SETTLE_MS = 600;
export const HOVER_MS = 700; // real pointer rests on the control before pressing, so a watcher can follow

// Drawn into the sandbox page on every document: a ring that follows the pointer and
// flashes on press, so the person watching sees where the agent is and when it clicks.
// `aria-hidden` and `pointer-events:none` keep it out of the accessibility tree the
// harness enumerates and out of the way of the clicks themselves.
export const HALO_SCRIPT = `(() => {
  if (window.__vistaHalo) return;
  const halo = document.createElement('div');
  halo.id = 'vista-pointer-halo';
  halo.setAttribute('aria-hidden', 'true');
  halo.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;width:56px;height:56px;margin:-28px 0 0 -28px;' +
    'border-radius:50%;border:4px solid #c2410c;background:rgba(194,65,12,.18);box-shadow:0 0 0 6px rgba(194,65,12,.25);' +
    'left:-100px;top:-100px;transition:transform .12s ease-out,background .12s;';
  const mount = () => (document.body || document.documentElement).appendChild(halo);
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount, { once: true });
  const at = (e) => { halo.style.left = e.clientX + 'px'; halo.style.top = e.clientY + 'px'; };
  window.addEventListener('mousemove', at, true);
  window.addEventListener('mousedown', (e) => {
    at(e);
    halo.style.transform = 'scale(.6)';
    halo.style.background = 'rgba(194,65,12,.6)';
    setTimeout(() => { halo.style.transform = ''; halo.style.background = 'rgba(194,65,12,.18)'; }, 220);
  }, true);
  window.__vistaHalo = true;
})();`;

// AX role → candidate kind. Anything not listed is not a target.
const ROLE_KIND = {
  textbox: 'field',
  searchbox: 'field',
  combobox: 'field',
  spinbutton: 'field',
  slider: 'field',
  link: 'link',
  button: 'interactive',
  checkbox: 'interactive',
  radio: 'interactive',
  switch: 'interactive',
  menuitem: 'interactive',
  menuitemcheckbox: 'interactive',
  menuitemradio: 'interactive',
  tab: 'interactive',
  option: 'interactive',
  treeitem: 'interactive',
  row: 'row',
  table: 'record',
  grid: 'record',
};

function axValue(prop) {
  const v = prop?.value;
  if (v == null) return null;
  return typeof v === 'object' ? v.value : v;
}

const CELL_ROLES = new Set(['cell', 'gridcell', 'rowheader']);
const ROW_NAME_CELLS = 3;

// Chrome gives a table row no accessible name; code derives one from its first cells so a
// row is a candidate the way a link or button is (its visible text, never anything hidden).
// Header rows (column headers only) are not offered.
function rowName(n, byId) {
  const cells = (n.childIds ?? []).map((id) => byId.get(id)).filter(Boolean);
  const data = cells.filter((c) => CELL_ROLES.has(axValue(c.role)));
  if (!data.length) return '';
  return data
    .map((c) => String(axValue(c.name) ?? '').trim())
    .filter(Boolean)
    .slice(0, ROW_NAME_CELLS)
    .join(' · ');
}

// Pure: AX nodes → candidates. Exported for tests.
export function candidatesFromAX(nodes, { limit = MAX_CANDIDATES } = {}) {
  const out = [];
  const seen = new Set();
  const byId = new Map((nodes ?? []).map((n) => [n.nodeId, n]));
  for (const n of nodes ?? []) {
    if (n.ignored || n.backendDOMNodeId == null) continue;
    const role = axValue(n.role);
    const kind = ROLE_KIND[role];
    if (!kind) continue;
    if (n.properties?.some((p) => p.name === 'hidden' && axValue(p) === true)) continue;
    const name = String(axValue(n.name) || (role === 'row' ? rowName(n, byId) : ''))
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 200);
    if (!name) continue;
    const key = `${role}\0${name.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const attrs = {};
    const disabled = n.properties?.find((p) => p.name === 'disabled');
    if (disabled && axValue(disabled) === true) attrs.disabled = true;
    const val = axValue(n.value);
    if (kind === 'field' && val != null && String(val).length) attrs.has_value = true; // never the value itself
    out.push({ id: String(n.backendDOMNodeId), role, name, kind, attrs });
    if (out.length >= limit) break;
  }
  return out;
}

export function observationId() {
  return `obs-${randomBytes(6).toString('hex')}`;
}

export class BrowserHarness {
  // `open` creates the page adapter on first use (so no window exists until a session
  // needs one); `pointer` is optional; `settings` supplies the private app/title lists.
  constructor({ open, pointer = null, settings = () => DEFAULT_SETTINGS, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
    this.kind = 'browser';
    this.supported = typeof open === 'function';
    this.open = open;
    this.pointer = pointer;
    this.settings = settings;
    this.sleep = sleep;
    this.page = null;
    this.current = null; // { id, nodes: Map<candidate id, backendNodeId> }
  }

  capabilities() {
    return this.supported ? ['observe', 'navigate', 'click', 'type', 'press', 'extract', 'screenshot', 'wait'] : [];
  }

  async perform(step) {
    try {
      const page = await this._page();
      switch (step.action) {
        case 'observe':
          return this._ok(step, 'Looked at the page', { observation: await this._observe(page) });
        case 'navigate': {
          await page.loadURL(step.value);
          await this.sleep(SETTLE_MS);
          const observation = await this._observe(page);
          return this._ok(step, `Opened ${observation.sensitive ? 'a page' : observation.title || step.value}`, { observation, result: { url_after: observation.url, title_after: observation.title } });
        }
        case 'click': {
          const node = this._node(step);
          const box = await this._center(page, node);
          await this._click(page, box);
          await this.sleep(SETTLE_MS);
          const observation = await this._observe(page);
          return this._ok(step, step.description || 'Clicked the control', { observation, result: { url_after: observation.url, title_after: observation.title } });
        }
        case 'type': {
          const node = this._node(step);
          await this._click(page, await this._center(page, node));
          const previous = await this._fill(page, node, step.value, step.replace !== false);
          const observation = await this._observe(page);
          return this._ok(step, step.description || 'Typed the value into the field', {
            observation,
            result: { previous_value: previous, undo: { action: 'type', target_id: step.target_id, value: previous ?? '' } },
          });
        }
        case 'press': {
          await this._press(page, step.value);
          await this.sleep(SETTLE_MS);
          const observation = await this._observe(page);
          return this._ok(step, `Pressed ${step.value}`, { observation, result: { url_after: observation.url, title_after: observation.title } });
        }
        case 'extract': {
          await this._guard(page);
          const node = step.target_id != null ? this._node(step) : null;
          const result = step.value === 'table' ? await this._table(page, node) : { text: await this._text(page, node) };
          return this._ok(step, step.value === 'table' ? `Extracted ${result.count} rows` : 'Read the text', { observation: await this._observe(page), result });
        }
        case 'screenshot': {
          await this._guard(page);
          const shot = await page.send('Page.captureScreenshot', { format: 'png' });
          return this._ok(step, 'Took a screenshot', { observation: await this._observe(page), evidence: { screenshot_base64: shot.data, content_type: 'image/png' } });
        }
        case 'wait':
          await this.sleep(Number(step.value));
          return this._ok(step, `Waited ${Number(step.value)} ms`, { observation: await this._observe(page) });
        default:
          return this._fail(step, 'invalid_value', `The browser harness cannot ${step.action}.`);
      }
    } catch (e) {
      return this._fail(step, e.code ?? 'harness_error', e.message);
    }
  }

  async close() {
    const p = this.page;
    this.page = null;
    this.current = null;
    if (p) await p.close();
  }

  // ---- observing ----

  async _observe(page) {
    const url = await page.url();
    const title = await page.title();
    const id = observationId();
    if (isPrivateWindow('browser', title, this.settings()) || isPrivateWindow('browser', url, this.settings())) {
      this.current = { id, nodes: new Map() };
      return { observation_id: id, url: '', title: '', sensitive: true, candidates: [], text_excerpt: '' };
    }
    const { nodes } = await page.send('Accessibility.getFullAXTree', {});
    const candidates = candidatesFromAX(nodes);
    this.current = { id, nodes: new Map(candidates.map((c) => [c.id, Number(c.id)])) };
    return { observation_id: id, url, title, sensitive: false, candidates, text_excerpt: await this._text(page, null) };
  }

  async _guard(page) {
    const title = await page.title();
    const url = await page.url();
    if (isPrivateWindow('browser', title, this.settings()) || isPrivateWindow('browser', url, this.settings())) {
      const err = new Error('This page looks private (sign-in, payment or a listed app); the step was not performed.');
      err.code = 'sensitive_window';
      throw err;
    }
  }

  _node(step) {
    const backendNodeId = this.current?.id === step.observation_id ? this.current.nodes.get(String(step.target_id)) : undefined;
    if (backendNodeId == null) {
      const err = new Error('The target is not in the current observation.');
      err.code = 'stale_observation';
      throw err;
    }
    return backendNodeId;
  }

  // ---- acting ----

  async _center(page, backendNodeId) {
    await this._guard(page);
    await page.send('DOM.scrollIntoViewIfNeeded', { backendNodeId }).catch(() => {});
    const { model } = await page.send('DOM.getBoxModel', { backendNodeId });
    const q = model.content; // [x1,y1, x2,y2, x3,y3, x4,y4]
    return { x: (q[0] + q[4]) / 2, y: (q[1] + q[5]) / 2 };
  }

  async _click(page, { x, y }) {
    await page.send('Runtime.evaluate', { expression: HALO_SCRIPT }).catch(() => {});
    const screen = this.pointer && page.screenPoint ? await page.screenPoint(x, y) : null;
    if (screen) {
      await this.pointer.moveTo(screen.x, screen.y);
      await this.sleep(HOVER_MS);
      await this.pointer.click();
      return;
    }
    await page.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await page.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await page.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  }

  async _fill(page, backendNodeId, text, replace) {
    await this._guard(page);
    const { object } = await page.send('DOM.resolveNode', { backendNodeId });
    const read = await page.send('Runtime.callFunctionOn', {
      objectId: object.objectId,
      functionDeclaration: 'function(){ this.focus(); return this.isContentEditable ? this.textContent : (this.value ?? null); }',
      returnByValue: true,
    });
    const previous = read.result?.value ?? null;
    if (replace) {
      await page.send('Runtime.callFunctionOn', {
        objectId: object.objectId,
        functionDeclaration: 'function(){ if (this.select) this.select(); else { const r = document.createRange(); r.selectNodeContents(this); const s = getSelection(); s.removeAllRanges(); s.addRange(r); } }',
      });
    }
    await page.send('Input.insertText', { text });
    return previous;
  }

  async _press(page, name) {
    await this._guard(page);
    const k = KEYS[name];
    const base = { key: k.key, code: k.code, windowsVirtualKeyCode: k.keyCode, nativeVirtualKeyCode: k.keyCode };
    await page.send('Input.dispatchKeyEvent', { type: 'keyDown', ...base });
    await page.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base });
  }

  async _text(page, backendNodeId) {
    if (backendNodeId != null) {
      const { object } = await page.send('DOM.resolveNode', { backendNodeId });
      const r = await page.send('Runtime.callFunctionOn', { objectId: object.objectId, functionDeclaration: 'function(){ return (this.innerText ?? this.textContent ?? "") }', returnByValue: true });
      return String(r.result?.value ?? '').slice(0, MAX_TEXT);
    }
    const expression = `(document.body && document.body.innerText || '').slice(0, ${MAX_TEXT})`;
    const r = await page.send('Runtime.evaluate', { expression, returnByValue: true });
    return String(r.result?.value ?? '').slice(0, MAX_TEXT);
  }

  async _table(page, backendNodeId) {
    const fn = `function(){
      const root = this instanceof Element ? this : document;
      const table = root.tagName === 'TABLE' ? root : root.querySelector('table');
      if (!table) return { columns: [], rows: [], count: 0 };
      const cell = (c) => (c.innerText || c.textContent || '').trim().slice(0, 200);
      const trs = Array.from(table.querySelectorAll('tr'));
      let columns = Array.from(trs[0]?.querySelectorAll('th') ?? []).map(cell).slice(0, ${MAX_COLUMNS});
      let body = columns.length ? trs.slice(1) : trs;
      if (!columns.length) columns = Array.from(trs[0]?.children ?? []).map((_, i) => 'column_' + (i + 1)).slice(0, ${MAX_COLUMNS});
      const rows = body.slice(0, ${MAX_ROWS}).map((tr) => Array.from(tr.children).slice(0, ${MAX_COLUMNS}).map(cell));
      return { columns, rows, count: rows.length };
    }`;
    if (backendNodeId != null) {
      const { object } = await page.send('DOM.resolveNode', { backendNodeId });
      const r = await page.send('Runtime.callFunctionOn', { objectId: object.objectId, functionDeclaration: fn, returnByValue: true });
      return r.result.value;
    }
    const r = await page.send('Runtime.evaluate', { expression: `(${fn}).call(null)`, returnByValue: true });
    return r.result.value;
  }

  // ---- plumbing ----

  async _page() {
    if (!this.supported) {
      const err = new Error('The browser harness is not available on this computer.');
      err.code = 'harness_unsupported';
      throw err;
    }
    if (!this.page) {
      this.page = await this.open();
      await this.page.send('Page.addScriptToEvaluateOnNewDocument', { source: HALO_SCRIPT }).catch(() => {});
    }
    return this.page;
  }

  _ok(step, description, { observation = null, result = null, evidence = null } = {}) {
    return { ok: true, description: description || step.description || step.action, observation, result, evidence, error: null };
  }

  _fail(step, code, message) {
    return { ok: false, description: `${step.action} refused: ${message}`, observation: null, result: null, evidence: null, error: { code, message } };
  }
}
