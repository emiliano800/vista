import test from 'node:test';
import assert from 'node:assert/strict';

import { KEYS, PolicyError, isPrivateWindow, validateStep } from '../src/computer-use/policy.js';

const session = { harness_kinds: ['browser'], limits: { max_steps: 3, max_runtime_seconds: 60 } };
const local = () => ({ capabilities: { browser: true, desktop: false }, stepsDone: 0, startedAt: 1000, lastObservationId: 'obs-1', now: () => 2000 });
const step = (over = {}) => ({ step_id: 's1', seq: 1, harness: 'browser', action: 'observe', ...over });
const refuses = (s, l, code) => {
  let err;
  try {
    validateStep(s, session, l ?? local());
  } catch (e) {
    err = e;
  }
  assert.ok(err instanceof PolicyError, `expected a PolicyError(${code})`);
  assert.equal(err.code, code);
};

test('a step must name a harness the session consented to AND this computer provides', () => {
  assert.equal(validateStep(step(), session, local()), true);
  refuses(step({ harness: 'desktop' }), undefined, 'disallowed_harness');
  refuses(step(), { ...local(), capabilities: { browser: false } }, 'disallowed_harness');
});

test('the session limits are mirrored locally: steps and runtime', () => {
  refuses(step(), { ...local(), stepsDone: 3 }, 'step_limit');
  refuses(step(), { ...local(), now: () => 1000 + 61_000 }, 'runtime_limit');
});

test('only the closed action set, with well-formed values', () => {
  refuses(step({ action: 'run_script' }), undefined, 'invalid_value');
  refuses(step({ action: 'navigate', value: 'file:///etc/passwd' }), undefined, 'invalid_value');
  refuses(step({ action: 'navigate', value: 'javascript:alert(1)' }), undefined, 'invalid_value');
  assert.equal(validateStep(step({ action: 'navigate', value: 'https://example.test/a' }), session, local()), true);
  refuses(step({ action: 'press', value: 'F12' }), undefined, 'invalid_value');
  for (const k of Object.keys(KEYS)) assert.equal(validateStep(step({ action: 'press', value: k }), session, local()), true);
  refuses(step({ action: 'wait', value: 60_000 }), undefined, 'invalid_value');
  refuses(step({ action: 'type', target_id: 1, observation_id: 'obs-1', value: 'x'.repeat(2001) }), undefined, 'invalid_value');
  refuses(step({ action: 'type', target_id: 1, observation_id: 'obs-1', value: '' }), undefined, 'invalid_value');
  refuses(step({ action: 'open_app', value: '../../bin/sh' }), undefined, 'invalid_value');
  refuses(step({ action: 'extract', value: 'html' }), undefined, 'invalid_value');
});

test('a targeted step must cite the observation the target came from, and it must be the current one', () => {
  refuses(step({ action: 'click' }), undefined, 'invalid_value');
  refuses(step({ action: 'click', target_id: 7 }), undefined, 'invalid_value');
  refuses(step({ action: 'click', target_id: 7, observation_id: 'obs-0' }), undefined, 'stale_observation');
  assert.equal(validateStep(step({ action: 'click', target_id: 7, observation_id: 'obs-1' }), session, local()), true);
  assert.equal(validateStep(step({ action: 'extract', value: 'table' }), session, local()), true, 'extract may run on the whole page');
});

test('private apps and sensitive titles are recognised from the recorder settings', () => {
  assert.equal(isPrivateWindow('1Password 8', 'Vault'), true);
  assert.equal(isPrivateWindow('Safari', 'Sign in to your bank'), true);
  assert.equal(isPrivateWindow('Safari', 'Vendor portal'), false);
  assert.equal(isPrivateWindow('Notes', 'Vendor portal', { privateApps: ['Notes'], privateTitles: [] }), true);
  assert.equal(isPrivateWindow('Safari', 'Payroll — Q3', { privateApps: [], privateTitles: ['payroll'] }), true);
});
