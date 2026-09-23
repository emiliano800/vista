import assert from 'node:assert/strict';
import { test } from 'node:test';

import { BrowserHarness, HALO_SCRIPT, candidatesFromAX } from '../src/computer-use/browser.js';
import { ChromeConnection, GEOMETRY_SCRIPT, openChromePage, screenPointFrom } from '../src/computer-use/browser-chrome.js';
import { DesktopHarness, candidatesFromElements } from '../src/computer-use/desktop.js';
import { parseElements } from '../src/computer-use/desktop-macos.js';
import { capabilitiesOf, defaultHarnesses } from '../src/computer-use/harnesses.js';

const ax = (id, role, name, extra = {}) => ({ backendDOMNodeId: id, role: { value: role }, name: { value: name }, ...extra });

// A fake CDP page: a fixed AX tree, a box per node, and a log of every command.
function fakePage({ title = 'Sandbox form', url = 'https://sandbox.example.test/form', nodes, screen = null } = {}) {
  const calls = [];
  const values = { 12: '' };
  return {
    calls,
    values,
    title: async () => title,
    url: async () => url,
    loadURL: async (u) => {
      calls.push(['loadURL', u]);
      url = u;
      title = 'Loaded ' + u;
    },
    screenPoint: async (x, y) => (screen ? { x: x + 100, y: y + 50 } : null),
    close: async () => calls.push(['close']),
    async send(method, params) {
      calls.push([method, params]);
      switch (method) {
        case 'Accessibility.getFullAXTree':
          return { nodes };
        case 'DOM.getBoxModel':
          return { model: { content: [10, 20, 110, 20, 110, 60, 10, 60] } };
        case 'DOM.resolveNode':
          return { object: { objectId: `obj:${params.backendNodeId}` } };
        case 'Runtime.callFunctionOn': {
          const id = Number(params.objectId.split(':')[1]);
          if (params.functionDeclaration.includes('this.focus()')) return { result: { value: values[id] ?? null } };
          if (params.functionDeclaration.includes('querySelectorAll')) return { result: { value: { columns: ['Invoice', 'Total'], rows: [['INV-1', '10']], count: 1 } } };
          if (params.functionDeclaration.includes('innerText')) return { result: { value: `text of ${id}` } };
          return { result: {} };
        }
        case 'Input.insertText':
          values[12] = (values[12] ?? '') + params.text;
          return {};
        case 'Runtime.evaluate':
          return { result: { value: params.expression.includes('querySelectorAll') ? { columns: [], rows: [], count: 0 } : 'Invoice INV-1 total 10' } };
        case 'Page.captureScreenshot':
          return { data: 'iVBORw0KGgo=' };
        default:
          return {};
      }
    },
  };
}

const NODES = [
  ax(7, 'RootWebArea', 'Sandbox form'),
  ax(12, 'textbox', 'Invoice number'),
  ax(13, 'textbox', 'Secret', { properties: [{ name: 'hidden', value: { value: true } }] }),
  ax(20, 'button', 'Save'),
  ax(21, 'button', 'Save'), // duplicate label: dropped
  ax(22, 'link', 'Help'),
  ax(30, 'StaticText', 'Some text'),
  { backendDOMNodeId: 31, role: { value: 'button' }, name: { value: '' } },
  ax(40, 'table', 'Lines'),
];

test('browser: AX tree → bounded, named, deduplicated candidates; values never leak', () => {
  const c = candidatesFromAX(NODES);
  assert.deepEqual(
    c.map((x) => [x.id, x.role, x.name, x.kind]),
    [
      ['12', 'textbox', 'Invoice number', 'field'],
      ['20', 'button', 'Save', 'interactive'],
      ['22', 'link', 'Help', 'link'],
      ['40', 'table', 'Lines', 'record'],
    ],
  );
  const withValue = candidatesFromAX([ax(1, 'textbox', 'Card', { value: { value: '4111 1111' } })]);
  assert.deepEqual(withValue[0].attrs, { has_value: true });
  assert.equal(JSON.stringify(withValue).includes('4111'), false);
  assert.equal(candidatesFromAX(Array.from({ length: 100 }, (_, i) => ax(i, 'button', `B${i}`))).length, 40);
});

