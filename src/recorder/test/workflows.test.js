import test from 'node:test';
import assert from 'node:assert/strict';

import { appRole, inferEnvironment, refineSessionWorkflow, sessionDigest, suggestWorkflows, workflowTrends, workflowsStub } from '../src/workflows.js';
import { buildInsights, summarizeInsights } from '../src/insights.js';

const T0 = Date.parse('2026-03-02T09:00:00Z');
const at = (ms) => new Date(T0 + ms).toISOString();
const ev = (ms, event_type, app, extra = {}) => ({ timestamp: at(ms), event_type, app, window_title: '', url: '', text: '', payload: {}, ...extra });

// An AP clerk: read the invoice PDF, key it into QuickBooks and the tracker, answer mail, check a site.
const manifest = {
  recording_id: 'r1', started_at: at(0), ended_at: at(600000), active_seconds: 600,
  counts: { click: 30, key: 200, paste: 8, copy: 4, focus: 12 },
  apps: [{ app: 'Microsoft Outlook', seconds: 150 }, { app: 'QuickBooks', seconds: 140 }, { app: 'Adobe Acrobat', seconds: 120 }, { app: 'Microsoft Excel', seconds: 100 }, { app: 'Google Chrome', seconds: 60 }, { app: '(private)', seconds: 30 }],
  summary: { steps: 8, cases: 2, open_questions: 1, top_activities: [{ activity: 'Read Email', count: 2, total_s: 80 }, { activity: 'Write Reply Email', count: 1, total_s: 60 }, { activity: 'QuickBooks', count: 2, total_s: 140 }], automation: [{ activity: 'QuickBooks', score: 0.74 }, { activity: 'Filing', score: 0.62 }], top_variant: null },
};
const events = [
  ev(0, 'focus', 'Microsoft Outlook', { window_title: 'Inbox - Outlook' }),
  ev(20000, 'focus', 'Adobe Acrobat', { window_title: 'INV-48213 Carrier Parts.pdf - Adobe Acrobat Reader' }),
  ev(30000, 'copy', 'Adobe Acrobat'),
  ev(40000, 'focus', 'QuickBooks', { window_title: 'Enter Bills - QuickBooks Desktop' }),
  ev(41000, 'paste', 'QuickBooks', { payload: { chars: 22, source_app: 'Adobe Acrobat', source_title: 'INV-48213 Carrier Parts.pdf', transfer_ms: 900 } }),
  ev(45000, 'paste', 'QuickBooks', { payload: { chars: 8, source_app: 'Adobe Acrobat', transfer_ms: 700 } }),
  ev(50000, 'paste', 'QuickBooks', { payload: { chars: 10, source_app: 'Adobe Acrobat', transfer_ms: 800 } }),
  ev(60000, 'focus', 'Microsoft Excel', { window_title: 'AP tracker 2026.csv - Excel' }),
  ev(61000, 'paste', 'Microsoft Excel', { payload: { chars: 22, source_app: 'Adobe Acrobat', transfer_ms: 1200 } }),
  ev(62000, 'paste', 'Microsoft Excel', { payload: { chars: 9, source_app: 'Adobe Acrobat', transfer_ms: 1000 } }),
  ev(70000, 'focus', 'Microsoft Outlook', { window_title: 'RE: invoice - Outlook' }),
  ev(80000, 'focus', 'Google Chrome', { window_title: 'Carrier Enterprise - Google Chrome', url: 'https://www.carrierenterprise.com/orders/123' }),
  ev(90000, 'focus', '(private)', { window_title: '(private)' }),
  ev(95000, 'paste', 'Microsoft Outlook', { payload: { chars: 5, source_app: 'Microsoft Outlook' } }),
];
const files = [
  { id: 'f1', name: 'AP tracker 2026.csv', ext: '.csv', edited: true, intervals: [{ app: 'Microsoft Excel' }], review: { status: 'parsed', flags: [] } },
  { id: 'f2', name: 'INV-48213 Carrier Parts.pdf', ext: '.pdf', edited: false, intervals: [{ app: 'Adobe Acrobat' }], review: { status: 'unsupported', flags: [] } },
];

