// Linux (X11) backend for the desktop harness. The front window comes from `xdotool`, its
// controls from the AT-SPI2 accessibility bus (`desktop-linux-atspi.py`, system python with
// `gir1.2-atspi-2.0`), the pointer and keyboard from nut-js. Anything missing → `null`
// backend, so the device advertises no desktop capability rather than clicking blind.
//
// `openApp` only raises a window that already exists (matched by title or process name);
// it never launches a program — the vocabulary stays "act in what is open".
import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { loadNut, moveTo } from './pointer.js';
import { KEYS } from './policy.js';

const run = promisify(execFile);
const HERE = dirname(fileURLToPath(import.meta.url));
export const ATSPI_SCRIPT = join(HERE, 'desktop-linux-atspi.py');
export const AX_TIMEOUT_MS = 8000;
export const PYTHON = process.env.VISTA_CU_PYTHON || '/usr/bin/python3';

const NUT_KEY = { Enter: 'Return', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right' };

async function xdotool(args, timeout = AX_TIMEOUT_MS) {
  const { stdout } = await run('xdotool', args, { timeout, maxBuffer: 1024 * 1024 });
  return stdout.trim();
}

export function parseGeometry(text) {
  const pos = /Position:\s*(-?\d+),(-?\d+)/.exec(text);
  const geo = /Geometry:\s*(\d+)x(\d+)/.exec(text);
  if (!pos || !geo) return null;
  return { x: Number(pos[1]), y: Number(pos[2]), width: Number(geo[1]), height: Number(geo[2]) };
}

async function probe() {
  try {
    await xdotool(['version'], 2000);
    await run(PYTHON, ['-c', "import gi; gi.require_version('Atspi','2.0'); from gi.repository import Atspi"], { timeout: 8000 });
    return true;
  } catch {
    return false;
  }
}

export async function linuxBackend({ nut = loadNut(), available = probe } = {}) {
  if (process.platform !== 'linux' || !process.env.DISPLAY) return null;
  const n = await nut;
  if (!n || !(await available())) return null;
  return {
    async activeWindow() {
      try {
        const id = await xdotool(['getactivewindow']);
        if (!id) return null;
        const [title, pid, geometry] = await Promise.all([
          xdotool(['getwindowname', id]),
          xdotool(['getwindowpid', id]).catch(() => ''),
          xdotool(['getwindowgeometry', id]),
        ]);
        const app = pid ? (await readFile(`/proc/${pid}/comm`, 'utf8').catch(() => '')).trim() : '';
        return { id, pid: Number(pid) || 0, app, title, bounds: parseGeometry(geometry) };
      } catch {
        return null;
      }
    },
    async elements(win) {
      if (!win?.pid) return [];
      try {
        const { stdout } = await run(PYTHON, [ATSPI_SCRIPT, String(win.pid), win.title ?? ''], { timeout: AX_TIMEOUT_MS, maxBuffer: 4 * 1024 * 1024 });
        const parsed = JSON.parse(stdout || '[]');
        return Array.isArray(parsed) ? parsed : [];
      } catch {
        return [];
      }
    },
    moveTo: (x, y) => moveTo(n, x, y),
    click: () => n.mouse.leftClick(),
    type: (text) => n.keyboard.type(text),
    async press(key) {
      if (key === 'SelectAll') {
        await n.keyboard.pressKey(n.Key.LeftControl, n.Key.A);
        return n.keyboard.releaseKey(n.Key.LeftControl, n.Key.A);
      }
      if (!KEYS[key]) throw Object.assign(new Error(`Unknown key ${key}.`), { code: 'invalid_value' });
      const k = n.Key[NUT_KEY[key] ?? key];
      await n.keyboard.pressKey(k);
      return n.keyboard.releaseKey(k);
    },
    async openApp(name) {
      const ids = (await xdotool(['search', '--onlyvisible', '--name', name]).catch(() => '')) || (await xdotool(['search', '--onlyvisible', '--class', name]).catch(() => ''));
      const id = ids.split('\n').filter(Boolean).pop();
      if (!id) throw Object.assign(new Error(`No open window matches ${name}.`), { code: 'invalid_value' });
      await xdotool(['windowactivate', '--sync', id]);
    },
    async screenshot() {
      try {
        const id = await xdotool(['getactivewindow']);
        const { stdout } = await run('import', ['-window', id, 'png:-'], { encoding: 'buffer', timeout: 8000, maxBuffer: 16 * 1024 * 1024 });
        return Buffer.from(stdout).toString('base64');
      } catch {
        return null;
      }
    },
  };
}
