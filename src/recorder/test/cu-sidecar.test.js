import assert from 'node:assert/strict';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { SidecarClient, SidecarHarness } from '../src/computer-use/sidecar.js';
import { defaultHarnesses } from '../src/computer-use/harnesses.js';

const FAKE = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures', 'fake-sidecar.mjs');
const client = (mode = 'ok') => new SidecarClient({ command: process.execPath, args: [FAKE], env: { FAKE_SIDECAR_MODE: mode }, healthTimeoutMs: 500 });

test('sidecar: health, self-test gates the harness, only a leakage-clean cloud block leaves', async () => {
  const c = client();
  const health = await c.start();
  assert.equal(health.drivers.browser, true);
  const browser = new SidecarHarness({ kind: 'browser', client: c, settings: () => ({ screenshots: false }) });
  const desktop = new SidecarHarness({ kind: 'desktop', client: c });
  assert.equal((await browser.probe()).ok, true);
  assert.equal((await desktop.probe()).ok, false);
  assert.equal(desktop.selfTest.error.code, 'harness_unsupported');
  assert.deepEqual(desktop.capabilities(), []);
  assert.ok(browser.capabilities().includes('click'));

  const r = await browser.perform({ action: 'click', target: 'x' });
  assert.equal(r.ok, true);
  assert.deepEqual(r.observation.l0, ['in:abc', 'have:field:amount']);
  assert.equal(r.observation.observation_id, 'obs-1');
  assert.equal(JSON.stringify(r).includes('crm.example.com'), false);
  assert.equal('local' in r, false);
  const shot = await browser.perform({ action: 'screenshot' });
  assert.equal(shot.error.code, 'consent_required');

  const h = defaultHarnesses({ sidecar: { browser, desktop } });
  assert.equal(h.browser, browser);
  assert.equal(h.desktop.supported, false); // fell back to UnsupportedHarness
  await c.stop();
  assert.equal(c.alive, false);
});

test('sidecar: a leaking cloud block is dropped from the step result', async () => {
  const c = client('leak');
  await c.start();
  const browser = new SidecarHarness({ kind: 'browser', client: c });
  await browser.probe();
  const r = await browser.perform({ action: 'navigate', url: 'https://x' });
  assert.equal(r.ok, true);
  assert.equal(r.observation, null);
  await c.stop();
});

test('sidecar: timeouts, garbage output and a dead process become refusals, never throws', async () => {
  const silent = client('silent');
  assert.equal(await silent.start(), null); // health timed out → stopped
  assert.equal(silent.alive, false);

  const garbage = client('garbage');
  assert.equal(await garbage.start(), null);

  const c = client();
  await c.start();
  const browser = new SidecarHarness({ kind: 'browser', client: c });
  await browser.probe();
  await c.stop();
  const r = await browser.perform({ action: 'click', target: 'x' });
  assert.equal(r.ok, false);
  assert.equal(r.error.code, 'sidecar_down');
  assert.equal((await browser.probe()).ok, false);
  assert.equal(browser.supported, false);
});
