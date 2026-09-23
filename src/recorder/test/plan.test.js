import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

import { applyPlanEdits, compilePlan, edgeId, mergeGraphs, planSummary, stateKey } from '../src/plan.js';

const T0 = Date.parse('2026-09-19T09:00:00Z');
const at = (ms) => new Date(T0 + ms).toISOString();
const ev = (ms, event_type, app, extra = {}) => ({ timestamp: at(ms), event_type, app, window_title: '', url: '', text: '', payload: {}, ...extra });

// An invoice re-keyed from a PDF into QuickBooks, then saved.
function invoiceEvents() {
  return [
    ev(0, 'focus', 'Preview', { window_title: 'INV-1042 ACME.pdf' }),
    ev(1000, 'copy', 'Preview', { element: 'Total', text: '1,250.00', payload: { clip_hash: 'h-total', chars: 8 } }),
    ev(2000, 'focus', 'QuickBooks', { window_title: 'Bills - ACME Corp', url: 'https://qbo.example/bills/new' }),
    ev(3000, 'click', 'QuickBooks', { payload: { x: 412, y: 233 } }),
    ev(4000, 'paste', 'QuickBooks', { element: 'Amount', text: '1,250.00', payload: { clip_hash: 'h-total' } }),
    ev(5000, 'key', 'QuickBooks', { text: 'A' }),
    ev(5100, 'key', 'QuickBooks', { text: 'C' }),
    ev(5200, 'key', 'QuickBooks', { text: 'M' }),
    ev(6000, 'key', 'QuickBooks', { payload: { key: 'Enter' } }),
    ev(7000, 'shortcut', 'QuickBooks', { text: 'Cmd+S' }),
  ];
}
const files = [{ id: 'abcdef123456', name: 'INV-1042 ACME.pdf', ext: '.pdf', intervals: [{ start: T0, end: T0 + 1500, app: 'Preview' }] }];

const SECRETS = ['INV-1042', 'ACME', '1,250', 'qbo.example', '412', '233', 'h-total'];

test('a recording compiles to a deterministic state graph with no values in it', () => {
  const a = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  const b = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  assert.equal(JSON.stringify(a.graph), JSON.stringify(b.graph));
  const { graph, anchors } = a;
  assert.equal(graph.compiled_by, 'recorder-plan/1');
  assert.equal(graph.trajectories, 1);
  assert.equal(graph.start.length, 1);
  const text = JSON.stringify(graph);
  for (const s of SECRETS) assert.ok(!text.includes(s), `graph leaks ${s}`);
  assert.ok(JSON.stringify(anchors).includes('qbo.example'), 'anchors keep the URL locally');

  const actions = graph.edges.map((e) => e.action_class).sort();
  assert.deepEqual(actions, ['click', 'navigate', 'press', 'read', 'submit', 'type_value', 'type_value']);
  const read = graph.edges.find((e) => e.action_class === 'read');
  assert.deepEqual(read.produces, ['f1']);
  assert.deepEqual(read.effect, ['fact:f1']);
  assert.equal(read.control, 'Total');
  const paste = graph.edges.find((e) => e.action_class === 'type_value' && e.slot === 'f1');
  assert.equal(paste.control, 'Amount');
  assert.deepEqual(paste.effect, ['field:Amount']);
  const typed = graph.edges.find((e) => e.action_class === 'type_value' && e.slot !== 'f1');
  assert.equal(typed.slot, 'input_1');
  assert.equal(typed.provenance[0].event_ids.length, 3, 'a run of printable keys is one move');
  const submit = graph.edges.find((e) => e.action_class === 'submit');
  assert.equal(submit.policy, 'always_ask');
  assert.equal(submit.control, 'Cmd+S');

  const start = graph.nodes.find((n) => n.key === graph.start[0]);
  assert.equal(start.app_role, 'pdf');
  assert.deepEqual(start.signature, ['doc:d1']);
  const held = graph.nodes.find((n) => n.key === paste.to);
  assert.deepEqual(held.signature, ['fact:f1', 'field:Amount']);
  assert.equal(held.app_role, 'accounting');
  assert.equal(graph.nodes.filter((n) => n.terminal).length, 1);

  for (const n of graph.nodes) assert.equal(n.key, stateKey(n.app_role, n.activity, n.signature));
  for (const e of graph.edges) {
    assert.equal(e.id, edgeId(e.frm, e.to, e.action_class, e.control, e.slot));
    assert.equal(e.provenance[0].id, 'rec-1');
    assert.ok(e.provenance[0].event_ids.every((x) => /^e\d+$/.test(x)));
    assert.equal(e.stats.recorded, 1);
  }
});

