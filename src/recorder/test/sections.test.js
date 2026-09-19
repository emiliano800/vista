import assert from 'node:assert/strict';
import { test } from 'node:test';

import { OPENAI_URL, askClarifying, describeSection, openaiConfig, parseReply } from '../src/clarify.js';
import { buildSections, focusSpans, parseEvents, pausedBefore, recordingName, sectionName } from '../src/sections.js';

const T0 = Date.parse('2026-03-03T09:00:00.000Z');
const at = (sec) => new Date(T0 + sec * 1000).toISOString();
const focus = (sec, app, title) => ({ timestamp: at(sec), user: 'u', event_type: 'focus', app, window_title: title, url: '', payload: {} });
const ev = (sec, type, app, extra = {}) => ({ timestamp: at(sec), user: 'u', event_type: type, app, window_title: '', url: '', text: '', payload: {}, ...extra });

// Excel 0-600, quick Slack peek 600-604, Excel 604-1200, Outlook 1200-1500, Chrome 1500-1520, Outlook 1520-1800
const EVENTS = [
  focus(0, 'Microsoft Excel', 'AP tracker.xlsx - Excel'),
  ev(30, 'click', 'Microsoft Excel'),
  ev(40, 'copy', 'Microsoft Excel'),
  focus(600, 'Slack', 'general'),
  focus(604, 'Microsoft Excel', 'AP tracker.xlsx - Excel'),
  ev(700, 'click', 'Microsoft Excel'),
  focus(1200, 'Microsoft Outlook', 'Inbox - Outlook'),
  ev(1210, 'paste', 'Microsoft Outlook', { payload: { source_app: 'Microsoft Excel' } }),
  ev(1220, 'shortcut', 'Microsoft Outlook', { text: 'Cmd+Enter' }),
  focus(1500, 'Google Chrome', 'Vendor portal'),
  focus(1520, 'Microsoft Outlook', 'Inbox - Outlook'),
  ev(1600, 'screen', 'Microsoft Outlook', { payload: { image: 'shots/000001.jpg' } }),
];
const MANIFEST = { started_at: at(0), ended_at: at(1800), active_seconds: 1800, apps: [{ app: 'Microsoft Excel', seconds: 1200 }, { app: 'Microsoft Outlook', seconds: 580 }] };

test('parseEvents skips blank and corrupt lines', () => {
  const rows = parseEvents('{"a":1}\n\nnot json\n{"b":2}\n');
  assert.equal(rows.length, 2);
});

test('focusSpans closes each focus with the next one and cuts pauses out', () => {
  const { spans } = focusSpans(EVENTS, { startedAt: at(0), endedAt: at(1800), pauses: [{ start: at(100), end: at(200) }] });
  assert.equal(spans[0].start, T0);
  assert.equal(spans[0].end, T0 + 100_000);
  assert.equal(spans[1].start, T0 + 200_000);
  assert.equal(spans[1].end, T0 + 600_000);
  assert.equal(spans.at(-1).end, T0 + 1800_000);
});

test('video offsets skip paused time (the webm is paused with the hooks)', () => {
  const pauses = [{ start: at(100), end: at(200) }, { start: at(1300), end: at(1400) }];
  assert.equal(pausedBefore(T0 + 50_000, pauses), 0);
  assert.equal(pausedBefore(T0 + 150_000, pauses), 50_000);
  assert.equal(pausedBefore(T0 + 1800_000, pauses), 200_000);
  const secs = buildSections(EVENTS, { ...MANIFEST, pauses }, { minSeconds: 45 });
  const outlook = secs.filter((s) => s.app === 'Microsoft Outlook');
  assert.equal(outlook[0].start, at(1200));
  assert.equal(outlook[0].offset_s, 1100);
  assert.equal(outlook[1].start, at(1400));
  assert.equal(outlook[1].offset_s, 1200);
});

test('buildSections folds quick hops into the surrounding long span', () => {
  const secs = buildSections(EVENTS, MANIFEST);
  assert.deepEqual(secs.map((s) => [s.app, s.seconds]), [
    ['Microsoft Excel', 1200],
    ['Microsoft Outlook', 600],
  ]);
  assert.equal(secs[0].offset_s, 0);
  assert.equal(secs[1].offset_s, 1200);
  assert.equal(secs[1].id, 'S2');
  assert.equal(secs[0].counts.copy, 1);
  assert.equal(secs[1].counts.paste, 1);
  assert.deepEqual(secs[1].shots, ['shots/000001.jpg']);
  assert.deepEqual(secs[1].other_apps, ['Google Chrome']);
});