test('browser: unnamed table rows are named from their first cells; header rows are not offered', () => {
  const cell = (nodeId, role, name) => ({ nodeId, backendDOMNodeId: nodeId, role: { value: role }, name: { value: name } });
  const nodes = [
    { nodeId: 'r0', backendDOMNodeId: 50, role: { value: 'row' }, name: { value: '' }, childIds: ['h1', 'h2'] },
    cell('h1', 'columnheader', 'Client ID'),
    cell('h2', 'columnheader', 'Client name'),
    { nodeId: 'r1', backendDOMNodeId: 51, role: { value: 'row' }, name: { value: '' }, childIds: ['c1', 'c2', 'c3', 'c4'] },
    cell('c1', 'cell', 'MER-C0008'),
    cell('c2', 'cell', 'Sterling Automotive Inc.'),
    cell('c3', 'cell', ''),
    cell('c4', 'cell', 'Auto Repair'),
    cell('c5', 'cell', 'Worcester'),
    { nodeId: 'r2', backendDOMNodeId: 52, role: { value: 'row' }, name: { value: 'Named row' }, childIds: ['c5'] },
  ];
  assert.deepEqual(
    candidatesFromAX(nodes).map((x) => [x.id, x.kind, x.name]),
    [
      ['51', 'row', 'MER-C0008 · Sterling Automotive Inc. · Auto Repair'],
      ['52', 'row', 'Named row'],
    ],
  );
});

test('chrome: page point → screen point uses the window geometry the page reports', () => {
  const g = { sx: 100, sy: 80, w: 1200, h: 700, dpr: 2, hidden: false };
  assert.deepEqual(screenPointFrom(g, 10, 20), { x: 220, y: 200 });
  assert.equal(screenPointFrom(g, 1300, 20), null);
  assert.equal(screenPointFrom({ ...g, hidden: true }, 10, 20), null);
  assert.equal(screenPointFrom(null, 10, 20), null);
  assert.match(GEOMETRY_SCRIPT, /screenX/);
});

test('chrome: the page adapter opens one tab, attaches flat, and closes it', async () => {
  const sent = [];
  const conn = {
    send: async (method, params, sessionId) => {
      sent.push([method, params, sessionId]);
      if (method === 'Target.createTarget') return { targetId: 't1' };
      if (method === 'Target.attachToTarget') return { sessionId: 's1' };
      if (method === 'Runtime.evaluate') return { result: { value: params.expression === 'document.title' ? 'CRM' : 'http://crm.test/' } };
      return {};
    },
    on: () => () => {},
    close: () => sent.push(['close']),
  };
  const page = await openChromePage({ connect: async () => conn })();
  assert.deepEqual(sent[0], ['Target.createTarget', { url: 'about:blank' }, undefined]);
  assert.deepEqual(sent[1], ['Target.attachToTarget', { targetId: 't1', flatten: true }, undefined]);
  assert.ok(sent.slice(2).every(([, , sid]) => sid === 's1'));
  assert.equal(await page.title(), 'CRM');
  assert.equal(await page.url(), 'http://crm.test/');
  await page.close();
  assert.deepEqual(sent.at(-2)[0], 'Target.closeTarget');
  assert.deepEqual(sent.at(-1), ['close']);
});

