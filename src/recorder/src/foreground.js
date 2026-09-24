// The foreground-window probe (`get-windows`) runs as a helper process. On macOS the
// helper checks Screen Recording permission for *itself*, and macOS 26 no longer
// credits it with the grant the Vista Recorder app holds, so the probe fails outright
// and every event lost its application name ("Unknown app" on the server, no
// cross-app patterns, nothing for Jev to judge — 2026-09-24). Screenshots, taken by
// the app itself, kept working, which is how the two diverged.
//
// Whatever the helper's reason, ask again with the permission check skipped before
// giving up: the application name and bounds come back, only the window title is
// empty. Once degraded, stay degraded (no point failing twice on every 500 ms poll),
// and say so once, with the helper's own words, so the failure is never silent again.

const SCREEN_RECORDING = /screen recording/i;

export function withPermissionFallback(activeWindow, { warn = () => {} } = {}) {
  let degraded = false;
  return async function probe() {
    if (degraded) return activeWindow({ screenRecordingPermission: false });
    try {
      return await activeWindow();
    } catch (err) {
      const reason = String(err?.message ?? err).replace(/\s+/g, ' ').trim().slice(0, 300);
      try {
        const win = await activeWindow({ screenRecordingPermission: false });
        degraded = true;
        warn(
          SCREEN_RECORDING.test(reason)
            ? 'foreground window helper cannot see the Screen Recording grant; recording app names without window titles'
            : `foreground window helper failed (${reason}); recording app names without window titles`,
        );
        return win;
      } catch (retryErr) {
        const retryReason = String(retryErr?.message ?? retryErr).replace(/\s+/g, ' ').trim().slice(0, 300);
        throw new Error(`foreground window helper failed twice: ${reason} | without permission check: ${retryReason}`);
      }
    }
  };
}
