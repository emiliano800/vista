import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

import { applyPlanEdits, compilePlan, compileReport, edgeId, mergeGraphs, planSummary, stateKey } from '../src/plan.js';

import { SECRETS, T0, at, ev, invoiceEvents, invoiceFiles as files } from './fixtures/invoice-recording.js';

test('a recording compiles to a deterministic state graph with no values in it', () => {
  const a = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  const b = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  assert.equal(JSON.stringify(a.graph), JSON.stringify(b.graph));
  const { graph, anchors, report } = a;
  assert.equal(graph.compiled_by, 'recorder-plan/2');
  assert.equal(graph.trajectories, 1);
  assert.equal(graph.start.length, 1);
  const text = JSON.stringify(graph);
  for (const s of SECRETS) assert.ok(!text.includes(s), `graph leaks ${s}`);
  assert.ok(JSON.stringify(anchors).includes('qbo.example'), 'anchors keep the URL locally');
  assert.ok(JSON.stringify(anchors).includes('Total'), 'anchors keep the raw control name locally');
  assert.deepEqual(graph.vocabulary.words, ['amount'], 'only names recurring across screens are vocabulary');
  assert.equal(report.leakage.ok, true, JSON.stringify(report.leakage.failures));
  assert.ok(report.values_checked >= 5 && report.titles_checked === 3);

  const actions = graph.edges.map((e) => e.action_class).sort();
  assert.deepEqual(actions, ['click', 'navigate', 'press', 'read', 'submit', 'type_value', 'type_value', 'type_value']);
  const read = graph.edges.find((e) => e.action_class === 'read');
  assert.deepEqual(read.produces, ['f1']);
  assert.deepEqual(read.effect, ['fact:f1']);
  assert.equal(read.control, null, 'a name seen on one screen is data, not a control name');
  const paste = graph.edges.find((e) => e.action_class === 'type_value' && e.control === 'amount');
  assert.equal(paste.slot, 'f1');
  assert.deepEqual(paste.effect, ['field:amount']);
  assert.equal(paste.provenance[0].event_ids.length, 2, 'the second bill re-keys the same amount from the same state: one move, two citations');
  const memo = graph.edges.find((e) => e.action_class === 'type_value' && e.slot === 'f1' && e.control === null);
  assert.deepEqual(memo.effect, ['field:c2'], 'an unnamed control is an ordinal (Total was c1)');
  const typed = graph.edges.find((e) => e.action_class === 'type_value' && e.slot === 'input_1');
  assert.equal(typed.provenance[0].event_ids.length, 3, 'a run of printable keys is one move');
  const submit = graph.edges.find((e) => e.action_class === 'submit');
  assert.equal(submit.policy, 'always_ask');
  assert.equal(submit.control, 'cmd+s');
  assert.equal(graph.edges.find((e) => e.action_class === 'press').control, 'enter');

  const start = graph.nodes.find((n) => n.key === graph.start[0]);
  assert.equal(start.app_role, 'pdf');
  assert.deepEqual(start.signature, ['doc:d1']);
  const held = graph.nodes.find((n) => n.key === paste.to);
  assert.deepEqual(held.signature, ['fact:f1', 'field:amount']);
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
  const expect = createHash('sha1').update('accounting|accounting|fact:f1,field:amount').digest('hex').slice(0, 16);
  assert.equal(stateKey('accounting', 'accounting', ['field:amount', 'fact:f1']), expect);
});

test('the compile report fails a graph that carries a recorded value, a title or a non-vocabulary name', () => {
  const { graph } = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  const leaky = { ...graph, edges: graph.edges.map((e, i) => (i === 0 ? { ...e, control: 'Bills - ACME Corp' } : i === 1 ? { ...e, control: 'Memo' } : e)) };
  const report = compileReport(leaky, { events: invoiceEvents(), files });
  assert.equal(report.leakage.ok, false);
  const reasons = new Set(report.leakage.failures.map((f) => f.reason));
  assert.ok(reasons.has('window_title') && reasons.has('non_vocab_name'));
  assert.ok(!JSON.stringify(report.leakage.failures).includes('ACME'));
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
  assert.deepEqual(merged.vocabulary.words, ['amount']);
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
