import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { ComputerUseClient } from '../src/computer-use/client.js';
import { UnsupportedHarness, capabilitiesOf, defaultHarnesses } from '../src/computer-use/harnesses.js';

const RUN = '11111111-1111-4111-8111-111111111111';
const SESSION = '22222222-2222-4222-8222-222222222222';
const STEP = '33333333-3333-4333-8333-333333333333';
const config = () => ({ url: 'https://w.example', token: 'pak' });

// A supported fake harness: performs whatever passes the policy and reports an observation.
class FakeBrowser {
  constructor() {
    this.kind = 'browser';
    this.supported = true;
    this.performed = [];
    this.closed = 0;
  }
  capabilities() {
    return ['observe', 'click'];
  }
  async perform(step) {
    this.performed.push(step);
    return { ok: true, description: `did ${step.action}`, observation: { observation_id: `obs-${this.performed.length}`, candidates: [{ id: 5, role: 'button', name: 'Save' }] }, result: {}, evidence: step.action === 'screenshot' ? { sha256: 'x', screenshot_base64: 'AAAA' } : null };
  }
  async close() {
    this.closed += 1;
  }
}

// An in-memory stand-in for the workspace's recorder routes.
function server() {
  const calls = [];
  const state = { status: 'active', pending: null, offers: [{ id: null, run_id: RUN, status: 'offered', workflow: { name: 'Enter bills', version: 2, goal: 'g' }, harness_kinds: ['browser'], limits: { max_steps: 5, max_runtime_seconds: 600 }, requested_by: { email: 'o@x' } }], active: null, results: [] };
  const view = () => ({ id: SESSION, run_id: RUN, workflow: state.offers[0]?.workflow ?? { name: 'Enter bills' }, status: state.status, harness_kinds: ['browser'], limits: { max_steps: 5, max_runtime_seconds: 600 }, remaining: { steps: 5 }, pause_reason: state.pause_reason ?? null, lease: { device_id: 'dev', expires_at: 'x' }, pending_step: state.pending });
  const fetchImpl = async (url, init) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push([init.method, u.pathname + u.search, body]);
    const json = (status, data) => ({ ok: status < 400, status, json: async () => data });
    assert.equal(init.headers.Authorization, 'Bearer pak');
    if (u.pathname === '/api/recorder/computer-use/sessions') return json(200, { device: { id: 'd', device_id: u.searchParams.get('device_id'), capabilities: { browser: u.searchParams.get('browser') === 'true' } }, poll_seconds: 30, active: state.active, offers: state.offers });
    if (u.pathname === `/api/recorder/computer-use/sessions/${RUN}/claim`) {
      assert.equal(body.consent.version, 'computer-use-v1');
      state.offers = [];
      return json(200, { ...view(), lease: { device_id: body.device_id, expires_at: 'x', token: 'tok-1' } });
    }
    if (u.pathname === `/api/recorder/computer-use/sessions/${SESSION}`) {
      if (u.searchParams.get('lease_token') !== 'tok-1') return json(409, { detail: 'lease held by another device' });
      const v = view();
      state.pending = null;
      return json(200, v);
    }
    if (u.pathname === `/api/recorder/computer-use/steps/${STEP}/result`) {
      state.results.push(body);
      return json(200, { ok: true, duplicate: false });
    }
    if (u.pathname === `/api/recorder/computer-use/sessions/${SESSION}/stop`) {
      state.status = 'stopped';
      return json(200, { ok: true, status: 'stopped' });
    }
    return json(404, { detail: 'nope' });
  };
  return { calls, state, fetchImpl };
}

function client(t, over = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vista-cu-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const changes = [];
  const c = new ComputerUseClient({ home, deviceId: 'dev', platform: 'darwin', version: '0.2.1', config, pollMs: 1, sleep: () => new Promise((r) => setImmediate(r)), onChange: (s) => changes.push(s), ...over });
  return { c, home, changes };
}

test('placeholder harnesses advertise nothing and refuse steps with harness_unsupported; demo mode advertises but still refuses', async () => {
  const h = defaultHarnesses();
  assert.deepEqual(capabilitiesOf(h), { browser: false, desktop: false });
  const r = await h.browser.perform({ action: 'click' });
  assert.equal(r.ok, false);
  assert.equal(r.error.code, 'harness_unsupported');
  assert.ok(new UnsupportedHarness('desktop', 'no').perform);
  const d = defaultHarnesses({ demo: true });
  assert.deepEqual(capabilitiesOf(d), { browser: true, desktop: true });
  const rd = await d.browser.perform({ action: 'click' });
  assert.equal(rd.ok, false);
  assert.equal(rd.error.code, 'harness_unsupported');
  assert.match(rd.error.message, /demo mode/);
});

