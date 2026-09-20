import assert from 'node:assert/strict';
import test from 'node:test';

import { isSensitiveWindow, maskSecrets, typedEntries, typedFlags, typingSummary } from '../src/keylog.js';

const T0 = Date.parse('2026-01-06T09:00:00.000Z');
let n = 0;
const key = (ch, { app = 'Excel', title = 'AP tracker.xlsx', url = '', named = null, gap = 200 } = {}) => ({
  timestamp: new Date(T0 + (n += gap)).toISOString(),
  event_type: 'key',
  app,
  window_title: title,
  url,
  text: named ? '' : ch,
  payload: named ? { key: named } : {},
});
// `lead` is the pause before the line starts; the characters themselves are fast.
const typeLine = (s, { lead = 200, ...opts } = {}) =>
  [...s].map((c, i) => (c === ' ' ? key('', { ...opts, named: 'Space', gap: i ? 200 : lead }) : key(c, { ...opts, gap: i ? 200 : lead })));

test('typed characters are stitched back into the line that was typed', () => {
  n = 0;
  const entries = typedEntries(typeLine('inv 4471', {}));
  assert.equal(entries.length, 1);
  assert.equal(entries[0].text, 'inv 4471');
  assert.equal(entries[0].app, 'Excel');
  assert.equal(entries[0].masked, false);
});

test('backspace deletes, Enter ends the entry', () => {
  n = 0;
  const events = [...typeLine('abx'), key('', { named: 'Backspace' }), key('c'), key('', { named: 'Enter' }), ...typeLine('next', { gap: 100 })];
  const entries = typedEntries(events);
  assert.equal(entries.length, 2);
  assert.equal(entries[0].text, 'abc');
  assert.equal(entries[1].text, 'next');
});

test('a quiet gap starts a new entry, a different window too', () => {
  n = 0;
  const events = [...typeLine('one'), ...typeLine('two', { lead: 5000 }), ...typeLine('three', { app: 'Chrome', title: 'Odoo' })];
  assert.deepEqual(typedEntries(events).map((e) => e.text), ['one', 'two', 'three']);
});

test('sign-in and private windows never yield text', () => {
  n = 0;
  const events = [
    ...typeLine('hunter2', { app: 'Chrome', title: 'Sign in — Odoo' }),
    ...typeLine('secret', { app: '1Password', title: '(private)' }),
    ...typeLine('https://erp/checkout', { app: 'Chrome', title: 'Cart', url: 'https://shop.example/checkout' }),
  ];
  const entries = typedEntries(events);
  assert.equal(entries.length, 3);
  assert.ok(entries.every((e) => e.masked && e.text === ''));
  assert.equal(typingSummary(events).enabled, false);
});

test('account and identity numbers are masked even with on-device redaction off', () => {
  assert.equal(maskSecrets('pay 4111 1111 1111 1111 now').text, 'pay [CARD] now');
  assert.deepEqual(maskSecrets('ann@acme.com').kinds, ['email']);
  n = 0;
  const events = typeLine('4111111111111111');
  assert.equal(typedEntries(events)[0].text, '[CARD]');
  assert.deepEqual(typedFlags(events).map((f) => f.kind), ['card_number']);
});

test('summary counts characters and caps the lines it shows', () => {
  n = 0;
  const events = Array.from({ length: 25 }, (_, i) => typeLine(`line${i}`, { lead: i === 0 ? 200 : 5000 })).flat();
  const s = typingSummary(events, { limit: 5 });
  assert.equal(s.entries, 25);
  assert.equal(s.captured, 25);
  assert.equal(s.lines.length, 5);
  assert.ok(s.chars > 0);
});

test('with the setting off there is no text and nothing is reported', () => {
  n = 0;
  const events = typeLine('abc').map((e) => ({ ...e, text: '' }));
  assert.deepEqual(typedEntries(events).map((e) => e.text), ['']);
  assert.equal(typingSummary(events).enabled, false);
});

test('isSensitiveWindow covers titles, urls and the private placeholder', () => {
  assert.ok(isSensitiveWindow({ title: 'Payroll — Workday' }));
  assert.ok(isSensitiveWindow({ title: 'Cart', url: 'https://x/login' }));
  assert.ok(isSensitiveWindow({ title: '(private)' }));
  assert.equal(isSensitiveWindow({ title: 'AP tracker.xlsx' }), false);
});