test('chrome: the connection multiplexes replies by id and surfaces CDP errors', async () => {
  const listeners = {};
  const ws = { sent: [], send: (m) => ws.sent.push(JSON.parse(m)), addEventListener: (ev, fn) => (listeners[ev] = fn), close: () => {} };
  const conn = new ChromeConnection(ws);
  const a = conn.send('A', {}, 's1');
  const b = conn.send('B');
  assert.equal(ws.sent[0].sessionId, 's1');
  assert.equal('sessionId' in ws.sent[1], false);
  listeners.message({ data: JSON.stringify({ id: 2, result: { ok: 2 } }) });
  listeners.message({ data: JSON.stringify({ id: 1, error: { message: 'No node' } }) });
  assert.deepEqual(await b, { ok: 2 });
  await assert.rejects(a, /No node/);
});

test('browser: observe, then a targeted click and type only against the cited observation', async () => {
  const page = fakePage({ nodes: NODES });
  const h = new BrowserHarness({ open: async () => page, sleep: async () => {} });
  assert.equal(h.supported, true);
  const obs = await h.perform({ action: 'observe' });
  assert.equal(obs.ok, true);
  assert.equal(obs.observation.candidates.length, 4);
  assert.equal(obs.observation.text_excerpt, 'Invoice INV-1 total 10');
  const oid = obs.observation.observation_id;

  const stale = await h.perform({ action: 'click', target_id: '20', observation_id: 'obs-other' });
  assert.equal(stale.ok, false);
  assert.equal(stale.error.code, 'stale_observation');

  const typed = await h.perform({ action: 'type', target_id: '12', observation_id: oid, value: 'INV-1042', description: 'Type the value of “invoice” into textbox: Invoice number' });
  assert.equal(typed.ok, true);
  assert.equal(typed.result.previous_value, '');
  assert.deepEqual(typed.result.undo, { action: 'type', target_id: '12', value: '' });
  assert.ok(page.calls.some(([m, p]) => m === 'Input.insertText' && p.text === 'INV-1042'));
  assert.equal(typed.observation.candidates[0].attrs.has_value, undefined); // AX tree is the fake's fixed one

  const click = await h.perform({ action: 'click', target_id: '20', observation_id: typed.observation.observation_id, description: 'Click button: Save' });
  assert.equal(click.ok, true);
  assert.equal(click.description, 'Click button: Save');
  const mouse = page.calls.filter(([m]) => m === 'Input.dispatchMouseEvent').map(([, p]) => p.type);
  assert.deepEqual(mouse, ['mouseMoved', 'mousePressed', 'mouseReleased', 'mouseMoved', 'mousePressed', 'mouseReleased']); // type clicks the field first
  assert.deepEqual(page.calls.filter(([m]) => m === 'DOM.getBoxModel').map(([, p]) => p.backendNodeId), [12, 20]);
  assert.equal(click.result.url_after, 'https://sandbox.example.test/form');

  const unknownTarget = await h.perform({ action: 'click', target_id: '999', observation_id: click.observation.observation_id });
  assert.equal(unknownTarget.error.code, 'stale_observation');
});

test('browser: a real pointer is used when the page can place the element on screen', async () => {
  const page = fakePage({ nodes: NODES, screen: true });
  const moves = [];
  const pointer = { moveTo: async (x, y) => moves.push(['move', x, y]), click: async () => moves.push(['click']) };
  const h = new BrowserHarness({ open: async () => page, pointer, sleep: async () => {} });
  const obs = await h.perform({ action: 'observe' });
  const clicked = await h.perform({ action: 'click', target_id: '20', observation_id: obs.observation.observation_id });
  assert.deepEqual(moves, [['move', 160, 90], ['click']]); // center (60,40) + screen offset (100,50)
  await h.perform({ action: 'type', target_id: '12', observation_id: clicked.observation.observation_id, value: 'x' });
  assert.equal(moves.length, 4); // typing travels to the field and clicks it before inserting text
  assert.equal(page.calls.some(([m]) => m === 'Input.dispatchMouseEvent'), false);
  // the pointer halo is installed for every document and refreshed before each click
  assert.ok(page.calls.some(([m, p]) => m === 'Page.addScriptToEvaluateOnNewDocument' && p.source === HALO_SCRIPT));
  assert.ok(page.calls.some(([m, p]) => m === 'Runtime.evaluate' && p.expression === HALO_SCRIPT));
  assert.ok(HALO_SCRIPT.includes('aria-hidden') && HALO_SCRIPT.includes('pointer-events:none'));
});

