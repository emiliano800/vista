// Electron smoke test for the browser driver: `npx electron test/cu-browser-smoke.mjs`.
// Opens the sandbox page on a local form, observes it through the accessibility tree,
// types into a field, clicks the button and checks the page reacted. Exits 0/1.
import { writeFile } from 'node:fs/promises';
import http from 'node:http';

import { app } from 'electron';

import { BrowserHarness } from '../src/computer-use/browser.js';
import { openSandboxPage } from '../src/computer-use/browser-electron.js';
import { nutPointer } from '../src/computer-use/pointer.js';

const PAGE = `<!doctype html><title>Sandbox form</title>
<form onsubmit="event.preventDefault(); document.getElementById('out').textContent='saved:'+document.getElementById('inv').value">
<label for="inv">Invoice number</label><input id="inv" value="old">
<button type="submit">Save</button></form>
<table><tr><th>Invoice</th><th>Total</th></tr><tr><td>INV-1</td><td>10</td></tr></table>
<p id="out">not saved</p>`;

// Electron emits `ready` only after the main module finished evaluating, so no
// top-level await here.
app.whenReady().then(main);

async function main() {
  const server = http.createServer((_, res) => res.end(PAGE)).listen(0, '127.0.0.1');
  await new Promise((r) => server.once('listening', r));
  const url = `http://127.0.0.1:${server.address().port}/form`;
  let code = 0;
  const check = (cond, msg) => {
    console.log(`${cond ? 'ok  ' : 'FAIL'} ${msg}`);
    if (!cond) code = 1;
  };
  try {
    const pointer = await nutPointer();
    console.log(`     pointer: ${pointer ? 'real (nut-js)' : 'none — CDP input'}`);
    const h = new BrowserHarness({
      open: openSandboxPage({ width: 900, height: 600 }),
      pointer,
    });
    const nav = await h.perform({ action: 'navigate', value: url });
    check(nav.ok, `navigate: ${nav.description}`);
    const names = nav.observation.candidates.map((c) => `${c.role}:${c.name}`);
    console.log('     candidates', names);
    const field = nav.observation.candidates.find((c) => c.role === 'textbox');
    const button = nav.observation.candidates.find((c) => c.role === 'button');
    check(field && button, 'field and button enumerated from the AX tree');
    const typed = await h.perform({
      action: 'type',
      target_id: field.id,
      observation_id: nav.observation.observation_id,
      value: 'INV-1042',
    });
    check(typed.ok && typed.result.previous_value === 'old', `type: previous_value=${typed.result?.previous_value}`);
    const btn2 = typed.observation.candidates.find((c) => c.role === 'button');
    const click = await h.perform({
      action: 'click',
      target_id: btn2.id,
      observation_id: typed.observation.observation_id,
    });
    check(click.ok, `click: ${click.description}`);
    const text = await h.perform({ action: 'extract', value: 'text' });
    check(text.result.text.includes('saved:INV-1042'), `page reacted: ${JSON.stringify(text.result.text.slice(-40))}`);
    const table = await h.perform({ action: 'extract', value: 'table' });
    check(table.result.count === 1 && table.result.columns[0] === 'Invoice', `table: ${JSON.stringify(table.result)}`);
    const shot = await h.perform({ action: 'screenshot' });
    check(shot.evidence?.screenshot_base64?.length > 100, 'screenshot captured');
    if (process.env.VISTA_SMOKE_SHOT) await writeFile(process.env.VISTA_SMOKE_SHOT, Buffer.from(shot.evidence.screenshot_base64, 'base64'));
    const halo = await h.page.send('Runtime.evaluate', {
      expression: `(() => { const h = document.getElementById('vista-pointer-halo'); const r = h?.getBoundingClientRect(); return h ? { x: r.x + r.width / 2, y: r.y + r.height / 2 } : null; })()`,
      returnByValue: true,
    });
    check(halo.result?.value && halo.result.value.x > 0, `pointer halo drawn in the page at ${JSON.stringify(halo.result?.value)}`);
    check(!click.observation.candidates.some((c) => c.id === 'vista-pointer-halo' || /halo/i.test(c.name)), 'halo is not an enumerated candidate');
    const priv = await h.perform({ action: 'navigate', value: `${url}?login` });
    check(priv.ok && priv.observation.sensitive === true && priv.observation.candidates.length === 0, 'sign-in-like URL flagged, no candidates');
    const refused = await h.perform({ action: 'press', value: 'Enter' });
    check(!refused.ok && refused.error.code === 'sensitive_window', `steps refused there: ${refused.error?.code}`);
    await h.close();
  } catch (e) {
    console.error('FAIL', e);
    code = 1;
  }
  server.close();
  app.exit(code);
}