test('appRole classifies from app name and window title', () => {
  assert.equal(appRole('QuickBooks'), 'accounting');
  assert.equal(appRole('Odoo', 'Customer Invoices - Odoo'), 'accounting');
  assert.equal(appRole('Odoo', 'Pipeline - CRM - Odoo'), 'crm');
  assert.equal(appRole('(private)'), 'password_manager');
  assert.equal(appRole('Figma'), 'other');
});

test('inferEnvironment pulls apps by role, documents from files + titles, sites from URLs', () => {
  const env = inferEnvironment({ manifest, events, files });
  assert.deepEqual(env.apps.map((a) => a.role), ['email', 'accounting', 'pdf', 'spreadsheet', 'browser', 'password_manager']);
  assert.deepEqual(env.roles.sort(), ['accounting', 'browser', 'email', 'password_manager', 'pdf', 'spreadsheet']);
  assert.deepEqual(env.documents.map((d) => d.name), ['AP tracker 2026.csv', 'INV-48213 Carrier Parts.pdf']);
  assert.deepEqual(env.sites, [{ host: 'carrierenterprise.com', n: 1 }]);
});

test('suggestWorkflows combines transfers, documents, taskmining activities, sites and sign-in', () => {
  const wf = suggestWorkflows({ manifest, events, files, summary: manifest.summary });
  const ids = wf.workflows.map((w) => w.id);
  assert.ok(ids.includes('rekey-adobe-acrobat-quickbooks'), ids.join());
  assert.ok(ids.includes('rekey-adobe-acrobat-microsoft-excel'));
  assert.ok(ids.includes('intake-quickbooks'));
  assert.ok(ids.includes('tracker-ap-tracker-2026-csv'));
  assert.ok(ids.includes('inbox-triage'));
  assert.ok(ids.includes('web-lookup'));
  assert.ok(ids.includes('sign-in'));
  assert.ok(ids.includes('activity-filing'), 'uncovered taskmining candidate is kept');
  assert.equal(ids.some((i) => i === 'activity-quickbooks'), false, 'covered by the re-keying workflow');
  const rekey = wf.workflows.find((w) => w.id === 'rekey-adobe-acrobat-quickbooks');
  assert.equal(rekey.evidence.pastes, 3);
  assert.equal(rekey.evidence.mean_transfer_s, 0.8);
  assert.deepEqual(rekey.sources, ['events']);
  assert.equal(wf.workflows.find((w) => w.id === 'intake-quickbooks').title, 'Invoice intake into QuickBooks');
  assert.deepEqual(wf.workflows.find((w) => w.id === 'inbox-triage').sources, ['taskmining', 'events']);
  assert.equal(wf.workflows.find((w) => w.id === 'web-lookup').title, 'Look up carrierenterprise.com during the work');
  for (let i = 1; i < wf.workflows.length; i++) assert.ok(wf.workflows[i - 1].automation >= wf.workflows[i].automation, 'sorted by automation');
  assert.equal('_roleOf' in wf.environment, false);
  assert.deepEqual(workflowsStub(wf), { count: wf.workflows.length, ids, kinds: [...new Set(wf.workflows.map((w) => w.kind))] });
});

