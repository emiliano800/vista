import test from 'node:test';
import assert from 'node:assert/strict';

import { buildInsights, inputAnalysis, insightsSummary, passwordCandidates, sectionFlags, summarizeInsights, trends, typingBursts } from '../src/insights.js';

const T0 = Date.parse('2026-03-02T09:00:00Z');
const at = (ms) => new Date(T0 + ms).toISOString();
const ev = (ms, event_type, extra = {}) => ({ timestamp: at(ms), event_type, app: 'Google Chrome', window_title: 'Inbox - Outlook', url: '', text: '', payload: {}, ...extra });
const keys = (start, n, gap = 100, extra = {}) => Array.from({ length: n }, (_, i) => ev(start + i * gap, 'key', extra));

test('typingBursts groups keys by gap; Enter closes a burst', () => {
  const events = [...keys(0, 5), ...keys(10000, 3), ev(10300, 'key', { payload: { key: 'Enter' } }), ...keys(10400, 4)];
  const b = typingBursts(events);
  assert.equal(b.length, 3);
  assert.equal(b[0].keys, 5);
  assert.equal(b[0].enter, false);
  assert.equal(b[1].keys, 4);
  assert.equal(b[1].enter, true);
  assert.equal(b[2].keys, 4);
});

test('passwordCandidates: short burst ending in Enter on a sign-in page, never the typed text', () => {
  const login = { window_title: 'Sign in - Vendor portal', url: 'https://portal.example.com/login' };
  const burst = [...keys(1000, 9, 80, login), ev(1000 + 9 * 80, 'key', { ...login, payload: { key: 'Enter' } })];
  const c = passwordCandidates([ev(0, 'focus', login), ...burst]);
  assert.equal(c.length, 1);
  assert.equal(c[0].keys, 10);
  assert.equal(Object.keys(c[0]).sort().join(','), 'app,at,keys,title');
  // same burst in a plain document: not a candidate
  assert.equal(passwordCandidates([...keys(1000, 9), ev(1800, 'key', { payload: { key: 'Enter' } })]).length, 0);
  // too long to be a password
  assert.equal(passwordCandidates([ev(0, 'focus', login), ...keys(1000, 60, 50, login), ev(4000, 'key', { ...login, payload: { key: 'Enter' } })]).length, 0);
  // right after focusing a private window counts too
  const priv = passwordCandidates([ev(0, 'focus', { window_title: '(private)' }), ...keys(2000, 7, 80, { window_title: 'x' }), ev(2600, 'key', { window_title: 'x', payload: { key: 'Enter' } })]);
  assert.equal(priv.length, 1);
});

test('sectionFlags: private windows, sensitive pages, redaction tokens, clipboard numbers, password entry', () => {
  const section = { id: 'S1', start: at(0), end: at(60000), title: 'Inbox' };
  const events = [
    ev(1000, 'focus', { window_title: '(private)' }),
    ev(5000, 'focus', { window_title: 'Checkout - Shop', url: 'https://shop/checkout' }),
    ev(6000, 'copy', { text: 'card 4111 1111 1111 1111' }),
    ev(7000, 'paste', { text: 'IBAN [IBAN] sent' }),
    ...keys(8000, 8, 80, { window_title: 'Checkout - Shop' }),
    ev(8700, 'key', { window_title: 'Checkout - Shop', payload: { key: 'Enter' } }),
    ev(70000, 'copy', { text: '4111 1111 1111 1111' }), // outside the section
  ];
  const flags = sectionFlags(section, events);
  const kinds = flags.map((f) => f.kind).sort();
  assert.deepEqual(kinds, ['card_number', 'password_entry', 'private_window', 'redacted_data', 'sensitive_page']);
  assert.ok(flags.every((f) => f.section_id === 'S1' && f.scope === 'section' && f.decision === null));
  assert.equal(new Set(flags.map((f) => f.id)).size, flags.length, 'ids unique');
  assert.equal(flags.find((f) => f.kind === 'card_number').severity, 'high');
  assert.equal(flags.find((f) => f.kind === 'card_number').reason.includes('4111'), false, 'never repeats the number');
  assert.equal(sectionFlags({ id: 'S2', start: at(100000), end: at(200000) }, events).length, 0);
});

test('inputAnalysis: rates, bursts, transfers, idle gaps, per app', () => {
  const manifest = { started_at: at(0), ended_at: at(600000), active_seconds: 600 };
  const events = [
    ...Array.from({ length: 30 }, (_, i) => ev(i * 1000, 'click', { app: 'Excel' })),
    ...keys(40000, 20, 100, { app: 'Excel' }),
    ev(50000, 'shortcut', { app: 'Excel', text: 'Cmd+C' }),
    ev(50100, 'copy', { app: 'Excel' }),
    ev(52000, 'paste', { app: 'QuickBooks', payload: { source_app: 'Excel' } }),
    ev(53000, 'paste', { app: 'QuickBooks', payload: { source_app: 'QuickBooks' } }),
    ev(200000, 'click', { app: 'QuickBooks' }), // 147 s gap → idle
  ];
  const a = inputAnalysis(events, manifest);
  assert.equal(a.active_seconds, 600);
  assert.equal(a.totals.click, 31);
  assert.equal(a.clicks_per_min, 3.1);
  assert.equal(a.keys_per_min, 2);
  assert.equal(a.typing_bursts, 1);
  assert.equal(a.longest_burst_keys, 20);
  assert.equal(a.rekeyed_between_apps, 1);
  assert.equal(a.idle_gaps, 1);
  assert.equal(a.longest_idle_seconds, 147);
  assert.deepEqual(a.top_shortcuts, [{ combo: 'Cmd+C', n: 1 }]);
  assert.equal(a.per_app[0].app, 'Excel');
  assert.equal(a.per_app[0].clicks, 30);
  assert.equal(a.per_app[0].keys, 20);
});