test('browser: navigate, press, extract, screenshot, wait', async () => {
  const page = fakePage({ nodes: NODES });
  const h = new BrowserHarness({ open: async () => page, sleep: async () => {} });
  const nav = await h.perform({ action: 'navigate', value: 'https://sandbox.example.test/new' });
  assert.equal(nav.ok, true);
  assert.equal(nav.result.url_after, 'https://sandbox.example.test/new');
  assert.equal(nav.description, 'Opened Loaded https://sandbox.example.test/new');

  const press = await h.perform({ action: 'press', value: 'Enter' });
  assert.equal(press.ok, true);
  const keys = page.calls.filter(([m]) => m === 'Input.dispatchKeyEvent').map(([, p]) => [p.type, p.key, p.windowsVirtualKeyCode]);
  assert.deepEqual(keys, [
    ['keyDown', 'Enter', 13],
    ['keyUp', 'Enter', 13],
  ]);

  const table = await h.perform({ action: 'extract', value: 'table', target_id: '40', observation_id: press.observation.observation_id });
  assert.deepEqual(table.result, { columns: ['Invoice', 'Total'], rows: [['INV-1', '10']], count: 1 });
  assert.equal(table.description, 'Extracted 1 rows');
  const text = await h.perform({ action: 'extract', value: 'text' });
  assert.equal(text.result.text, 'Invoice INV-1 total 10');

  const shot = await h.perform({ action: 'screenshot' });
  assert.equal(shot.evidence.screenshot_base64, 'iVBORw0KGgo=');
  assert.equal(shot.evidence.content_type, 'image/png');

  const wait = await h.perform({ action: 'wait', value: 5 });
  assert.equal(wait.ok, true);
  assert.ok(wait.observation.observation_id);

  await h.close();
  assert.deepEqual(page.calls.at(-1), ['close']);
  assert.equal(h.page, null);
});

test('browser: a private page yields an empty, flagged observation and refuses steps', async () => {
  const page = fakePage({ nodes: NODES, title: 'Sign in to Payroll', url: 'https://payroll.example.test/login' });
  const h = new BrowserHarness({ open: async () => page, sleep: async () => {} });
  const obs = await h.perform({ action: 'observe' });
  assert.equal(obs.ok, true);
  assert.equal(obs.observation.sensitive, true);
  assert.deepEqual(obs.observation.candidates, []);
  assert.equal(obs.observation.title, '');
  assert.equal(page.calls.some(([m]) => m === 'Accessibility.getFullAXTree'), false);
  const shot = await h.perform({ action: 'screenshot' });
  assert.equal(shot.ok, false);
  assert.equal(shot.error.code, 'sensitive_window');
  const press = await h.perform({ action: 'press', value: 'Enter' });
  assert.equal(press.error.code, 'sensitive_window');
  assert.equal(page.calls.some(([m]) => m === 'Input.dispatchKeyEvent'), false);
});

test('browser: the settings private-app list applies to pages too', async () => {
  const page = fakePage({ nodes: NODES, title: 'Vault — Company Secrets' });
  const h = new BrowserHarness({ open: async () => page, sleep: async () => {}, settings: () => ({ privateApps: [], privateTitles: ['vault'] }) });
  const obs = await h.perform({ action: 'observe' });
  assert.equal(obs.observation.sensitive, true);
});

test('browser: without a page opener the harness is unsupported and refuses', async () => {
  const h = new BrowserHarness({});
  assert.equal(h.supported, false);
  assert.deepEqual(h.capabilities(), []);
  const r = await h.perform({ action: 'observe' });
  assert.equal(r.error.code, 'harness_unsupported');
});

