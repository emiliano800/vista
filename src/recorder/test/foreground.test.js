import assert from 'node:assert/strict';
import { test } from 'node:test';

import { withPermissionFallback } from '../src/foreground.js';

const WIN = { owner: { name: 'Excel', processId: 42 }, title: 'Q3 invoices.xlsx' };
const permissionError = () =>
  new Error('get-windows requires the screen recording permission in “System Settings › Privacy & Security › Screen Recording”.');

test('a helper that refuses on Screen Recording grounds is asked again without the check', async () => {
  const calls = [];
  const helper = async (options) => {
    calls.push(options);
    if (options?.screenRecordingPermission === false) return { ...WIN, title: '' };
    throw permissionError();
  };
  const warnings = [];
  const probe = withPermissionFallback(helper, { warn: (m) => warnings.push(m) });

  const first = await probe();
  assert.equal(first.owner.name, 'Excel', 'the application name is kept');
  assert.equal(first.title, '', 'only the window title is given up');
  assert.deepEqual(calls, [undefined, { screenRecordingPermission: false }]);
  assert.equal(warnings.length, 1);

  const second = await probe();
  assert.equal(second.owner.name, 'Excel');
  assert.equal(calls.length, 3, 'after one refusal every later probe skips the check without retrying');
  assert.equal(warnings.length, 1, 'warned once');
});

test('a helper that works is left alone, titles included', async () => {
  const probe = withPermissionFallback(async () => WIN);
  assert.deepEqual(await probe(), WIN);
});

test('other failures still surface', async () => {
  const probe = withPermissionFallback(async () => {
    throw new Error('Error parsing window data');
  });
  await assert.rejects(probe(), /parsing window data/);
});
