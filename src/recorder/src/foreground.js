// The foreground-window probe (`get-windows`) runs as a helper process. On macOS the
// helper checks Screen Recording permission for *itself*, and macOS 26 no longer
// credits it with the grant the Vista Recorder app holds, so the probe fails outright
// and every event lost its application name ("Unknown app" on the server, no
// cross-app patterns, nothing for Jev to judge — 2026-09-24). Screenshots, taken by
// the app itself, kept working, which is how the two diverged.
//
// When the helper refuses on those grounds, ask again with the permission check
// skipped: the application name and bounds come back, only the window title is empty.

const SCREEN_RECORDING = /screen recording/i;

export function withPermissionFallback(activeWindow, { warn = () => {} } = {}) {
  let degraded = false;
  return async function probe() {
    if (degraded) return activeWindow({ screenRecordingPermission: false });
    try {
      return await activeWindow();
    } catch (err) {
      if (!SCREEN_RECORDING.test(err?.message ?? '')) throw err;
      degraded = true;
      warn('foreground window helper cannot see the Screen Recording grant; recording app names without window titles');
      return activeWindow({ screenRecordingPermission: false });
    }
  };
}