// ---- desktop ----

function fakeBackend({ windows }) {
  const calls = [];
  let i = 0;
  return {
    calls,
    next: () => (i += 1),
    activeWindow: async () => windows[Math.min(i, windows.length - 1)],
    elements: async (w) => w.elements ?? [],
    moveTo: async (x, y) => calls.push(['move', x, y]),
    click: async () => calls.push(['click']),
    type: async (t) => calls.push(['type', t]),
    press: async (k) => calls.push(['press', k]),
    openApp: async (n) => calls.push(['open', n]),
    screenshot: async () => 'iVBORw0KGgo=',
  };
}

const FORM = {
  app: 'Ledger',
  title: 'New bill',
  bounds: { x: 0, y: 0, width: 800, height: 600 },
  elements: [
    { id: 'ax:0', role: 'text field', name: 'Vendor', x: 100, y: 100, width: 200, height: 24 },
    { id: 'ax:1', role: 'secure text field', name: 'PIN', x: 100, y: 140, width: 200, height: 24 },
    { id: 'ax:2', role: 'button', name: 'Save', x: 300, y: 500, width: 80, height: 30 },
    { id: 'ax:3', role: 'button', name: 'Save', x: 400, y: 500, width: 80, height: 30 },
    { id: 'ax:4', role: 'button', name: 'Hidden', x: 0, y: 0, width: 0, height: 0 },
    { id: 'ax:5', role: 'static text', name: 'Amount due', x: 0, y: 0, width: 50, height: 10 },
    { id: 'ax:6', role: 'check box', name: 'Paid', x: 100, y: 200, width: 20, height: 20, enabled: false },
  ],
};

test('desktop: elements → candidates (no secure fields, no zero-size, no duplicates)', () => {
  const c = candidatesFromElements(FORM.elements);
  assert.deepEqual(
    c.map((x) => [x.id, x.role, x.name, x.kind, x.attrs]),
    [
      ['ax:0', 'text_field', 'Vendor', 'field', {}],
      ['ax:2', 'button', 'Save', 'interactive', {}],
      ['ax:6', 'check_box', 'Paid', 'interactive', { disabled: true }],
    ],
  );
});

test('desktop: observe, click and type move the real pointer to the observed control', async () => {
  const b = fakeBackend({ windows: [FORM] });
  const h = new DesktopHarness({ backend: b, sleep: async () => {} });
  assert.equal(h.supported, true);
  const obs = await h.perform({ action: 'observe' });
  assert.equal(obs.observation.app, 'Ledger');
  assert.equal(obs.observation.candidates.length, 3);
  const oid = obs.observation.observation_id;

  const typed = await h.perform({ action: 'type', target_id: 'ax:0', observation_id: oid, value: 'Acme Ltd' });
  assert.equal(typed.ok, true);
  assert.deepEqual(b.calls, [['move', 200, 112], ['click'], ['press', 'SelectAll'], ['type', 'Acme Ltd']]);

  b.calls.length = 0;
  const click = await h.perform({ action: 'click', target_id: 'ax:2', observation_id: typed.observation.observation_id, description: 'Click button: Save' });
  assert.equal(click.ok, true);
  assert.deepEqual(b.calls, [['move', 340, 515], ['click']]);
  assert.equal(click.result.window_changed, false);

  const pin = await h.perform({ action: 'click', target_id: 'ax:1', observation_id: click.observation.observation_id });
  assert.equal(pin.error.code, 'stale_observation'); // never a candidate, so never a target
});

