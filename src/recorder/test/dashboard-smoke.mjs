// Headless smoke render of ui/dashboard.html inside Electron with a stubbed
// window.vista bridge shaped like main.js's IPC returns. Fails on any renderer
// error. Run: npx electron test/dashboard-smoke.mjs
import { app, BrowserWindow } from 'electron';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const day = (n, h) => new Date(Date.now() - n * 86400000 - h * 3600000).toISOString();
const rec = (id, n, extra = {}) => ({
  recording_id: id, name: `Session ${id}`, started_at: day(n, 3), ended_at: day(n, 2),
  active_seconds: 3600, processing: 'done',
  counts: { total: 120, click: 80, copy: 4, paste: 4 },
  apps: [{ app: 'Excel', seconds: 2000 }, { app: 'Outlook', seconds: 1600 }],
  annotations: 1, edits: 1, upload: null, dir: '/x', ...extra,
});
const recordings = [
  rec('a', 0),
  rec('b', 1, { upload: { status: 'uploading', progress: { done: 3, total: 10 } } }),
  rec('c', 1, { upload: { status: 'failed', error: 'boom' } }),
  rec('d', 3, { submitted: { at: day(3, 1) }, upload: { status: 'submitted', submittedAt: day(3, 1) }, review: { source: 'cloud', run: { status: 'running' }, summary: { open: 2 }, workspace: { key: 'running', label: 'Agent explaining', sub: 'The Recording Reviewer is explaining the sections in your workspace.' } } }),
];
const sections = {
  recording_id: 'a', video: null, video_url: null, started_at: day(0, 3), ended_at: day(0, 2), pauses: [],
  submitted: null, upload: null, shots_dir: 'file:///x',
  apps: [
    { start: day(0, 3), end: day(0, 2.6), app: 'Microsoft Excel', title: 'Q3 budget.xlsx' },
    { start: day(0, 2.6), end: day(0, 2.55), app: 'Slack', title: 'general' },
    { start: day(0, 2.55), end: day(0, 2.5), app: 'Microsoft Excel', title: 'Q3 budget.xlsx' },
    { start: day(0, 2.5), end: day(0, 2), app: 'Microsoft Outlook', title: 'Inbox' },
  ],
  files: [
    { id: 'a1b2c3d4e5f6', name: 'Q3 budget.xlsx', ext: '.xlsx', folder: 'Documents', first_opened: day(0, 3), last_closed: day(0, 2.6), seconds: 1440, intervals: [{ start: day(0, 3), end: day(0, 2.6), app: 'Microsoft Excel' }], used_at: [], sources: ['ax'], snapshot: 'files/a1b2c3d4e5f6/Q3_budget.xlsx', snapshot_url: 'file:///x/files/a1b2c3d4e5f6/Q3_budget.xlsx', sha256: 'ab', size_bytes: 120000, edited: true, include: true, review: { status: 'parsed', chars: 2400, note: null, flags: [{ kind: 'payroll', reason: 'mentions pay', line: 3, source: 'scan' }], ai: { summary: 'Quarterly budget by cost centre.', kind: 'spreadsheet', sensitive: [], confidence: 0.9 }, at: day(0, 1.9) } },
    { id: 'ffffffffffff', name: 'statement.csv', ext: '.csv', folder: 'Downloads', first_opened: day(0, 2.2), last_closed: day(0, 2.2), seconds: 0, intervals: [], used_at: [day(0, 2.2)], sources: ['download'], snapshot: null, snapshot_error: 'ENOENT', include: false },
  ],
  sections: [
    { id: 's0', index: 0, name: 'Excel — Ledger', app: 'Excel', start: day(0, 3), end: day(0, 2.5), seconds: 1800, counts: { click: 40 }, annotations: [], review: { status: 'suggested', label: 'Entering invoices', explanation: 'x', confidence: 0.93 }, edited: true, note: 'my note', files: ['Q3 budget.xlsx'] },
    { id: 's1', index: 1, name: 'Outlook', app: 'Outlook', start: day(0, 2.5), end: day(0, 2), seconds: 1800, counts: { click: 40 }, annotations: [], review: { status: 'unsure', label: '', explanation: '', confidence: 0.4, unclear: ['a'] }, flags: [{ id: 'section-s1-password_entry-1', scope: 'section', section_id: 's1', kind: 'password_entry', reason: 'an 11-key entry ending in Enter in "Sign in" looks like a password being typed', severity: 'high', decision: null, at: day(0, 2.4) }], excluded: false },
  ],
  insights: {
    version: 1, generated_at: day(0, 1.9), running: false, approved_at: null, approved_by: null,
    flags: [
      { id: 'section-s1-password_entry-1', scope: 'section', section_id: 's1', kind: 'password_entry', reason: 'an 11-key entry ending in Enter in "Sign in" looks like a password being typed', severity: 'high', decision: null, at: day(0, 2.4) },
      { id: 'file-a1b2c3d4e5f6-payroll-1', scope: 'file', file_id: 'a1b2c3d4e5f6', file_name: 'Q3 budget.xlsx', kind: 'payroll', reason: 'Q3 budget.xlsx: mentions pay', severity: 'medium', decision: 'dismissed', at: null },
    ],
    summary_counts: { flags: 2, open_flags: 1, confirmed: 0, high: 1, approved: false },
    input: { active_seconds: 3600, totals: { click: 80, key: 900, shortcut: 12, copy: 4, paste: 4, scroll: 9, focus: 6 }, clicks_per_min: 1.3, keys_per_min: 15, typing_bursts: 40, longest_burst_keys: 60, mouse_to_key_ratio: 0.09, rekeyed_between_apps: 3, idle_seconds: 120, idle_gaps: 1, longest_idle_seconds: 120, top_shortcuts: [{ combo: 'Ctrl+C', n: 6 }, { combo: 'Ctrl+V', n: 6 }], per_app: [{ app: 'Microsoft Excel', short: 'Excel', clicks: 60, keys: 800, shortcuts: 12, copies: 4, pastes: 0, scrolls: 5, minutes: 30, clicks_per_min: 2, keys_per_min: 26.7 }, { app: 'Microsoft Outlook', short: 'Outlook', clicks: 20, keys: 100, shortcuts: 0, copies: 0, pastes: 4, scrolls: 4, minutes: 30, clicks_per_min: 0.7, keys_per_min: 3.3 }] },
    trends: { baseline: 3, metrics: [{ key: 'clicks_per_min', label: 'Clicks per minute', now: 1.3, baseline: 2.1, delta_pct: -38 }, { key: 'keys_per_min', label: 'Keystrokes per minute', now: 15, baseline: 12, delta_pct: 25 }] },
    summary: { text: 'A typing-heavy session in Excel with some re-keying from Outlook.', highlights: ['Re-keyed 3 times'], model: 'm', usage: null, at: day(0, 1.9) },
  },
  review: { enabled: true, generating: false, generated_at: null, model: 'm', error: null, sync_error: null, session: null, summary: { open: 1, unsure: 1, approved: 0, total: 2 }, source: 'cloud', run: { status: 'succeeded', finished_at: day(0, 1) }, workspace: { key: 'succeeded', label: 'Reviewed by agent', sub: 'Recording Reviewer finished · 1 section still open.' } },
  workspace_url: 'https://w/account/?view=runs&recording=11111111-1111-1111-1111-111111111111',
};
const stub = `window.vista = {
  info: async () => ({ demo: true, admin: ${JSON.stringify(process.env.VISTA_ADMIN === '1')}, home: '/x', platform: 'linux', user: 'me', openai: true, ai: { enabled: true }, cloud: true, ownApps: ['Vista','Electron'] }),
  status: async () => ({ state: 'idle', elapsedMs: 0, counts: { total: 0 }, apps: [] }),
  recordings: async () => ${JSON.stringify(recordings)},
  sections: async () => (${JSON.stringify(sections)}),
  permissions: async () => ({ ok: true, items: [] }),
  getSettings: async () => ({ ownApps: ['Vista','Electron'], privateApps: [], privateTitles: [] }),
  cloudStatus: async () => ({ connected: true, url: 'https://w', companyId: 'c', email: 'e', companyName: 'Co', uploads: {} }),
  submit: async () => (${JSON.stringify(sections)}), editSection: async () => (${JSON.stringify(sections)}), toggleFile: async () => (${JSON.stringify(sections)}), openFile: async () => {},
  decideFlag: async () => (${JSON.stringify(sections)}), excludeSection: async () => (${JSON.stringify(sections)}), approveInsights: async () => (${JSON.stringify(sections)}), rerunAgents: async () => (${JSON.stringify(sections)}),
  onStatus() {}, onRecordings() {}, onSections() {}, onReview() {}, onPermissions() {},
  start() {}, stop() {}, pause() {}, resume() {}, openRecording() {}, openWorkspace: async () => {}, annotate() {}, decide() {}, explain() {}, setSettings() {}, openPermission() {}, cloudConnect() {}, cloudDisconnect() {},
};`;

