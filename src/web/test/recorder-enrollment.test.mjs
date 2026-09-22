import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { JSDOM } from 'jsdom';

const html = fs.readFileSync(new URL('../../recorder/ui/dashboard.html', import.meta.url), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n');
const id = '00000000-0000-0000-0000-000000000001';
const other = '00000000-0000-0000-0000-000000000002';
const binding = { url: 'https://vista.example', userId: id, tenantId: other, workspace: { id, kind: 'company' } };

async function settle(check) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail('Recorder UI did not settle');
}

function page({ connected = false, accepted = false } = {}) {
  const dom = new JSDOM(html, { url: 'file:///recorder/ui/dashboard.html', runScripts: 'outside-only' });
  const win = dom.window;
  const errors = [];
  win.addEventListener('error', (event) => errors.push(event.error));
  win.HTMLMediaElement.prototype.load = () => {};
  win.HTMLMediaElement.prototype.pause = () => {};
  win.HTMLMediaElement.prototype.play = () => Promise.resolve();
  win.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  win.HTMLDialogElement.prototype.close = function () { this.open = false; };
  win.HTMLElement.prototype.scrollIntoView = () => {};
  win.alert = (message) => errors.push(new Error(message));
  const record = {
    recording_id: 'session-1', name: 'Recorded work', started_at: '2026-09-19T09:00:00Z', ended_at: '2026-09-19T09:10:00Z',
    active_seconds: 600, processing: 'awaiting_upload', upload_ready: true, counts: { total: 2 }, apps: [], annotations: 0,
    upload: accepted ? { protocol: 2, status: 'accepted' } : null,
  };
  const sections = {
    recording_id: record.recording_id, started_at: record.started_at, ended_at: record.ended_at,
    video: null, video_url: null, pauses: [], sections: [], apps: [], files: [], submitted: null,
    insights: { flags: [], input: null, trends: null, summary: null, summary_counts: { flags: 0, open_flags: 0 } },
    review: { enabled: false, generating: false, summary: { total: 0, open: 0 }, items: {} }, workflows: null,
  };
  const state = { discover: [], connect: [], submit: [], onReview: null };
  let connection = { connected, protocol: connected ? 2 : null, companyName: 'Company A', email: 'employee@example.com', url: binding.url };
  win.vista = {
    info: async () => ({ admin: false, demo: false, home: '/local', platform: 'linux', user: 'Employee', cloud: connection.connected, ai: { enabled: false } }),
    status: async () => ({ state: 'idle', elapsedMs: 0, counts: { total: 0 }, apps: [] }),
    recordings: async () => [record],
    sections: async () => sections,
    permissions: async () => ({ needed: false, ok: true }),
    getSettings: async () => ({ ownApps: [], privateApps: [], privateTitles: [] }),
    cloudStatus: async () => connection,
    cloudDiscover: async (input) => {
      state.discover.push(input);
      return { user_id: id, tenant_id: other, email: 'employee@example.com', workspaces: [
        { id, kind: 'company', name: 'Company A' },
        { id: other, kind: 'deal', name: '<img src=x>Company B' },
      ] };
    },
    cloudConnect: async (input) => {
      state.connect.push(input);
      connection = { ...connection, connected: true, protocol: 2, companyName: input.workspace.id === id ? 'Company A' : 'Company B' };
      return connection;
    },
    cloudDisconnect: async () => { connection = { connected: false }; return connection; },
    uploadPreview: async () => ({ companyName: 'Company A', email: 'employee@example.com', binding, documents: [
      { id: 'abcdef123456', name: '<img src=x>invoice.csv', size_bytes: 32 },
    ] }),
    submit: async (recordId, options) => { state.submit.push({ recordId, options }); return sections; },
    retryUploads: async () => connection,
    onStatus() {}, onRecordings() {}, onSections() {}, onPermissions() {},
    onReview(callback) { state.onReview = callback; },
  };
  win.eval(script);
  return { dom, state, errors, $: (key) => win.document.getElementById(key) };
}

