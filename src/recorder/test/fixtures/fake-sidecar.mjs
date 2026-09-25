// A stand-in for `python -m vista_device`: same NDJSON protocol, scripted answers.
// FAKE_SIDECAR_MODE: ok | silent (never answers) | garbage (non-JSON then exits) | leak.
import readline from 'node:readline';

const mode = process.env.FAKE_SIDECAR_MODE ?? 'ok';
const obs = (settled = true) => ({
  observation: { observation_id: 'obs-1', kind: 'browser', url: 'https://crm.example.com/deals/48213', title: 'Deal 48213', l0: ['in:abc', 'have:field:amount'], l1: { landmarks: ['main'] }, settled, candidates: [] },
  cloud: { kind: 'browser', l0: ['in:abc', 'have:field:amount'], l1: { landmarks: ['main'] }, descriptors: [] },
  leakage: { ok: mode !== 'leak', failures: mode === 'leak' ? [{ reason: 'exact_value' }] : [] },
});
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  if (mode === 'silent') return;
  if (mode === 'garbage') {
    process.stdout.write('this is not json\n');
    process.exit(3);
  }
  const { id, method, params } = JSON.parse(line);
  const reply = (result) => process.stdout.write(JSON.stringify({ id, result }) + '\n');
  if (method === 'health') return reply({ ok: true, version: 'fake', drivers: { browser: true, desktop: false } });
  if (method === 'open') return params.kind === 'browser' ? reply({ kind: 'browser', capabilities: ['observe', 'navigate', 'click', 'type', 'press', 'extract', 'screenshot', 'wait'] }) : process.stdout.write(JSON.stringify({ id, error: { code: 'harness_unsupported', message: 'no desktop here' } }) + '\n');
  if (method === 'observe') return reply(obs());
  if (method === 'perform') return reply({ ok: true, description: `${params.step.action} done`, result: { note: 'x' }, ...obs() });
  if (method === 'close') return reply({ closed: true });
  process.stdout.write(JSON.stringify({ id, error: { code: 'bad_request', message: `unknown ${method}` } }) + '\n');
});
rl.on('close', () => process.exit(0));