app.whenReady().then(async () => {
  const errors = [];
  const preload = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'vista-smoke-')), 'preload.cjs');
  fs.writeFileSync(preload, stub);
  const w = new BrowserWindow({ show: false, width: 1200, height: 900, webPreferences: { preload, contextIsolation: false, sandbox: false } });
  w.webContents.on('console-message', (e) => { if (e.level === 'error') errors.push(e.message); });
  w.webContents.on('preload-error', (_e, _p, err) => errors.push(String(err)));
  setTimeout(() => { console.log('timeout', JSON.stringify(errors)); app.exit(1); }, 30000);
  w.webContents.on('did-finish-load', async () => {
    await new Promise((r) => setTimeout(r, 800));
    const out = await w.webContents.executeJavaScript(`(async () => {
      const t = (s) => document.body.innerText.includes(s);
      document.querySelector('[data-view="recordings"]').click();
      await new Promise(r => setTimeout(r, 50));
      const days = document.querySelectorAll('tr.day').length;
      const list = { uploading: t('Uploading 3/10'), failed: t('Failed'), submitted: t('Submitted'), electron: t('Electron'), other: t('Other ('), wsPill: !!document.querySelector('#rec-rows .pill.ws-running') };
      // open review of recording a, expand edit form for section 0
      const rv = document.querySelector('[data-review="a"]'); rv && rv.click();
      await new Promise(r => setTimeout(r, 200));
      const ed = document.querySelector('[data-edit="0"]'); ed && ed.click();
      await new Promise(r => setTimeout(r, 50));
      return {
        days, hasWeek: !!document.querySelector('#week .d'), kpis: document.getElementById('k-hours').textContent,
        adminHidden: document.getElementById('admin-settings').classList.contains('hidden'),
        submitBtn: !!document.getElementById('rv-submit') && !document.getElementById('rv-submit').classList.contains('hidden'),
        attn: document.getElementById('rv-note')?.className, editForm: !!document.querySelector('[data-form="0"]'),
        appRows: document.querySelectorAll('#docs .row.app').length, appBars: document.querySelectorAll('#docs .row.app .tr i').length, appFirst: document.querySelector('#docs .row.app .lb')?.textContent,
        docRows: document.querySelectorAll('#docs .row:not(.app)').length, docBars: document.querySelectorAll('#docs .row:not(.app) .tr i').length, docTicks: document.querySelectorAll('#docs .tr b').length,
        secsScroll: getComputedStyle(document.getElementById('secs')).overflowY, secCards: document.querySelectorAll('#secs .sec[data-sec]').length,
        fileRows: document.querySelectorAll('#files .file').length, fileOff: document.querySelectorAll('#files .file.off').length, filesSub: document.getElementById('files-sub').textContent,
        docNow: document.getElementById('doc-now').textContent.trim(), secFiles: t('Q3 budget.xlsx'),
        ...list, whatRecorded: t('What is recorded'), edited: t('edited by you'),
        wsBanner: !document.getElementById('rv-ws').classList.contains('hidden') && !!document.querySelector('#rv-ws .pill.ws-succeeded') && !!document.getElementById('rv-open-ws'),
        flagRows: document.querySelectorAll('#flags .flag').length, flagOpen: document.querySelectorAll('#flags .flag:not(.dismissed)').length,
        flagBtns: !!document.querySelector('#flags [data-flag][data-decision="confirmed"]'), timelineFlag: !!document.querySelector('.strip i.flagged'),
        secChip: !!document.querySelector('#secs .fchip'), excludeBtn: !!document.querySelector('#flags [data-excl]'),
        inputBody: t('clicks / min'), trendRow: t('Clicks per minute'), appTable: !!document.querySelector('#input-body .apps-tbl'), aiSummary: t('typing-heavy session'),
        approveBtn: !!document.querySelector('#input-act [data-approve-ins="1"]') && !!document.querySelector('#input-act [data-rerun]'), fileAi: t('Quarterly budget by cost centre'),
      };
    })()`);
    console.log(JSON.stringify({ errors, ...out }, null, 1));
    const docsOk = out.docRows === 2 && out.docBars === 1 && out.docTicks === 1 && out.fileRows === 2 && out.fileOff === 1 && out.filesSub.startsWith('1 of 2') && out.secFiles;
    const appsOk = out.appRows === 3 && out.appBars === 4 && out.appFirst === 'Outlook' && out.secsScroll === 'auto' && out.secCards === 2;
    const insightsOk = out.flagRows === 2 && out.flagOpen === 1 && out.flagBtns && out.timelineFlag && out.secChip && out.excludeBtn && out.inputBody && out.trendRow && out.appTable && out.aiSummary && out.approveBtn && out.fileAi;
    app.exit(errors.length || !insightsOk || !out.wsPill || !out.wsBanner || !out.hasWeek || out.days < 3 || !out.adminHidden || !out.editForm || out.electron || out.other || out.whatRecorded || !docsOk || !appsOk ? 1 : 0);
  });
  w.loadFile(path.join(here, '..', 'ui', 'dashboard.html'));
});
