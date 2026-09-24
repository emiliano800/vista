// Real desktop smoke on Linux: raise a window by title, enumerate its AT-SPI controls, type into
// a field and press a button through the real pointer/keyboard. Not part of `npm test`.
//
//   (zenity --forms --title "Vista Desktop Probe" --add-entry Name --add-entry Amount &)
//   node test/cu-desktop-smoke.mjs "Vista Desktop Probe"
import { DesktopHarness } from '../src/computer-use/desktop.js';
import { linuxBackend } from '../src/computer-use/desktop-linux.js';

const title = process.argv[2] ?? 'Vista Desktop Probe';
const backend = await linuxBackend();
if (!backend) {
  console.log('FAIL linux desktop backend unavailable (need DISPLAY, xdotool, nut-js, gir1.2-atspi-2.0)');
  process.exit(1);
}
const h = new DesktopHarness({ backend });
const show = (r) => `${r.ok ? 'ok  ' : 'FAIL'} ${r.description}${r.error ? ` [${r.error.code}]` : ''}`;

let r = await h.perform({ action: 'navigate', value: title });
console.log(show(r));
const obs = r.observation;
console.log(`     app=${obs?.app} title=${JSON.stringify(obs?.window_title)} candidates=${obs?.candidates.length}`);
for (const c of obs?.candidates ?? []) console.log(`       ${c.kind.padEnd(11)} ${c.role.padEnd(12)} ${c.name}`);

const field = obs?.candidates.find((c) => c.kind === 'field');
if (field) {
  r = await h.perform({ action: 'type', observation_id: obs.observation_id, target_id: field.id, value: 'Ridgeway Supply' });
  console.log(show(r));
}
const button = r.observation?.candidates.find((c) => c.kind === 'interactive' && /cancel/i.test(c.name));
if (button) {
  r = await h.perform({ action: 'click', observation_id: r.observation.observation_id, target_id: button.id, description: `Clicked ${button.name}` });
  console.log(show(r), r.result);
}
await h.close();