test('buildSections keeps only the largest N, in chronological order', () => {
  const many = [];
  for (let i = 0; i < 12; i++) many.push(focus(i * 300, `App${i}`, `w${i}`));
  const secs = buildSections(many, { started_at: at(0), ended_at: at(12 * 300 + 3000) }, { maxSections: 3, minSeconds: 10 });
  assert.equal(secs.length, 3);
  assert.equal(secs.at(-1).app, 'App11'); // the last span is 3300 s, the largest
  assert.ok(Date.parse(secs[0].start) < Date.parse(secs[1].start));
});

test('buildSections without focus events returns nothing', () => {
  assert.deepEqual(buildSections([ev(1, 'click', 'X')], MANIFEST), []);
});

test('sectionName strips the app suffix from the title and shows the time range', () => {
  const n = sectionName({ start: at(0), end: at(600), app: 'Microsoft Excel', title: 'AP tracker.xlsx - Excel' });
  assert.match(n, /^\d\d:\d\d–\d\d:\d\d · Excel — AP tracker\.xlsx$/);
});

test('recordingName combines day, time span, top apps and the employee summary', () => {
  const base = recordingName(MANIFEST);
  assert.match(base, /^\w{3} \d\d:\d\d–\d\d:\d\d · Excel, Outlook$/);
  const named = recordingName(MANIFEST, { summary: 'Month-end AP run' });
  assert.equal(named, `${base} — Month-end AP run`);
  assert.ok(recordingName(MANIFEST, { summary: 'x'.repeat(200) }).length < base.length + 90);
});

test('describeSection includes counts, paste flows and prior answers', () => {
  const secs = buildSections(EVENTS, MANIFEST);
  const txt = describeSection(secs[1], { manifest: MANIFEST, sections: secs, events: EVENTS, answers: [{ q: 'Why?', a: 'Because.' }] });
  assert.match(txt, /SECTION: .*Outlook/);
  assert.match(txt, /1 pastes/);
  assert.match(txt, /Microsoft Excel → Microsoft Outlook \(1×\)/);
  assert.match(txt, /Cmd\+Enter ×1/);
  assert.match(txt, /Q: Why\?\n\s+A: Because\./);
  const whole = describeSection({ whole: true }, { manifest: MANIFEST, sections: secs, events: EVENTS });
  assert.match(whole, /WHOLE SESSION/);
  assert.match(whole, /Main stretches of work/);
});

test('askClarifying posts to OpenAI with the key and returns parsed questions', async () => {
  const secs = buildSections(EVENTS, MANIFEST);
  let captured = null;
  const fetchFn = async (url, init) => {
    captured = { url, init };
    return { ok: true, json: async () => ({ model: 'gpt-4o-mini-2024', usage: { total_tokens: 12 }, choices: [{ message: { content: '{"summary":"Sending vendor emails","questions":["Which vendors?","Was this from a list?"]}' } }] }) };
  };
  const r = await askClarifying(secs[1], { manifest: MANIFEST, sections: secs, events: EVENTS }, { key: 'sk-test', model: 'gpt-4o-mini' }, { fetchFn });
  assert.equal(captured.url, OPENAI_URL);
  assert.equal(captured.init.headers.Authorization, 'Bearer sk-test');
  const body = JSON.parse(captured.init.body);
  assert.equal(body.model, 'gpt-4o-mini');
  assert.equal(body.messages[1].content.length, 1); // text only, no screenshots by default
  assert.deepEqual(r.questions, ['Which vendors?', 'Was this from a list?']);
  assert.equal(r.summary, 'Sending vendor emails');
  assert.equal(r.model, 'gpt-4o-mini-2024');

  const bad = async () => ({ ok: false, status: 401, statusText: 'Unauthorized', text: async () => 'bad key' });
  await assert.rejects(askClarifying(secs[1], { manifest: MANIFEST }, { key: 'x', model: 'm' }, { fetchFn: bad }), /OpenAI 401/);
});

test('openaiConfig prefers the environment over settings', () => {
  assert.equal(openaiConfig({}, {}), null);
  assert.deepEqual(openaiConfig({}, { openaiApiKey: 'sk-a', openaiModel: 'm1' }), { key: 'sk-a', model: 'm1', url: OPENAI_URL });
  assert.deepEqual(openaiConfig({ OPENAI_API_KEY: 'sk-env', VISTA_OPENAI_URL: 'http://gw/v1/chat/completions' }, { openaiApiKey: 'sk-a' }), {
    key: 'sk-env',
    model: 'gpt-4o-mini',
    url: 'http://gw/v1/chat/completions',
  });
});

test('parseReply accepts JSON, fenced JSON and plain question lists', () => {
  assert.deepEqual(parseReply('{"summary":"s","questions":["a?","b?"]}'), { summary: 's', questions: ['a?', 'b?'] });
  assert.deepEqual(parseReply('```json\n{"summary":"s","questions":["a?"]}\n```').questions, ['a?']);
  assert.deepEqual(parseReply('1. What triggered this?\n2. Who else was involved?\nThanks.').questions, ['What triggered this?', 'Who else was involved?']);
});