test('trends compare against previous manifests with counts', () => {
  const cur = { clicks_per_min: 6, keys_per_min: 10, active_seconds: 600, totals: { copy: 5, paste: 5, shortcut: 10 } };
  assert.deepEqual(trends(cur, []), { baseline: 0, metrics: [] });
  const prev = [
    { counts: { click: 300, key: 600, copy: 10, paste: 10, shortcut: 5 }, active_seconds: 600 },
    { counts: { click: 300, key: 600, copy: 10, paste: 10, shortcut: 5 }, active_seconds: 600 },
    { active_seconds: 100 }, // no counts → ignored
  ];
  const t = trends(cur, prev);
  assert.equal(t.baseline, 2);
  assert.equal(trends({ ...cur, active_seconds: 30, totals: { copy: 1, paste: 0, shortcut: 0 } }, [{ counts: { click: 1 }, active_seconds: 12 }]).metrics[0].baseline, 5, 'short sessions are still rates');
  const by = Object.fromEntries(t.metrics.map((m) => [m.key, m]));
  assert.equal(by.clicks_per_min.baseline, 30);
  assert.equal(by.clicks_per_min.delta_pct, -80);
  assert.equal(by.copy_paste_per_min.now, 1);
  assert.equal(by.copy_paste_per_min.delta_pct, -50);
  assert.equal(by.active_minutes.delta_pct, 0);
});

test('buildInsights keeps flag decisions and approval across re-runs; summary counts', () => {
  const manifest = { started_at: at(0), ended_at: at(60000), active_seconds: 60 };
  const events = [ev(1000, 'focus', { window_title: '(private)' }), ev(2000, 'click')];
  const sections = [{ id: 'S1', start: at(0), end: at(60000) }];
  const files = [{ id: 'f1', name: 'pay.csv', include: true, review: { flags: [{ kind: 'payroll', reason: 'mentions pay', line: 1, source: 'scan' }] } }, { id: 'f2', name: 'x.csv', include: false, review: { flags: [{ kind: 'iban', reason: 'iban' }] } }];
  const first = buildInsights({ manifest, events, sections, files });
  assert.deepEqual(first.flags.map((f) => f.kind).sort(), ['payroll', 'private_window']);
  assert.equal(first.flags.find((f) => f.scope === 'file').file_id, 'f1');
  assert.deepEqual(insightsSummary(first), { flags: 2, open_flags: 2, confirmed: 0, high: 0, approved: false });
  first.flags[0].decision = 'confirmed';
  first.flags[0].decided_at = at(5000);
  first.approved_at = at(6000);
  first.approved_by = 'jane';
  first.summary = { text: 'ok' };
  const second = buildInsights({ manifest, events, sections, files }, first);
  assert.equal(second.flags[0].id, first.flags[0].id);
  assert.equal(second.flags[0].decision, 'confirmed');
  assert.equal(second.flags[0].decided_at, at(5000));
  assert.equal(second.flags[1].decision, null);
  assert.equal(second.approved_at, at(6000));
  assert.deepEqual(second.summary, { text: 'ok' });
  assert.deepEqual(insightsSummary(second), { flags: 2, open_flags: 1, confirmed: 1, high: 0, approved: true });
  assert.equal(second.trends.baseline, 0);
});

test('summarizeInsights sends only kinds/reasons of flags and parses the reply', async () => {
  let sent;
  const fetchFn = async (_u, init) => { sent = JSON.parse(init.body); return { ok: true, json: async () => ({ model: 'm', usage: { total_tokens: 5 }, choices: [{ message: { content: '{"summary":"Mostly clicking.","highlights":["a"]}' } }] }) }; };
  const out = await summarizeInsights({ input: { clicks_per_min: 3 }, trends: { baseline: 0, metrics: [] }, flags: [{ id: 'x', kind: 'private_window', scope: 'section', reason: 'r', section_id: 'S1', at: 't' }] }, { key: 'k', model: 'm' }, { fetchFn });
  assert.equal(out.text, 'Mostly clicking.');
  assert.deepEqual(out.highlights, ['a']);
  assert.deepEqual(JSON.parse(sent.messages[1].content).flags, [{ kind: 'private_window', scope: 'section', reason: 'r' }]);
  assert.equal('trends' in JSON.parse(sent.messages[1].content), false, 'no baseline → no trends sent to the model');
});
