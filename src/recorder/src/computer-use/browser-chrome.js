// Real-Chrome side of the browser harness: a new tab in the employee's own running Chrome,
// reached over its DevTools port (`chrome --remote-debugging-port=<port>`), driven with the
// same CDP page adapter shape `browser-electron.js` returns. This deliberately leaves the
// recorder's isolated partition: the tab shares the employee's profile, cookies and logins,
// so it is only used when `VISTA_CU_BROWSER=chrome` is set. Everything above the adapter —
// the closed vocabulary, candidate enumeration, private-window refusal, stale-observation
// checks, consent, gates and the ledger — is unchanged. Imported only by main.js.

export const DEFAULT_ENDPOINT = 'http://127.0.0.1:9222';
export const LOAD_TIMEOUT_MS = 20000;
export const COMMAND_TIMEOUT_MS = 15000;

// One CDP connection to the browser endpoint; commands are multiplexed by sessionId.
export class ChromeConnection {
  constructor(ws) {
    this.ws = ws;
    this.seq = 0;
    this.pending = new Map();
    this.listeners = new Set();
    ws.addEventListener('message', (ev) => this._onMessage(String(ev.data)));
    ws.addEventListener('close', () => {
      for (const { reject } of this.pending.values()) reject(Object.assign(new Error('Chrome closed the DevTools connection.'), { code: 'harness_error' }));
      this.pending.clear();
    });
  }

  static async connect(endpoint = DEFAULT_ENDPOINT, { WebSocketImpl = globalThis.WebSocket, fetchImpl = globalThis.fetch } = {}) {
    const res = await fetchImpl(`${endpoint.replace(/\/$/, '')}/json/version`);
    if (!res.ok) throw Object.assign(new Error(`Chrome DevTools endpoint answered ${res.status}.`), { code: 'harness_error' });
    const { webSocketDebuggerUrl } = await res.json();
    const ws = new WebSocketImpl(webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', () => reject(Object.assign(new Error('Could not connect to Chrome DevTools.'), { code: 'harness_error' })), { once: true });
    });
    return new ChromeConnection(ws);
  }

  send(method, params = {}, sessionId = undefined, timeoutMs = COMMAND_TIMEOUT_MS) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(Object.assign(new Error(`${method} did not answer in time.`), { code: 'timeout' }));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }));
    });
  }

  on(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  close() {
    try { this.ws.close(); } catch { /* already closed */ }
  }

  _onMessage(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    if (msg.id != null && this.pending.has(msg.id)) {
      const { resolve, reject } = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) reject(Object.assign(new Error(msg.error.message), { code: 'harness_error', cdp: msg.error }));
      else resolve(msg.result ?? {});
      return;
    }
    if (msg.method) for (const fn of this.listeners) fn(msg);
  }
}

// page CSS pixel → screen pixel, from what the page itself knows about its window: the
// viewport's screen origin is the window origin plus the browser chrome (toolbar height,
// side borders), all in CSS pixels; nut-js wants device pixels.
export const GEOMETRY_SCRIPT = `({
  sx: window.screenX + (window.outerWidth - window.innerWidth) / 2,
  sy: window.screenY + (window.outerHeight - window.innerHeight),
  w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio || 1,
  hidden: document.visibilityState !== 'visible',
})`;

export function screenPointFrom(geometry, x, y) {
  if (!geometry || geometry.hidden || x < 0 || y < 0 || x > geometry.w || y > geometry.h) return null;
  return { x: Math.round((geometry.sx + x) * geometry.dpr), y: Math.round((geometry.sy + y) * geometry.dpr) };
}

export function openChromePage({ endpoint = process.env.VISTA_CU_CHROME_ENDPOINT || DEFAULT_ENDPOINT, connect = ChromeConnection.connect } = {}) {
  return async () => {
    const conn = await connect(endpoint);
    const { targetId } = await conn.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await conn.send('Target.attachToTarget', { targetId, flatten: true });
    const send = (method, params = {}) => conn.send(method, params, sessionId);
    await send('Page.enable');
    await send('Runtime.enable');
    await send('DOM.enable');
    await send('Accessibility.enable');
    const evaluate = async (expression) => {
      const r = await send('Runtime.evaluate', { expression, returnByValue: true });
      return r.result?.value;
    };
    return {
      send,
      loadURL: (url) =>
        new Promise((resolve, reject) => {
          const timer = setTimeout(() => { off(); reject(Object.assign(new Error('The page did not finish loading in time.'), { code: 'timeout' })); }, LOAD_TIMEOUT_MS);
          const off = conn.on((msg) => {
            if (msg.sessionId === sessionId && msg.method === 'Page.loadEventFired') { clearTimeout(timer); off(); resolve(); }
          });
          send('Page.navigate', { url })
            .then((r) => { if (r.errorText) { clearTimeout(timer); off(); reject(Object.assign(new Error(r.errorText), { code: 'navigation_failed' })); } })
            .catch((e) => { clearTimeout(timer); off(); reject(Object.assign(new Error(e.message), { code: 'navigation_failed' })); });
        }),
      url: async () => String((await evaluate('location.href')) ?? ''),
      title: async () => String((await evaluate('document.title')) ?? ''),
      screenPoint: async (x, y) => {
        await send('Page.bringToFront').catch(() => {});
        return screenPointFrom(await evaluate(GEOMETRY_SCRIPT), x, y);
      },
      close: async () => {
        await conn.send('Target.closeTarget', { targetId }).catch(() => {});
        conn.close();
      },
    };
  };
}