test('state keys match the worker: sha1 of role|activity|sorted signature, 16 hex chars', () => {
  const expect = createHash('sha1').update('accounting|accounting|fact:f1,field:Amount').digest('hex').slice(0, 16);
  assert.equal(stateKey('accounting', 'accounting', ['field:Amount', 'fact:f1']), expect);
});

test('excluded sections, pauses and private apps never reach the graph', () => {
  const events = [
    ...invoiceEvents(),
    ev(8000, 'focus', '1Password'),
    ev(8500, 'paste', '1Password', { element: 'Password' }),
    ev(9000, 'focus', 'Excel'),
    ev(9500, 'paste', 'Excel', { element: 'B2' }),
  ];
  const { graph } = compilePlan({ recordingId: 'rec-1', events, files, excluded: [{ start: at(8900), end: at(10000) }] });
  const roles = new Set(graph.nodes.map((n) => n.app_role));
  assert.ok(!roles.has('password_manager') && !roles.has('spreadsheet'));
  assert.ok(!JSON.stringify(graph).includes('Password'));
});

test('a second recording of the same work merges onto the same states and adds support', () => {
  const one = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files }).graph;
  const variant = invoiceEvents().filter((e) => e.event_type !== 'click'); // no click this time
  const two = compilePlan({ recordingId: 'rec-2', events: variant, files }).graph;
  const merged = mergeGraphs([one, two]);
  assert.equal(merged.trajectories, 2);
  assert.deepEqual(merged.start, one.start);
  const read = merged.edges.find((e) => e.action_class === 'read');
  assert.equal(read.stats.support, 2);
  assert.deepEqual(read.provenance.map((p) => p.id), ['rec-1', 'rec-2']);
  const click = merged.edges.find((e) => e.action_class === 'click');
  assert.equal(click.stats.support, 1, 'the optional step is a low-support branch, not a conflict');
  assert.equal(merged.edges.length, one.edges.length, 'a click that changes nothing held is a self-loop, so the variant adds no branch');
  assert.equal(JSON.stringify(mergeGraphs([two, one])), JSON.stringify(merged), 'merge order does not matter');
});

test('review edits drop moves or make them ask, never the other way', () => {
  const { graph } = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  const click = graph.edges.find((e) => e.action_class === 'click');
  const read = graph.edges.find((e) => e.action_class === 'read');
  const edited = applyPlanEdits(graph, { [click.id]: { excluded: true }, [read.id]: { policy: 'always_ask' }, [graph.edges.find((e) => e.action_class === 'submit').id]: { policy: 'auto' } });
  assert.ok(!edited.edges.some((e) => e.id === click.id));
  assert.equal(edited.edges.find((e) => e.id === read.id).policy, 'always_ask');
  assert.ok(edited.edges.every((e) => e.policy !== 'auto' || graph.edges.find((g) => g.id === e.id).policy === 'auto'));
  assert.ok(edited.nodes.length <= graph.nodes.length);
  const s = planSummary(edited);
  assert.equal(s.moves, graph.edges.length - 1);
  assert.deepEqual(s.roles, ['accounting', 'pdf']);
  assert.equal(s.submits, 1);
});

test('graphs are bounded: a very long session is truncated, not unbounded', () => {
  const events = [];
  for (let i = 0; i < 1500; i++) {
    events.push(ev(i * 1000, 'copy', i % 2 ? 'Preview' : 'Excel', { element: `Cell${i}`, payload: { clip_hash: `h${i}` } }));
  }
  const { graph } = compilePlan({ recordingId: 'rec-long', events, files: [] });
  assert.ok(graph.nodes.length <= 200);
  assert.ok(graph.edges.length <= 600);
  assert.equal(graph.truncated, true);
  for (const n of graph.nodes) assert.ok(n.signature.length <= 40);
});
