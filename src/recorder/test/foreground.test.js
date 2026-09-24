import assert from 'node:assert/strict';
import { test } from 'node:test';

import { withPermissionFallback } from '../src/foreground.js';

const WIN = { owner: { name: 'Excel', processId: 42 }, title: 'Q3 invoices.xlsx' };
const permissionError = () =>
  new Error('get-windows requires the screen recording permission in “System Settings › Privacy & Security › Screen Recording”.');

function helperThatRefuses(error) {
  const calls = [];
  const helper = async (options) => {
    calls.push(options);
    if (options?.screenRecordingPermission === false) return { ...WIN, title: '' };
    throw error();
  };
  return { helper, calls };
}

test('a helper that refuses on Screen Recording grounds is asked again without the check', async () => {
  const { helper, calls } = helperThatRefuses(permissionError);
  const warnings = [];
  const probe = withPermissionFallback(helper, { warn: (m) => warnings.push(m) });

  const first = await probe();
  assert.equal(first.owner.name, 'Excel', 'the application name is kept');
  assert.equal(first.title, '', 'only the window title is given up');
  assert.deepEqual(calls, [undefined, { screenRecordingPermission: false }]);
  assert.match(warnings[0], /cannot see the Screen Recording grant/);

  const second = await probe();
  assert.equal(second.owner.name, 'Excel');
  assert.equal(calls.length, 3, 'after one refusal every later probe skips the check without retrying');
  assert.equal(warnings.length, 1, 'warned once');
});

test('any other helper failure is retried the same way, and the reason is quoted', async () => {
  const { helper } = helperThatRefuses(() => new Error('Command failed: /app/main\n'));
  const warnings = [];
  const probe = withPermissionFallback(helper, { warn: (m) => warnings.push(m) });
  assert.equal((await probe()).owner.name, 'Excel');
  assert.match(warnings[0], /helper failed \(Command failed: \/app\/main\); recording app names/);
});

test('a helper that works is left alone, titles included', async () => {
  const probe = withPermissionFallback(async () => WIN);
  assert.deepEqual(await probe(), WIN);
});

test('a helper that fails both ways surfaces both reasons', async () => {
  const probe = withPermissionFallback(async (options) => {
    throw new Error(options ? 'spawn EACCES' : 'Command failed');
  });
  await assert.rejects(probe(), /failed twice: Command failed \| without permission check: spawn EACCES/);
});