test('the idle tick announces the device with its real capabilities and lists offers; no workspace means no offers', async (t) => {
  const s = server();
  const { c } = client(t, { fetchImpl: s.fetchImpl });
  const st = await c.tick();
  assert.equal(st.offers.length, 1);
  assert.equal(st.device.device_id, 'dev');
  assert.match(s.calls[0][1], /device_id=dev&platform=darwin&browser=false&desktop=false&recorder_version=0\.2\.1/);
  const off = new ComputerUseClient({ home: os.tmpdir(), deviceId: 'dev', config: () => null, fetchImpl: () => assert.fail('must not call') });
  assert.deepEqual((await off.tick()).offers, []);
  assert.equal((await off.tick()).enabled, false);
});

test('a step whose result could not be posted is re-posted when offered again, never performed twice', async (t) => {
  const s = server();
  let failPosts = 1;
  const flaky = async (url, init) => {
    if (failPosts > 0 && url.includes(`/steps/${STEP}/result`)) { failPosts -= 1; return { ok: false, status: 502, json: async () => ({ detail: 'proxy hiccup' }) }; }
    return s.fetchImpl(url, init);
  };
  const browser = new FakeBrowser();
  const { c } = client(t, { fetchImpl: flaky, harnesses: { browser, desktop: new UnsupportedHarness('desktop', 'no') } });
  await c.tick();
  s.state.pending = { step_id: STEP, seq: 1, harness: 'browser', action: 'observe' };
  await c.start(RUN, { consent: true, shareScreenshots: false });
  for (let i = 0; i < 200 && browser.performed.length < 1; i++) await new Promise((r) => setImmediate(r));
  assert.equal(browser.performed.length, 1);
  assert.equal(s.state.results.length, 0, 'the first post failed');
  // The server has no result, so it offers the same step again.
  s.state.pending = { step_id: STEP, seq: 1, harness: 'browser', action: 'observe' };
  for (let i = 0; i < 400 && s.state.results.length < 1; i++) await new Promise((r) => setImmediate(r));
  assert.equal(s.state.results.length, 1, 'the saved result went up');
  assert.equal(browser.performed.length, 1, 'the step was not performed a second time');
  assert.equal(s.state.results[0].description, 'did observe');
  await c.stop('done');
});

test('start refuses without consent === true, when the offer needs a harness this computer lacks, and when busy', async (t) => {
  const s = server();
  const { c } = client(t, { fetchImpl: s.fetchImpl });
  await c.tick();
  await assert.rejects(c.start(RUN, {}), /consent/);
  await assert.rejects(c.start(RUN, { consent: 'yes' }), /consent/);
  await assert.rejects(c.start(RUN, { consent: true }), /cannot provide the browser harness/);
  assert.equal(s.calls.filter((x) => x[1].includes('/claim')).length, 0, 'no claim was sent');
});

