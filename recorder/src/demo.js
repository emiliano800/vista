// `--demo` mode: no native hooks. Simulates an accounts-payable clerk bouncing
// between Outlook, Acrobat, QuickBooks and Excel so the overlay and dashboard
// can be exercised on any machine (CI, VMs, design reviews).
import { EventEmitter } from 'node:events';

// `copies`: what the clerk copies here; `pastes`: true = re-keys the last copy into this app
const SCENES = [
  { app: 'Microsoft Outlook', title: 'Inbox - ap@northwind-hvac.com - Outlook', keys: 12, clicks: 4, secs: 9 },
  { app: 'Adobe Acrobat', title: 'INV-48213 Carrier Parts.pdf - Adobe Acrobat Reader', keys: 0, clicks: 3, secs: 7, copies: ['INV-48213', '1,284.50', 'Carrier Enterprise LLC'] },
  { app: 'QuickBooks', title: 'Enter Bills - Northwind HVAC - QuickBooks Desktop', keys: 46, clicks: 9, secs: 14, pastes: true },
  { app: 'Microsoft Excel', title: 'AP tracker 2026.xlsx - Excel', keys: 30, clicks: 6, secs: 11, pastes: true },
  { app: 'Microsoft Outlook', title: 'RE: INV-48213 approval - Message (HTML) - Outlook', keys: 40, clicks: 3, secs: 8 },
  { app: 'Google Chrome', title: 'Vendor portal - Carrier Enterprise - Google Chrome', keys: 8, clicks: 5, secs: 6 },
  { app: '1Password', title: '1Password - Vault', keys: 10, clicks: 2, secs: 4 },
];

export class DemoHook extends EventEmitter {
  start() {
    this._t = setInterval(() => this._tick(), 700);
  }
  stop() {
    clearInterval(this._t);
  }
  _tick() {
    const scene = currentScene();
    const r = Math.random();
    if (r < 0.15) this.emit('mousedown', { button: 1, x: 400 + Math.round(r * 800), y: 300, clicks: 1 });
    else if (r < 0.2) this.emit('wheel', { direction: 3, amount: 3 });
    else if (r < 0.4 && (scene.copies || scene.pastes)) {
      // Ctrl+C in the source app, Ctrl+V in the target app — swivel-chair re-keying
      if (scene.copies) clipboardText = scene.copies[Math.floor(Math.random() * scene.copies.length)];
      this.emit('keydown', { keycode: CTRL });
      this.emit('keydown', { keycode: scene.copies ? KEY_C : KEY_V });
      this.emit('keyup', { keycode: CTRL });
    } else if (r < 0.3) {
      this.emit('keydown', { keycode: CTRL });
      this.emit('keydown', { keycode: KEY_S });
      this.emit('keyup', { keycode: CTRL });
    } else if (scene.keys) {
      for (let i = 0; i < 3; i++) this.emit('keydown', { keycode: KEY_A + i });
    }
  }
}

let clipboardText = '';
export function demoClipboard() {
  return clipboardText;
}

const CTRL = 29;
const KEY_A = 30;
const KEY_S = 31;
const KEY_C = 46;
const KEY_V = 47;

let sceneStart = Date.now();
let sceneIdx = 0;
function currentScene() {
  const scene = SCENES[sceneIdx];
  if (Date.now() - sceneStart > scene.secs * 1000) {
    sceneIdx = (sceneIdx + 1) % SCENES.length;
    sceneStart = Date.now();
  }
  return SCENES[sceneIdx];
}

export function demoActiveWindow() {
  return async () => {
    const s = currentScene();
    return { id: sceneIdx + 1, title: s.title, owner: { name: s.app } };
  };
}

// keycode table for the demo hook, matching uiohook's UiohookKey values
export const DEMO_KEYS = new Map([
  [CTRL, 'Ctrl'],
  [KEY_A, 'A'],
  [KEY_A + 1, 'S'],
  [KEY_A + 2, 'D'],
  [KEY_S, 'S'],
  [KEY_C, 'C'],
  [KEY_V, 'V'],
]);