test('suggestWorkflows: thin evidence still yields suggestions', () => {
  const figma = suggestWorkflows({ manifest: { apps: [{ app: 'Figma', seconds: 10 }] }, events: [ev(0, 'paste', 'Figma', { payload: { source_app: 'Figma' } })], files: [], summary: null });
  assert.deepEqual(figma.workflows.map((w) => w.id), ['session'], 'unrecognised app alone falls back to one session workflow');
  assert.equal(figma.fallback, true);
  assert.equal(figma.workflows[0].kind, 'session');
  assert.equal(figma.workflows[0].title, 'Session in Figma');
  const empty = suggestWorkflows({ manifest: {}, events: [], files: [], summary: null });
  assert.deepEqual(empty.workflows, []);
  const onePaste = suggestWorkflows({ manifest: { apps: [{ app: 'Adobe Acrobat', seconds: 5 }, { app: 'QuickBooks', seconds: 5 }] }, events: [ev(0, 'paste', 'QuickBooks', { payload: { source_app: 'Adobe Acrobat', chars: 8, transfer_ms: 900 } })], files: [], summary: null });
  const rekey = onePaste.workflows.find((w) => w.id === 'rekey-adobe-acrobat-quickbooks');
  assert.equal(rekey.evidence.pastes, 1);
  assert.match(rekey.why, /^1 value was copied/);
  const switches = suggestWorkflows({ manifest: { apps: [{ app: 'Microsoft Excel', seconds: 5 }, { app: 'QuickBooks', seconds: 5 }] }, events: [ev(0, 'focus', 'Microsoft Excel'), ev(1, 'focus', 'QuickBooks')], files: [], summary: null });
  assert.ok(switches.workflows.some((w) => w.id === 'carry-microsoft-excel-quickbooks'), 'one switch between two recognised systems counts');
  const oneDoc = suggestWorkflows({ manifest: { apps: [{ app: 'Adobe Acrobat', seconds: 5 }] }, events: [], files: [{ name: 'quote.pdf', ext: '.pdf', intervals: [{ app: 'Adobe Acrobat' }] }], summary: null });
  assert.equal(oneDoc.workflows.find((w) => w.id === 'intake-pdf').title, 'Read document PDFs');
  const oneSheet = suggestWorkflows({ manifest: {}, events: [], files: [{ name: 'list.xlsx', ext: '.xlsx', intervals: [{ app: 'Microsoft Excel' }] }], summary: null });
  assert.equal(oneSheet.workflows[0].id, 'tracker-list-xlsx', 'an open, unedited sheet is enough');
  const oneWord = suggestWorkflows({ manifest: {}, events: [], files: [{ name: 'memo.docx', ext: '.docx', edited: true }], summary: null });
  assert.equal(oneWord.workflows[0].title, 'Update the “memo.docx” document');
  const lowScore = suggestWorkflows({ manifest: {}, events: [], files: [], summary: { automation: [{ activity: 'Filing', score: 0.2 }] } });
  assert.equal(lowScore.workflows[0].id, 'activity-filing', 'no automation-score floor');
});

test('workflowTrends: no baseline without previous summaries; recurrence from manifest stubs', () => {
  const wf = suggestWorkflows({ manifest, events, files, summary: manifest.summary });
  const none = workflowTrends({ summary: manifest.summary, workflows: wf }, [{ counts: {}, active_seconds: 10 }]);
  assert.equal(none.baseline, 0);
  assert.equal(none.recurring.find((r) => r.id === 'intake-quickbooks').seen_before, 0);
  const prev = [
    { active_seconds: 500, summary: { steps: 12, cases: 2, open_questions: 3 }, workflows: { ids: ['intake-quickbooks', 'sign-in'] } },
    { active_seconds: 700, summary: { steps: 10, cases: 1, open_questions: 1 }, workflows: { ids: ['intake-quickbooks'] } },
  ];
  const t = workflowTrends({ summary: manifest.summary, workflows: wf }, prev);
  assert.equal(t.baseline, 2);
  const by = Object.fromEntries(t.metrics.map((m) => [m.key, m]));
  assert.equal(by.steps_per_case.now, 4);
  assert.equal(by.steps_per_case.baseline, 8); // (6 + 10) / 2
  assert.equal(by.steps_per_case.delta_pct, -50);
  assert.equal(by.cases.now, 2);
  assert.equal(by.workflows.now, wf.workflows.length);
  assert.equal(t.recurring.find((r) => r.id === 'intake-quickbooks').seen_before, 2);
  assert.equal(t.recurring.find((r) => r.id === 'sign-in').seen_before, 1);
});

