// The foreground-window probe (`get-windows`) runs as a helper process. On macOS the
// helper checks Screen Recording permission for *itself*, and the window list it reads
// is gated the same way; macOS 26 does not always credit the helper with the grant the
// Vista Recorder app holds (screenshots, taken by the app itself, kept working). The
// helper then either refuses, or answers with no window at all — and every event lost
// its application name ("Unknown app" on the server, no cross-app patterns, nothing for
// Jev to judge, 2026-09-24).
//
// Three layers, each noted once so the failure is never silent again:
//   1. the helper as shipped (app name, title, url);
//   2. the helper with its permission check skipped (app name, empty title);
//   3. LaunchServices (`lsappinfo`), which needs no permission at all and names the
//      frontmost application — app name only, which is what the analysis needs.

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);
const SCREEN_RECORDING = /screen recording/i;

const reasonOf = (err) => String(err?.message ?? err).replace(/\s+/g, ' ').trim().slice(0, 300);

/** Frontmost application via LaunchServices, or null off macOS / on any failure. */
export async function frontmostViaLaunchServices(exec = run) {
  if (process.platform !== 'darwin') return null;
  const { stdout: asn } = await exec('lsappinfo', ['front'], { timeout: 1000 });
  const id = asn.trim();
  if (!id) return null;
  const { stdout } = await exec('lsappinfo', ['info', '-only', 'name,bundleid,pid', id], { timeout: 1000 });
  return parseLsappinfo(stdout);
}

export function parseLsappinfo(text) {
  const field = (key) => text.match(new RegExp(`"${key}"\\s*=\\s*"?([^"\\n]*)"?`))?.[1]?.trim() ?? '';
  const name = field('LSDisplayName');
  if (!name) return null;
  const pid = Number.parseInt(field('pid'), 10);
  return {
    id: null,
    title: '',
    url: '',
    owner: { name, bundleId: field('CFBundleIdentifier') || undefined, processId: Number.isFinite(pid) ? pid : undefined },
    source: 'lsappinfo',
  };
}

export function withPermissionFallback(activeWindow, { warn = () => {}, frontmost = frontmostViaLaunchServices } = {}) {
  let degraded = false; // helper answers only without its permission check
  let noted = false; // said once why LaunchServices is answering
  const say = (m) => {
    if (!noted) warn(m);
    noted = true;
  };

  async function helper() {
    if (degraded) return activeWindow({ screenRecordingPermission: false });
    try {
      return await activeWindow();
    } catch (err) {
      const reason = reasonOf(err);
      const win = await activeWindow({ screenRecordingPermission: false }); // may throw: handled by probe()
      degraded = true;
      warn(
        SCREEN_RECORDING.test(reason)
          ? 'foreground window helper cannot see the Screen Recording grant; recording app names without window titles'
          : `foreground window helper failed (${reason}); recording app names without window titles`,
      );
      return win;
    }
  }

  return async function probe() {
    let reason = null;
    try {
      const win = await helper();
      if (win?.owner?.name) return win;
      reason = 'the window helper answered with no window';
    } catch (err) {
      reason = `the window helper failed (${reasonOf(err)})`;
    }
    const app = await frontmost().catch((err) => {
      throw new Error(`${reason}; LaunchServices failed too (${reasonOf(err)})`);
    });
    if (!app) throw new Error(`${reason}; LaunchServices named no frontmost application`);
    say(`${reason}; recording app names from LaunchServices without window titles`);
    return app;
  };
}
