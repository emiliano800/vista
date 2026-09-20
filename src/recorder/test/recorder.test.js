import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import { DEFAULT_SETTINGS, Recorder, clipHash, frameDiff } from '../src/recorder.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const thumb = (fill, w = 4, h = 2) => ({ width: w, height: h, gray: new Uint8Array(w * h).fill(fill) });

function makeRecorder(overrides = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vista-rec-'));
  let win = { owner: { name: 'Adobe Acrobat' }, title: 'INV-1.pdf' };
  let clip = '';
  const frames = [];
  const rec = new Recorder({
    root,
    settings: { ...DEFAULT_SETTINGS, video: false, changePollMs: 20, changeMinGapMs: 0, frameEverySec: 3600 },
    hook: null,
    activeWindow: async () => win,
    readClipboard: () => clip,
    frameProvider: async () => {
      frames.push(1);
      return Buffer.from('jpg');
    },
    ...overrides,
  });
  return {
    rec,
    root,
    frames,
    setWindow: (app, title) => (win = { owner: { name: app }, title }),
    setClip: (t) => (clip = t),
    events: () => fs.readFileSync(path.join(root, rec.recordingId, 'events.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l)),
  };
}

test('frameDiff: identical, changed, mismatched', () => {
  assert.equal(frameDiff(thumb(10), thumb(10)), 0);
  assert.equal(frameDiff(thumb(10), thumb(30)), 0); // below the 24-level noise floor
  assert.equal(frameDiff(thumb(0), thumb(255)), 1);
  const half = thumb(0);
  half.gray.fill(200, 0, 4);
  assert.equal(frameDiff(thumb(0), half), 0.5);
  assert.equal(frameDiff(thumb(0), thumb(0, 2, 2)), 1);
  assert.equal(frameDiff(null, thumb(0)), 1);
});

test('clipHash: deterministic, salted, short, never the text', () => {
  assert.equal(clipHash('INV-48213', 'a'), clipHash('INV-48213', 'a'));
  assert.notEqual(clipHash('INV-48213', 'a'), clipHash('INV-48213', 'b'));
  assert.equal(clipHash('x').length, 12);
  assert.ok(!clipHash('INV-48213').includes('INV'));
});

test('paste is linked to the copy it came from, cross-app transfers are counted', async () => {
  const t = makeRecorder({ thumbProvider: null });
  t.rec.start();
  await sleep(10);
  t.setClip('1,284.50');
  t.rec._clip('copy', 'Ctrl+C');
  await sleep(80);
  t.setWindow('QuickBooks', 'Enter Bills');
  await t.rec._pollWindow();
  t.rec._clip('paste', 'Ctrl+V');
  await sleep(80);
  t.setClip('something else');
  t.rec._clip('paste', 'Ctrl+V');
  await sleep(80);
  const status = t.rec.status();
  await t.rec.stop();

  const ev = t.events();
  const copy = ev.find((e) => e.event_type === 'copy');
  const [linked, unlinked] = ev.filter((e) => e.event_type === 'paste');
  assert.equal(copy.payload.clip_hash.length, 12);
  assert.equal(copy.payload.chars, 8);
  assert.equal(linked.payload.clip_hash, copy.payload.clip_hash);
  assert.equal(linked.payload.source_app, 'Adobe Acrobat');
  assert.equal(linked.payload.source_title, 'INV-1.pdf');
  assert.equal(linked.payload.cross_app, true);
  assert.ok(linked.payload.transfer_ms >= 0);
  assert.equal(unlinked.payload.source_app, undefined);
  assert.equal(status.counts.transfer, 1);
  assert.equal(status.counts.paste, 2);
});

test('clipboard text is not persisted when the setting is off, hash still links', async () => {
  const t = makeRecorder({ thumbProvider: null });
  t.rec.settings = { ...t.rec.settings, clipboard: false };
  t.rec.start();
  t.setClip('secret-ish value');
  t.rec._clip('copy', 'Ctrl+C');
  await sleep(80);
  t.rec._clip('paste', 'Ctrl+V');
  await sleep(80);
  await t.rec.stop();
  const ev = t.events().filter((e) => e.event_type === 'copy' || e.event_type === 'paste');
  assert.equal(ev.length, 2);
  for (const e of ev) assert.equal(e.text, '');
  assert.equal(ev[1].payload.source_app, 'Adobe Acrobat');
});

test('session intent is kept in the manifest through Stop and ignored when idle', async () => {
  const t = makeRecorder({ thumbProvider: null });
  t.rec.setIntent('nothing yet');
  t.rec.start();
  const manifestPath = path.join(t.root, t.rec.recordingId, 'manifest.json');
  assert.equal(JSON.parse(fs.readFileSync(manifestPath, 'utf8')).summary_text, undefined);
  t.rec.setIntent('  Month-end close — AP reconciliation  ');
  assert.equal(JSON.parse(fs.readFileSync(manifestPath, 'utf8')).summary_text, 'Month-end close — AP reconciliation');
  await sleep(10);
  await t.rec.stop();
  const final = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  assert.equal(final.summary_text, 'Month-end close — AP reconciliation');
  assert.ok(final.ended_at);
  t.rec.start();
  assert.equal(JSON.parse(fs.readFileSync(path.join(t.root, t.rec.recordingId, 'manifest.json'), 'utf8')).summary_text, undefined);
  await t.rec.stop();
});

test('screenshot fires when the thumbnail changes, not when it is stable', async () => {
  let cur = thumb(10);
  const t = makeRecorder({ thumbProvider: async () => cur });
  t.rec.start();
  await sleep(10);
  const afterFocus = t.frames.length; // focus screenshot on start
  await sleep(120); // several stable polls
  assert.equal(t.frames.length, afterFocus);
  cur = thumb(200);
  await sleep(120);
  assert.equal(t.frames.length, afterFocus + 1);
  await t.rec.stop();
  const shots = t.events().filter((e) => e.event_type === 'screen');
  const change = shots.find((s) => s.payload.reason === 'change');
  assert.ok(change);
  assert.equal(change.payload.diff, 1);
  assert.ok(shots.some((s) => s.payload.reason === 'focus'));
});

test('change-triggered screenshots respect the minimum gap', async () => {
  let cur = thumb(10);
  const t = makeRecorder({ thumbProvider: async () => cur });
  t.rec.settings = { ...t.rec.settings, changeMinGapMs: 10_000 };
  t.rec.start();
  await sleep(10);
  const afterFocus = t.frames.length;
  cur = thumb(200);
  await sleep(120);
  assert.equal(t.frames.length, afterFocus); // focus shot was < 10 s ago
  await t.rec.stop();
});
