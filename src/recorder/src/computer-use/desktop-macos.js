// macOS backend for the desktop harness. Reads the front window's controls through the
// accessibility tree (System Events, via `osascript`) and moves the real pointer/keyboard
// through nut-js when it is installed (an optional dependency; the recorder still builds
// without it), falling back to System Events for clicks and keystrokes.
//
// Needs the Accessibility permission (System Settings → Privacy & Security), the same one
// the recorder asks for to see window titles. Without it `elements()` returns [] and every
// targeted step is refused as `stale_observation` — nothing is clicked blind.
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

import { loadNut } from './pointer.js';
import { KEYS } from './policy.js';

const run = promisify(execFile);
export const MAX_ELEMENTS = 400;
export const AX_TIMEOUT_MS = 8000;

// System Events key codes for the keys the policy allows; SelectAll is ⌘A.
const KEY_CODE = { Enter: 36, Tab: 48, Escape: 53, Backspace: 51, ArrowUp: 126, ArrowDown: 125, ArrowLeft: 123, ArrowRight: 124 };

async function osascript(script, timeout = AX_TIMEOUT_MS) {
  const { stdout } = await run('osascript', ['-e', script], { timeout, maxBuffer: 4 * 1024 * 1024 });
  return stdout.trim();
}

const q = (s) => `"${String(s).replace(/[\\"]/g, '\\$&')}"`;

// One line per element: role<TAB>name<TAB>x<TAB>y<TAB>w<TAB>h<TAB>enabled
const ELEMENTS_SCRIPT = (app) => `
set out to ""
set n to 0
tell application "System Events"
  tell process ${q(app)}
    if (count of windows) is 0 then return ""
    set els to entire contents of window 1
    repeat with el in els
      if n ≥ ${MAX_ELEMENTS} then exit repeat
      try
        set r to role description of el
        set t to ""
        try
          set t to title of el
        end try
        if t is missing value or t is "" then
          try
            set t to description of el
          end try
        end if
        if t is missing value or t is "" then
          try
            set t to value of attribute "AXPlaceholderValue" of el
          end try
        end if
        if t is not missing value and t is not "" then
          set p to position of el
          set s to size of el
          set e to "1"
          try
            if enabled of el is false then set e to "0"
          end try
          set out to out & r & tab & t & tab & (item 1 of p) & tab & (item 2 of p) & tab & (item 1 of s) & tab & (item 2 of s) & tab & e & linefeed
          set n to n + 1
        end if
      end try
    end repeat
  end tell
end tell
return out`;

export function parseElements(text) {
  const out = [];
  for (const line of String(text ?? '').split('\n')) {
    if (!line.trim()) continue;
    const [role, name, x, y, width, height, enabled] = line.split('\t');
    if (role == null || name == null) continue;
    out.push({ id: `ax:${out.length}`, role, name, x: Number(x), y: Number(y), width: Number(width), height: Number(height), enabled: enabled !== '0' });
  }
  return out;
}

export async function macosBackend({ activeWindow, nut = loadNut() } = {}) {
  if (process.platform !== 'darwin' || typeof activeWindow !== 'function') return null;
  const n = await nut;
  return {
    async activeWindow() {
      const w = await activeWindow();
      if (!w) return null;
      return { app: w.owner?.name ?? '', title: w.title ?? '', bounds: w.bounds ?? null };
    },
    async elements(win) {
      if (!win?.app) return [];
      try {
        return parseElements(await osascript(ELEMENTS_SCRIPT(win.app)));
      } catch {
        return [];
      }
    },
    async moveTo(x, y) {
      if (n) await n.mouse.move(n.straightTo(new n.Point(x, y)));
      else this._at = { x, y };
    },
    async click() {
      if (n) return n.mouse.leftClick();
      const p = this._at;
      if (!p) throw Object.assign(new Error('Nothing to click.'), { code: 'harness_error' });
      await osascript(`tell application "System Events" to click at {${p.x}, ${p.y}}`);
    },
    async type(text) {
      if (n) return n.keyboard.type(text);
      await osascript(`tell application "System Events" to keystroke ${q(text)}`);
    },
    async press(key) {
      if (key === 'SelectAll') {
        if (n) return n.keyboard.pressKey(n.Key.LeftCmd, n.Key.A).then(() => n.keyboard.releaseKey(n.Key.LeftCmd, n.Key.A));
        return osascript('tell application "System Events" to keystroke "a" using command down');
      }
      if (!KEYS[key]) throw Object.assign(new Error(`Unknown key ${key}.`), { code: 'invalid_value' });
      if (n) {
        const k = n.Key[key === 'Enter' ? 'Return' : key === 'ArrowUp' ? 'Up' : key === 'ArrowDown' ? 'Down' : key === 'ArrowLeft' ? 'Left' : key === 'ArrowRight' ? 'Right' : key];
        if (k != null) return n.keyboard.pressKey(k).then(() => n.keyboard.releaseKey(k));
      }
      await osascript(`tell application "System Events" to key code ${KEY_CODE[key]}`);
    },
    async openApp(name) {
      await osascript(`tell application ${q(name)} to activate`);
    },
    async screenshot() {
      // The front window only, to a temp file, then base64. Screen Recording permission.
      const os = await import('node:os');
      const fs = await import('node:fs/promises');
      const path = await import('node:path');
      const file = path.join(os.tmpdir(), `vista-cu-${Date.now()}.png`);
      try {
        const id = await osascript('tell application "System Events" to tell (first process whose frontmost is true) to get value of attribute "AXWindowNumber" of window 1').catch(() => '');
        const args = id ? ['-x', '-l', id, file] : ['-x', file];
        await run('screencapture', args, { timeout: 8000 });
        const data = await fs.readFile(file);
        return data.toString('base64');
      } catch {
        return null;
      } finally {
        await fs.unlink(file).catch(() => {});
      }
    },
  };
}