test('ordinary employees can connect without admin mode, manual IDs or defaulting to the first company', async () => {
  const ui = page();
  try {
    await settle(() => ui.$('cloud-state').textContent.includes('Not connected'));
    assert.equal(ui.$('admin-settings').contains(ui.$('cloud-enrollment')), false);
    assert.equal(ui.$('view-settings').classList.contains('hidden'), false);
    ui.$('cloud-key').value = 'synthetic-personal-access-key';
    ui.$('cloud-discover').click();
    await settle(() => ui.$('cloud-company').options.length === 3);
    assert.equal(ui.$('cloud-key').value, '');
    assert.equal(ui.$('cloud-company').value, '');
    assert.equal(ui.$('cloud-connect').disabled, true);
    assert.equal(ui.$('cloud-enrollment').querySelectorAll('img').length, 0);
    ui.$('cloud-consent').checked = true;
    ui.$('cloud-consent').dispatchEvent(new ui.dom.window.Event('change'));
    assert.equal(ui.$('cloud-connect').disabled, true);
    ui.$('cloud-company').value = '1';
    ui.$('cloud-company').dispatchEvent(new ui.dom.window.Event('change'));
    assert.equal(ui.$('cloud-connect').disabled, false);
    ui.$('cloud-connect').click();
    await settle(() => ui.$('cloud-state').textContent.includes('Connected to Company B'));
    assert.equal(ui.state.connect.length, 1);
    assert.equal(ui.state.connect[0].workspace.id, other);
    assert.equal(ui.state.connect[0].workspace.kind, 'deal');
    assert.equal(ui.state.connect[0].consent, true);
    assert.equal(ui.state.connect[0].token, undefined);
    assert.deepEqual(ui.errors, []);
  } finally { ui.dom.window.close(); }
});

for (const selectDocument of [false, true]) {
  test(`unprocessed sessions can be uploaded with explicit consent; document selected=${selectDocument}`, async () => {
    const ui = page({ connected: true });
    try {
      await settle(() => ui.state.onReview && ui.$('cloud-state').textContent.includes('Connected'));
      await ui.state.onReview('session-1');
      await settle(() => ui.$('rv-submit').disabled === false);
      assert.match(ui.$('rv-submit').textContent, /Upload session/);
      ui.$('rv-submit').click();
      await settle(() => ui.$('upload-dialog').open);
      assert.equal(ui.$('upload-confirm').disabled, true);
      const checkbox = ui.$('upload-documents').querySelector('input');
      assert.equal(checkbox.checked, false);
      assert.equal(ui.$('upload-documents').querySelectorAll('img').length, 0);
      checkbox.checked = selectDocument;
      ui.$('upload-consent').checked = true;
      ui.$('upload-consent').dispatchEvent(new ui.dom.window.Event('change'));
      ui.$('upload-confirm').click();
      await settle(() => ui.state.submit.length === 1 && !ui.$('upload-dialog').open);
      const request = ui.state.submit[0];
      assert.equal(request.recordId, 'session-1');
      assert.equal(request.options.consent, true);
      assert.equal(request.options.expectedBinding.workspace.id, id);
      assert.equal(request.options.selectedFileIds.length, selectDocument ? 1 : 0);
      assert.deepEqual(ui.errors, []);
    } finally { ui.dom.window.close(); }
  });
}

test('the recordings list uploads through the same consent dialog as the review view', async () => {
  const ui = page({ connected: true });
  try {
    await settle(() => ui.$('cloud-state').textContent.includes('Connected') && ui.$('rec-rows').querySelector('[data-submit="session-1"]'));
    const button = ui.$('rec-rows').querySelector('[data-submit="session-1"]');
    assert.equal(button.disabled, false);
    assert.match(button.textContent, /Upload/);
    button.click();
    await settle(() => ui.$('upload-dialog').open);
    assert.equal(ui.state.submit.length, 0);
    ui.$('upload-consent').checked = true;
    ui.$('upload-consent').dispatchEvent(new ui.dom.window.Event('change'));
    ui.$('upload-confirm').click();
    await settle(() => ui.state.submit.length === 1 && !ui.$('upload-dialog').open);
    assert.equal(ui.state.submit[0].recordId, 'session-1');
    assert.equal(ui.state.submit[0].options.consent, true);
    assert.equal(ui.state.submit[0].options.expectedBinding.workspace.id, id);
    assert.equal(ui.state.submit[0].options.selectedFileIds.length, 0);
    assert.deepEqual(ui.errors, []);
  } finally { ui.dom.window.close(); }
});

test('receipt confirmation is shown as awaiting analysis, not a completed or published report', async () => {
  const ui = page({ connected: true, accepted: true });
  try {
    await settle(() => ui.state.onReview && ui.$('cloud-state').textContent.includes('Connected'));
    await settle(() => ui.$('rec-rows').textContent.includes('Uploaded'));
    assert.equal(ui.$('rec-rows').querySelector('[data-submit]'), null);
    await ui.state.onReview('session-1');
    assert.match(ui.$('rv-banner-t').textContent, /awaiting analysis/);
    assert.match(ui.$('rv-banner-s').textContent, /Local originals are retained/);
    assert.match(ui.$('rv-banner-s').textContent, /no report has been published/);
    assert.equal(ui.$('rv-submit').classList.contains('hidden'), true);
    assert.deepEqual(ui.errors, []);
  } finally { ui.dom.window.close(); }
});
