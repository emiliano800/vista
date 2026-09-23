// Electron side of the browser harness: a visible BrowserWindow in its own storage
// partition (`persist:vista-computer-use`) — no cookies, logins or history from the
// employee's browsing — driven over CDP through `webContents.debugger`. Imported only by
// main.js; `browser.js` never touches Electron.
import { BrowserWindow, screen } from 'electron';

export const PARTITION = 'persist:vista-computer-use';
export const LOAD_TIMEOUT_MS = 20000;

export function openSandboxPage({ title = 'Vista is working here — ⌘/Ctrl+Shift+Esc stops', width = 1200, height = 820 } = {}) {
  return async () => {
    const win = new BrowserWindow({
      width,
      height,
      title,
      show: true,
      autoHideMenuBar: true,
      webPreferences: { partition: PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false, devTools: false },
    });
    win.on('page-title-updated', (e) => e.preventDefault());
    const wc = win.webContents;
    wc.setWindowOpenHandler(() => ({ action: 'deny' })); // one page, no pop-ups
    // The renderer only exists once something has loaded; CDP commands sent before that
    // never answer.
    await wc.loadURL('about:blank');
    wc.debugger.attach('1.3');
    await wc.debugger.sendCommand('Accessibility.enable');
    await wc.debugger.sendCommand('DOM.enable');
    await wc.debugger.sendCommand('Runtime.enable');
    await wc.debugger.sendCommand('Page.enable');
    return {
      send: (method, params = {}) => wc.debugger.sendCommand(method, params),
      loadURL: (url) =>
        new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(Object.assign(new Error('The page did not finish loading in time.'), { code: 'timeout' })), LOAD_TIMEOUT_MS);
          wc.loadURL(url)
            .then(() => resolve())
            .catch((e) => (e?.code === 'ERR_ABORTED' ? resolve() : reject(Object.assign(new Error(e.message), { code: 'navigation_failed' }))))
            .finally(() => clearTimeout(timer));
        }),
      url: async () => wc.getURL(),
      title: async () => wc.getTitle(),
      // page CSS pixel → screen pixel for a real pointer: the window's content origin plus
      // the page point scaled by the zoom factor. `null` when the window is not visible.
      screenPoint: async (x, y) => {
        if (win.isDestroyed() || !win.isVisible() || win.isMinimized()) return null;
        const b = win.getContentBounds();
        const z = wc.getZoomFactor();
        const p = { x: Math.round(b.x + x * z), y: Math.round(b.y + y * z) };
        const d = screen.getDisplayNearestPoint(p).workArea;
        if (p.x < d.x || p.y < d.y || p.x > d.x + d.width || p.y > d.y + d.height) return null;
        win.focus();
        return p;
      },
      close: async () => {
        try {
          if (wc.debugger.isAttached()) wc.debugger.detach();
        } catch {
          /* already gone */
        }
        if (!win.isDestroyed()) win.destroy();
      },
    };
  };
}