test('start claims with consent, the loop performs a pending step after the policy check and posts the result; Stop closes the session', async (t) => {
  const s = server();
  const browser = new FakeBrowser();
  const { c, home, changes } = client(t, { fetchImpl: s.fetchImpl, harnesses: { browser, desktop: new UnsupportedHarness('desktop', 'no') } });
  await c.tick();
  s.state.pending = { step_id: STEP, seq: 1, harness: 'browser', action: 'observe' };
  const summary = await c.start(RUN, { consent: true, shareScreenshots: false });
  assert.equal(summary.id, SESSION);
  assert.equal(summary.screenshots_shared, false);
  const claim = s.calls.find((x) => x[1].endsWith('/claim'))[2];
  assert.deepEqual(claim.capabilities, { browser: true, desktop: false, recorder_version: '0.2.1' });
  assert.equal(claim.consent.screenshots, false);
  // wait for the step to be performed and posted
  for (let i = 0; i < 200 && s.state.results.length < 1; i++) await new Promise((r) => setImmediate(r));
  assert.equal(browser.performed.length, 1);
  const posted = s.state.results[0];
  assert.equal(posted.lease_token, 'tok-1');
  assert.equal(posted.ok, true);
  assert.equal(posted.observation.observation_id, 'obs-1');
  // a targeted step citing the current observation runs; one citing a stale observation is refused locally, never performed
  s.state.pending = { step_id: STEP, seq: 2, harness: 'browser', action: 'click', target_id: 5, observation_id: 'obs-0' };
  for (let i = 0; i < 200 && s.state.results.length < 2; i++) await new Promise((r) => setImmediate(r));
  assert.equal(browser.performed.length, 1, 'stale step not performed');
  assert.equal(s.state.results[1].ok, false);
  assert.equal(s.state.results[1].error.code, 'stale_observation');
  s.state.pending = { step_id: STEP, seq: 3, harness: 'browser', action: 'click', target_id: 5, observation_id: 'obs-1' };
  for (let i = 0; i < 200 && s.state.results.length < 3; i++) await new Promise((r) => setImmediate(r));
  assert.equal(browser.performed.length, 2);
  // the local log was written before the POST and never carries the lease token
  const log = fs.readFileSync(path.join(home, 'computer-use', SESSION, 'steps.jsonl'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  assert.ok(log.some((l) => l.note), 'session start note');
  assert.equal(log.filter((l) => l.request).length, 3);
  assert.equal(JSON.stringify(log).includes('tok-1'), false);
  assert.equal(fs.statSync(path.join(home, 'computer-use', SESSION, 'steps.jsonl')).mode & 0o777, 0o600);
  assert.ok(c.status().active.log.some((line) => /✓ 1 browser · did observe/.test(line)));
  assert.ok(c.status().active.log.some((line) => /✗ 2 browser/.test(line)));
  // stop
  const r = await c.stop('the employee pressed Stop');
  assert.equal(r.active, null);
  assert.ok(s.calls.some((x) => x[1].endsWith(`/sessions/${SESSION}/stop`) && x[2].reason === 'the employee pressed Stop'));
  assert.equal(browser.closed, 1);
  assert.equal(c.status().active, null);
  assert.equal(JSON.parse(fs.readFileSync(path.join(home, 'computer-use', SESSION, 'session.json'), 'utf8')).status, 'stopped');
  assert.ok(changes.length > 3);
});

test('screenshots stay local unless the employee shared them; the session ends when the run does or the lease is lost', async (t) => {
  const s = server();
  const browser = new FakeBrowser();
  const { c } = client(t, { fetchImpl: s.fetchImpl, harnesses: { browser } });
  await c.tick();
  s.state.pending = { step_id: STEP, seq: 1, harness: 'browser', action: 'screenshot' };
  await c.start(RUN, { consent: true });
  for (let i = 0; i < 200 && s.state.results.length < 1; i++) await new Promise((r) => setImmediate(r));
  assert.equal(s.state.results[0].evidence.screenshot_base64, undefined, 'no consent → no pixels leave');
  assert.equal(s.state.results[0].evidence.sha256, 'x');
  s.state.status = 'succeeded';
  for (let i = 0; i < 200 && c.session; i++) await new Promise((r) => setImmediate(r));
  assert.equal(c.status().active, null);
  assert.equal(c.lastSession.status, 'succeeded');
  assert.equal(browser.closed, 1);

  // with consent the base64 is posted; a lost lease (409) aborts locally without a stop call
  const s2 = server();
  const { c: c2 } = client(t, { fetchImpl: s2.fetchImpl, harnesses: { browser: new FakeBrowser() } });
  await c2.tick();
  s2.state.pending = { step_id: STEP, seq: 1, harness: 'browser', action: 'screenshot' };
  await c2.start(RUN, { consent: true, shareScreenshots: true });
  for (let i = 0; i < 200 && s2.state.results.length < 1; i++) await new Promise((r) => setImmediate(r));
  assert.equal(s2.state.results[0].evidence.screenshot_base64, 'AAAA');
  c2.session.token = 'tok-lost';
  for (let i = 0; i < 200 && c2.session; i++) await new Promise((r) => setImmediate(r));
  assert.equal(c2.status().active, null);
  assert.match(c2.lastSession.reason, /lease was lost/);
  assert.equal(s2.calls.some((x) => x[1].endsWith('/stop')), false);
});

test('a step for a harness this computer does not provide is refused locally and reported, not performed', async (t) => {
  const s = server();
  s.state.offers[0].harness_kinds = [];
  const { c } = client(t, { fetchImpl: s.fetchImpl });
  await c.tick();
  s.state.pending = { step_id: STEP, seq: 1, harness: 'desktop', action: 'open_app', value: 'Calculator' };
  await c.start(RUN, { consent: true });
  for (let i = 0; i < 200 && s.state.results.length < 1; i++) await new Promise((r) => setImmediate(r));
  assert.equal(s.state.results[0].ok, false);
  assert.equal(s.state.results[0].error.code, 'disallowed_harness');
  await c.stop();
});
