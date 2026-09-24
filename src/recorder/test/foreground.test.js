import assert from 'node:assert/strict';
import { test } from 'node:test';

import { frontmostViaLaunchServices, parseLsappinfo, withPermissionFallback } from '../src/foreground.js';

const WIN = { owner: { name: 'Excel', processId: 42 }, title: 'Q3 invoices.xlsx' };
const LS = { id: null, title: '', url: '', owner: { name: 'Google Chrome', bundleId: 'com.google.Chrome', processId: 7 }, source: 'lsappinfo' };
const permissionError = () =>
  new Error('get-windows requires the screen recording permission in “System Settings › Privacy & Security › Screen Recording”.');
const noLaunchServices = async () => {
  throw new Error('not on this platform');
};

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
  const probe = withPermissionFallback(helper, { warn: (m) => warnings.push(m), frontmost: noLaunchServices });

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
  const probe = withPermissionFallback(helper, { warn: (m) => warnings.push(m), frontmost: noLaunchServices });
  assert.equal((await probe()).owner.name, 'Excel');
  assert.match(warnings[0], /helper failed \(Command failed: \/app\/main\); recording app names/);
});

test('a helper that works is left alone, titles included, and LaunchServices is never asked', async () => {
  let asked = 0;
  const probe = withPermissionFallback(async () => WIN, {
    frontmost: async () => {
      asked += 1;
      return LS;
    },
  });
  assert.deepEqual(await probe(), WIN);
  assert.equal(asked, 0);
});

test('a helper that answers with no window is replaced by LaunchServices, noted once', async () => {
  const warnings = [];
  const probe = withPermissionFallback(async () => null, { warn: (m) => warnings.push(m), frontmost: async () => LS });
  assert.equal((await probe()).owner.name, 'Google Chrome');
  assert.equal((await probe()).source, 'lsappinfo');
  assert.deepEqual(warnings, ['the window helper answered with no window; recording app names from LaunchServices without window titles']);
});

test('a helper that fails both ways is replaced by LaunchServices too', async () => {
  const warnings = [];
  const probe = withPermissionFallback(
    async (options) => {
      throw new Error(options ? 'spawn EACCES' : 'Command failed');
    },
    { warn: (m) => warnings.push(m), frontmost: async () => LS },
  );
  assert.equal((await probe()).owner.name, 'Google Chrome');
  assert.match(warnings[0], /window helper failed \(spawn EACCES\); recording app names from LaunchServices/);
});

test('when nothing can name the frontmost app the probe fails with every reason', async () => {
  const probe = withPermissionFallback(async () => undefined, { frontmost: noLaunchServices });
  await assert.rejects(probe(), /answered with no window; LaunchServices failed too \(not on this platform\)/);
});

test('lsappinfo output is parsed into a window-shaped record', () => {
  const parsed = parseLsappinfo('"LSDisplayName"="Google Chrome"\n"CFBundleIdentifier"="com.google.Chrome"\n"pid"=26566\n');
  assert.deepEqual(parsed, { id: null, title: '', url: '', owner: { name: 'Google Chrome', bundleId: 'com.google.Chrome', processId: 26566 }, source: 'lsappinfo' });
  assert.equal(parseLsappinfo(''), null);
});

test('frontmostViaLaunchServices runs lsappinfo twice and parses the answer', async () => {
  if (process.platform !== 'darwin') return;
  const calls = [];
  const exec = async (cmd, args) => {
    calls.push([cmd, ...args]);
    return { stdout: args[0] === 'front' ? 'ASN:0x0-0x1:\n' : '"LSDisplayName"="Excel"\n"pid"=5\n' };
  };
  const win = await frontmostViaLaunchServices(exec);
  assert.equal(win.owner.name, 'Excel');
  assert.deepEqual(calls, [
    ['lsappinfo', 'front'],
    ['lsappinfo', 'info', '-only', 'name,bundleid,pid', 'ASN:0x0-0x1:'],
  ]);
});
