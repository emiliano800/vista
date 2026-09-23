// Drives a tab in a running Chrome through the browser harness. Needs Chrome started with
// --remote-debugging-port and a URL to load:
//   VISTA_CU_CHROME_ENDPOINT=http://127.0.0.1:9222 node test/cu-chrome-smoke.mjs http://127.0.0.1:8765/meridian_risk_partners/index.html
import { BrowserHarness } from '../src/computer-use/browser.js';
import { openChromePage } from '../src/computer-use/browser-chrome.js';
import { nutPointer } from '../src/computer-use/pointer.js';

const url = process.argv[2];
if (!url) throw new Error('usage: cu-chrome-smoke.mjs <url>');
const ok = (label, cond, extra = '') => console.log(`${cond ? 'ok  ' : 'FAIL'} ${label}${extra ? ' ' + extra : ''}`);

const pointer = await nutPointer();
ok('real pointer available', Boolean(pointer));
const h = new BrowserHarness({ open: openChromePage(), pointer });
const nav = await h.perform({ step_id: 's1', action: 'navigate', value: url });
ok('navigate', nav.ok, nav.ok ? `→ ${nav.observation.title}` : JSON.stringify(nav.error));
const obs = nav.observation;
ok('candidates enumerated', obs.candidates.length > 0, `n=${obs.candidates.length}`);
console.log(obs.candidates.slice(0, 40).map((c) => `  [${c.kind}] ${c.role}: ${c.name}${c.attrs.has_value ? ' (has value)' : ''}`).join('\n'));
const link = obs.candidates.find((c) => c.kind === 'link' && /clients/i.test(c.name));
if (link) {
  const r = await h.perform({ step_id: 's2', action: 'click', target_id: link.id, observation_id: obs.observation_id });
  ok('click nav link', r.ok, r.ok ? `→ ${r.observation.url}` : JSON.stringify(r.error));
  const rows = r.observation?.candidates.filter((c) => c.kind === 'row') ?? [];
  ok('rows enumerated', rows.length > 0, `n=${rows.length}${rows[0] ? ` e.g. "${rows[0].name.slice(0, 80)}"` : ''}`);
  const field = r.observation?.candidates.find((c) => c.kind === 'field');
  ok('search field enumerated', Boolean(field), field ? `"${field.name}"` : '');
}
await h.close();
