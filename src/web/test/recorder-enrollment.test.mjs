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

const REPORT = {
  id: '00000000-0000-0000-0000-00000000000a', submission_id: '00000000-0000-0000-0000-00000000000b', run_id: null, status: 'draft',
  coverage: { note: 'Activities are known only at application level.', excluded: ['window_title'] },
  observed: { events: 40, switches: 9, apps: [{ app: '<img src=x>Excel', share: 0.6, events: 30, copies: 5, pastes: 0 }, { app: 'Portal', share: 0.4, events: 10, copies: 0, pastes: 5 }], transfers: [{ from: '<img src=x>Excel', to: 'Portal', count: 5, mean_latency_s: 60 }] },
  interpretation: { source: 'stub', summary: '', workflows: [], automation_candidates: [], documents: [] },
  questions: [
    { id: 'q1', source: 'observed', question: 'You copied from <img src=x>Excel and pasted into Portal 5 times. What moves?', about: {}, answer: null, answered_at: null },
    { id: 'q2', source: 'observed', question: 'Most of the session was in Excel. Which task?', about: {}, answer: null, answered_at: null },
  ],
  questions_open: 2, questions_total: 2, published_at: null,
};

function page({ connected = false, accepted = false, analysis = null } = {}) {
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
    upload: accepted ? { protocol: 2, status: 'accepted', analysis_status: analysis?.status ?? 'not_started', publication_status: analysis?.publication ?? 'draft', analysis } : null,
  };
  const sections = {
    recording_id: record.recording_id, started_at: record.started_at, ended_at: record.ended_at,
    video: null, video_url: null, pauses: [], sections: [], apps: [], files: [], submitted: null,
    insights: { flags: [], input: null, trends: null, summary: null, summary_counts: { flags: 0, open_flags: 0 } },
    review: { enabled: false, generating: false, summary: { total: 0, open: 0 }, items: {} }, workflows: null,
  };
  const state = { discover: [], connect: [], submit: [], answers: [], publish: [], reanalyze: 0, refresh: 0, onReview: null };
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
    analysis: async () => { state.refresh += 1; return sections; },
    answerQuestions: async (recordId, answers) => {
      state.answers.push({ recordId, answers });
      for (const q of record.upload.analysis.report.questions) if (answers[q.id] !== undefined) q.answer = answers[q.id].trim() || null;
      return sections;
    },
    publishReport: async (recordId, options) => {
      state.publish.push({ recordId, options });
      record.upload.analysis.publication = 'published';
      record.upload.analysis.report.status = 'published';
      record.upload.analysis.report.published_at = '2026-09-22T10:00:00Z';
      return sections;
    },
    reanalyze: async () => { state.reanalyze += 1; record.upload.analysis.status = 'queued'; return sections; },
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
    assert.match(ui.$('rv-banner-t').textContent, /Uploaded — analysing/);
    assert.match(ui.$('rv-banner-s').textContent, /no report has been published/);
    assert.equal(ui.$('rv-submit').classList.contains('hidden'), true);
    assert.equal(ui.$('rv-cloud').classList.contains('hidden'), false);
    assert.match(ui.$('rv-cloud-body').textContent, /Recording Reviewer/);
    assert.equal(ui.$('rv-cloud-publish'), null);
    ui.$('rv-cloud-refresh').click();
    await settle(() => ui.state.refresh === 1);
    assert.deepEqual(ui.errors, []);
  } finally { ui.dom.window.close(); }
});

test('a failed analysis can be retried from the review view', async () => {
  const ui = page({ connected: true, accepted: true, analysis: { status: 'failed', error: 'artifact <b>lost</b>', publication: 'draft', report: null } });
  try {
    await settle(() => ui.state.onReview);
    await ui.state.onReview('session-1');
    assert.match(ui.$('rv-banner-t').textContent, /analysis failed/);
    assert.equal(ui.$('rv-cloud-body').querySelector('b'), null);
    assert.match(ui.$('rv-cloud-body').textContent, /artifact <b>lost<\/b>/);
    ui.$('rv-cloud-retry').click();
    await settle(() => ui.state.reanalyze === 1 && /analysing/.test(ui.$('rv-banner-t').textContent));
    assert.deepEqual(ui.errors, []);
  } finally { ui.dom.window.close(); }
});

test('a finished analysis shows observed facts apart from hypotheses, takes answers, and publishes only with consent', async () => {
  const report = JSON.parse(JSON.stringify(REPORT));
  const ui = page({ connected: true, accepted: true, analysis: { status: 'succeeded', error: null, publication: 'draft', report } });
  try {
    await settle(() => ui.state.onReview);
    await ui.state.onReview('session-1');
    assert.match(ui.$('rv-banner-t').textContent, /Report ready/);
    const body = ui.$('rv-cloud-body');
    assert.equal(body.querySelectorAll('img').length, 0);
    assert.match(body.textContent, /Observed \(from metadata\)/);
    assert.match(body.textContent, /Agent's reading \(hypotheses\)/);
    assert.match(body.textContent, /No model interpretation/);
    assert.match(body.textContent, /9 app switches/);
    assert.equal(body.querySelectorAll('textarea[data-q]').length, 2);
    assert.equal(ui.$('rv-cloud-publish').disabled, true);
    body.querySelector('textarea[data-q="q1"]').value = ' Vendor statements ';
    ui.$('rv-cloud-save').click();
    await settle(() => ui.state.answers.length === 1);
    assert.equal(JSON.stringify(ui.state.answers[0]), JSON.stringify({ recordId: 'session-1', answers: { q1: ' Vendor statements ', q2: '' } }));
    await settle(() => /1 question to answer/.test(ui.$('rv-cloud-sub').textContent));
    assert.equal(ui.state.publish.length, 0);
    ui.$('rv-cloud-consent').checked = true;
    ui.$('rv-cloud-consent').dispatchEvent(new ui.dom.window.Event('change'));
    assert.equal(ui.$('rv-cloud-publish').disabled, false);
    ui.$('rv-cloud-publish').click();
    await settle(() => ui.state.publish.length === 1 && /Published/.test(ui.$('rv-banner-t').textContent));
    assert.equal(JSON.stringify(ui.state.publish[0]), JSON.stringify({ recordId: 'session-1', options: { consent: true } }));
    assert.equal(ui.$('rv-cloud-publish'), null);
    assert.ok([...ui.$('rv-cloud-body').querySelectorAll('textarea')].every((t) => t.disabled));
    assert.match(ui.$('rec-rows').textContent, /Published to workspace/);
    assert.deepEqual(ui.errors, []);
  } finally { ui.dom.window.close(); }
});