test('desktop: the front window must still be the observed one', async () => {
  const other = { app: 'Mail', title: 'Inbox', elements: [] };
  const b = fakeBackend({ windows: [FORM, other] });
  const h = new DesktopHarness({ backend: b, sleep: async () => {} });
  const obs = await h.perform({ action: 'observe' });
  b.next();
  const r = await h.perform({ action: 'click', target_id: 'ax:2', observation_id: obs.observation.observation_id });
  assert.equal(r.ok, false);
  assert.equal(r.error.code, 'stale_observation');
  assert.deepEqual(b.calls, []);
});

test('desktop: private windows are never observed or acted on', async () => {
  const b = fakeBackend({ windows: [{ app: 'Ledger', title: 'Sign in — Ledger', elements: FORM.elements }] });
  const h = new DesktopHarness({ backend: b, sleep: async () => {} });
  const obs = await h.perform({ action: 'observe' });
  assert.equal(obs.observation.sensitive, true);
  assert.deepEqual(obs.observation.candidates, []);
  assert.equal(obs.observation.window_title, '');
  const press = await h.perform({ action: 'press', value: 'Enter' });
  assert.equal(press.error.code, 'sensitive_window');
  const shot = await h.perform({ action: 'screenshot' });
  assert.equal(shot.error.code, 'sensitive_window');
  assert.deepEqual(b.calls, []);

  const listed = new DesktopHarness({ backend: fakeBackend({ windows: [FORM] }), settings: () => ({ privateApps: ['ledger'], privateTitles: [] }) });
  assert.equal((await listed.perform({ action: 'observe' })).observation.sensitive, true);
});

test('desktop: open_app/navigate, press, screenshot, wait; unsupported without a backend', async () => {
  const b = fakeBackend({ windows: [FORM] });
  const h = new DesktopHarness({ backend: b, sleep: async () => {} });
  const open = await h.perform({ action: 'open_app', value: 'Ledger' });
  assert.equal(open.ok, true);
  assert.deepEqual(b.calls, [['open', 'Ledger']]);
  const press = await h.perform({ action: 'press', value: 'Tab' });
  assert.equal(press.ok, true);
  assert.deepEqual(b.calls.at(-1), ['press', 'Tab']);
  const shot = await h.perform({ action: 'screenshot' });
  assert.equal(shot.evidence.content_type, 'image/png');
  assert.equal((await h.perform({ action: 'wait', value: 1 })).ok, true);
  assert.equal((await h.perform({ action: 'extract', value: 'text' })).error.code, 'invalid_value');

  const none = new DesktopHarness({});
  assert.equal(none.supported, false);
  assert.equal((await none.perform({ action: 'observe' })).error.code, 'harness_unsupported');
});

test('desktop-macos: parses the System Events listing', () => {
  const out = parseElements('button\tSave\t300\t500\t80\t30\t1\ntext field\tVendor\t100\t100\t200\t24\t0\n\n');
  assert.deepEqual(out, [
    { id: 'ax:0', role: 'button', name: 'Save', x: 300, y: 500, width: 80, height: 30, enabled: true },
    { id: 'ax:1', role: 'text field', name: 'Vendor', x: 100, y: 100, width: 200, height: 24, enabled: false },
  ]);
});

test('registry: drivers are advertised only when their platform pieces exist', () => {
  assert.deepEqual(capabilitiesOf(defaultHarnesses()), { browser: false, desktop: false });
  assert.deepEqual(capabilitiesOf(defaultHarnesses({ demo: true })), { browser: true, desktop: true });
  const real = defaultHarnesses({ openPage: async () => fakePage({ nodes: NODES }), desktopBackend: null });
  assert.deepEqual(capabilitiesOf(real), { browser: true, desktop: false });
  assert.ok(real.browser instanceof BrowserHarness);
  const both = defaultHarnesses({ openPage: async () => fakePage({ nodes: NODES }), desktopBackend: fakeBackend({ windows: [FORM] }) });
  assert.deepEqual(capabilitiesOf(both), { browser: true, desktop: true });
  assert.ok(both.desktop instanceof DesktopHarness);
});