test('buildInsights carries workflow trends + a compact workflow list; the model gets them only with a baseline', async () => {
  const wf = suggestWorkflows({ manifest, events, files, summary: manifest.summary });
  const first = buildInsights({ manifest, events, sections: [], files: [], previous: [], workflows: wf });
  assert.equal(first.trends.workflow.baseline, 0);
  assert.equal(first.workflows.length, wf.workflows.length);
  assert.deepEqual(Object.keys(first.workflows[0]).sort(), ['apps', 'automation', 'id', 'kind', 'title']);

  let sent;
  const fetchFn = async (_url, init) => { sent = JSON.parse(init.body); return { ok: true, json: async () => ({ model: 'm', choices: [{ message: { content: '{"summary":"ok"}' } }] }) }; };
  await summarizeInsights({ ...first, flags: [] }, { key: 'k', model: 'm' }, { fetchFn });
  let user = JSON.parse(sent.messages[1].content);
  assert.equal('trends' in user, false, 'no baseline → nothing about trends');
  assert.equal(user.workflows.length, 5);
  assert.deepEqual(user.workflows[0].apps, ['Adobe Acrobat', 'QuickBooks']);

  const prev = [{ active_seconds: 500, counts: { click: 10 }, summary: { steps: 12, cases: 2, open_questions: 3 }, workflows: { ids: ['intake-quickbooks'] } }];
  const second = buildInsights({ manifest, events, sections: [], files: [], previous: prev, workflows: wf });
  await summarizeInsights({ ...second, flags: [] }, { key: 'k', model: 'm' }, { fetchFn });
  user = JSON.parse(sent.messages[1].content);
  assert.equal(user.trends.workflow.baseline, 1);
  assert.deepEqual(user.trends.workflow.recurring.map((r) => r.id), ['intake-quickbooks'], 'only recurring workflows are sent');
});

test('session fallback: one workflow from the digest, rewritten by the model when a key is present', async () => {
  const m = { apps: [{ app: 'Figma', seconds: 300 }, { app: 'Notion', seconds: 120 }] };
  const evs = [ev(0, 'focus', 'Figma'), ev(10000, 'focus', 'Notion'), ev(20000, 'focus', 'Figma'), ev(25000, 'paste', 'Figma', { payload: { source_app: 'Figma' } })];
  const wf = suggestWorkflows({ manifest: m, events: evs, files: [], summary: null });
  assert.equal(wf.fallback, true);
  assert.equal(wf.workflows.length, 1, 'never several small workflows');
  const d = sessionDigest({ manifest: m, events: evs, files: [] });
  assert.deepEqual(d.apps.map((a) => a.app), ['Figma', 'Notion']);
  assert.deepEqual(d.sequence, ['Figma', 'Notion', 'Figma']);
  assert.equal(d.pastes, 1);
  assert.equal(d.duration_min, 7);

  let sent = null;
  const fetchFn = async (_url, init) => {
    sent = JSON.parse(init.body);
    return { ok: true, json: async () => ({ model: 'stub', usage: { total_tokens: 12 }, choices: [{ message: { content: JSON.stringify({ title: 'Design brief hand-off', steps: ['Open the brief in Figma', 'Note decisions in Notion', 'Back to Figma'], why: 'Same two tools all session.', automation: 0.4 }) } }] }) };
  };
  const out = await refineSessionWorkflow(wf, d, { key: 'k', model: 'stub' }, { fetchFn });
  assert.match(sent.messages[0].content, /ONE workflow/);
  assert.equal(JSON.parse(sent.messages[1].content).pastes, 1);
  assert.equal(out.workflows[0].title, 'Design brief hand-off');
  assert.equal(out.workflows[0].generated, true);
  assert.equal(out.workflows[0].automation, 0.4);
  assert.equal(out.workflows[0].id, 'session');

  // Not a fallback → untouched, no call.
  const full = suggestWorkflows({ manifest, events, files, summary: manifest.summary });
  assert.equal(await refineSessionWorkflow(full, d, { key: 'k' }, { fetchFn: () => { throw new Error('should not call'); } }), full);
  // Bad model output → keep the deterministic one.
  const bad = await refineSessionWorkflow(wf, d, { key: 'k' }, { fetchFn: async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: '{}' } }] }) }) });
  assert.equal(bad.workflows[0].title, 'Session in Figma and Notion');
});
