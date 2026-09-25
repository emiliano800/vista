import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

import { applyPlanEdits, compilePlan, compileReport, edgeId, frameKey, irreversibilityOf, mergeGraphs, pathShape, planSummary, stateKey, titleShape } from '../src/plan.js';
import { Vocabulary } from '../src/normalise.js';

import { SECRETS, T0, at, ev, invoiceEvents, invoiceFiles as files } from './fixtures/invoice-recording.js';

const noCoordinates = (o) => {
  if (Array.isArray(o)) return o.every(noCoordinates);
  if (o && typeof o === 'object') return Object.entries(o).every(([k, v]) => !['x', 'y', 'points', 'url', 'window_title', 'selector'].includes(k) && noCoordinates(v));
  return true;
};

test('a recording compiles to a deterministic L0 frame graph with no values in it', () => {
  const a = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  const b = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  assert.equal(JSON.stringify(a.graph), JSON.stringify(b.graph));
  const { graph, anchors, report } = a;
  assert.equal(graph.compiled_by, 'recorder-plan/3');
  assert.equal(graph.trajectories, 1);
  assert.equal(graph.start.length, 1);
  const text = JSON.stringify(graph);
  for (const s of SECRETS) assert.ok(!text.includes(s), `graph leaks ${s}`);
  assert.ok(noCoordinates(graph), 'coordinates, urls, titles and selectors never enter the graph');
  assert.ok(JSON.stringify(anchors).includes('qbo.example'), 'anchors keep the URL locally');
  assert.ok(JSON.stringify(anchors).includes('Total'), 'anchors keep the raw control name locally');
  assert.ok(Object.values(anchors.screens).some((s) => s.shape === '/bills/new'), 'anchors keep the screen shape locally');
  assert.deepEqual(graph.vocabulary.words, ['amount'], 'only names recurring across screens are vocabulary');
  assert.equal(report.leakage.ok, true, JSON.stringify(report.leakage.failures));
  assert.ok(report.values_checked >= 5 && report.titles_checked === 3);

  // Typing is one edge keyed on its commit: the Enter after "ACM" is not a separate press.
  const actions = graph.edges.map((e) => e.action_class).sort();
  assert.deepEqual(actions, ['click', 'navigate', 'read', 'submit', 'type_value', 'type_value', 'type_value']);
  const typed = graph.edges.find((e) => e.action_class === 'type_value' && e.slot === 'input_1');
  assert.equal(typed.commit, 'enter');
  assert.equal(typed.provenance[0].event_ids.length, 4, 'three keys plus the commit are one move');
  assert.equal(typed.irreversibility, 'mutating');

  // Slot alignment rule 1: the copied Total pasted into Amount is one slot, named from the source.
  const read = graph.edges.find((e) => e.action_class === 'read');
  assert.equal(read.slot, 'fact:f1', 'the source name is not vocabulary, so the fact is an ordinal');
  assert.deepEqual(read.effect, ['read:fact:f1']);
  assert.equal(read.irreversibility, 'navigational');
  const paste = graph.edges.find((e) => e.action_class === 'type_value' && e.control === 'amount');
  assert.equal(paste.slot, 'fact:f1');
  assert.deepEqual(paste.effect, ['have:field:amount']);
  assert.deepEqual(paste.descriptor, { role: 'textbox', name: 'amount', landmark: null, position: null });
  assert.equal(paste.provenance[0].event_ids.length, 2, 'the second bill re-keys the same amount from the same frame: one move, two citations');
  const memo = graph.edges.find((e) => e.action_class === 'type_value' && e.slot === 'fact:f1' && e.control === null);
  assert.deepEqual(memo.effect, ['have:field:c2'], 'an unnamed control is an ordinal (Total was c1)');
  assert.deepEqual(graph.slot_table.map((s) => [s.slot, s.method, s.controls, s.single_recording]), [['fact:f1', 'transfer', ['amount'], false], ['input_1', 'declared', [], true]]);

  const submit = graph.edges.find((e) => e.action_class === 'submit');
  assert.equal(submit.irreversibility, 'committing');
  assert.equal(submit.policy, 'always_ask');
  assert.equal(submit.control, 'cmd+s');
  assert.deepEqual(submit.effect, ['have:field:amount', 'have:field:input_1']);
  assert.equal(graph.edges.find((e) => e.action_class === 'click').irreversibility, 'navigational', 'a click on no known control is navigational until a frame says otherwise');

  const start = graph.nodes.find((n) => n.key === graph.start[0]);
  assert.equal(start.app_role, 'pdf');
  assert.deepEqual(start.l0.filter((x) => !x.startsWith('in:')), ['open:doc:d1']);
  assert.ok(start.l0.some((x) => /^in:[0-9a-f]{16}$/.test(x)), 'every frame is in a screen class');
  const held = graph.nodes.find((n) => n.key === paste.to);
  assert.deepEqual(held.l0.filter((x) => !x.startsWith('in:')), ['have:field:amount', 'read:fact:f1']);
  assert.deepEqual(held.signature, held.l0, 'signature mirrors L0 for graph consumers');
  assert.equal(held.app_role, 'accounting');
  assert.equal(graph.nodes.filter((n) => n.terminal).length, 1);
  assert.equal(graph.goal.node, graph.nodes.find((n) => n.terminal).key);
  assert.deepEqual(graph.goal.criteria.map((c) => [c.type, c.slot]), [['present', 'field:amount'], ['present', 'field:c2'], ['present', 'fact:f1']]);
  assert.equal(report.under_segmented, 0);
  assert.deepEqual(report.irreversibility, { navigational: 3, mutating: 3, committing: 1 });

  for (const n of graph.nodes) assert.equal(n.key, frameKey(n.l0));
  for (const e of graph.edges) {
    assert.equal(e.id, edgeId(e.frm, e.to, e.action_class, e.control, e.slot));
    assert.equal(e.provenance[0].id, 'rec-1');
    assert.ok(e.provenance[0].event_ids.every((x) => /^e\d+$/.test(x)));
    assert.equal(e.stats.recorded, 1);
  }
});

test('keys match the worker: v3 frame key is sha1 of v3|sorted L0; v2 state key kept', () => {
  assert.equal(stateKey('accounting', 'accounting', ['field:amount', 'fact:f1']), createHash('sha1').update('accounting|accounting|fact:f1,field:amount').digest('hex').slice(0, 16));
  assert.equal(frameKey(['read:fact:f1', 'have:field:amount', 'in:abc']), createHash('sha1').update('v3|have:field:amount,in:abc,read:fact:f1').digest('hex').slice(0, 16));
  assert.equal(frameKey(['a', 'b']), frameKey(['b', 'a', 'a']));
});

test('screen class: URL path shape and title shape carry no identifiers', () => {
  assert.equal(pathShape('https://qbo.example/bills/48213/edit?tab=2'), '/bills/{id}/edit');
  assert.equal(pathShape('https://qbo.example/bills/new'), '/bills/new');
  assert.equal(pathShape('https://crm.example.com/deals/8f3a9c2d1e4b7a6f'), '/deals/{id}');
  assert.equal(pathShape('not a url'), null);
  const vocab = new Vocabulary({ words: ['bills'] });
  assert.equal(titleShape('Bills - ACME Corp', vocab), titleShape('Bills - Beta Ltd', vocab));
  assert.equal(titleShape('Bills - ACME Corp', vocab), 'bills {text}');
  assert.equal(titleShape('INV-1042 ACME.pdf', vocab), '{id} {text}');
});

test('irreversibility is assigned in code from primitive and descriptor', () => {
  assert.equal(irreversibilityOf('navigate'), 'navigational');
  assert.equal(irreversibilityOf('click', { role: 'link', name: 'deals' }), 'navigational');
  assert.equal(irreversibilityOf('click', { role: 'button', name: 'add line' }), 'mutating');
  assert.equal(irreversibilityOf('click', { role: 'button', name: 'save' }), 'committing');
  assert.equal(irreversibilityOf('click', { role: 'button', name: 'send {text}' }), 'committing');
  assert.equal(irreversibilityOf('type_value', { role: 'textbox', name: 'amount' }), 'mutating');
  assert.equal(irreversibilityOf('press', { role: 'key', name: 'enter' }, { ctx: 'confirm' }), 'committing');
  assert.equal(irreversibilityOf('press', { role: 'key', name: 'enter' }), 'mutating');
  assert.equal(irreversibilityOf('submit'), 'committing');
});

test('sidecar state frames give clicks a control descriptor and screens their class', () => {
  const state = (ms, app, extra) => ev(ms, 'state', app, { payload: { reason: 'click', screen_class: 'ab12cd34ef56ab12', l0: ['in:ab12cd34ef56ab12'], l1: { landmarks: ['main', 'form'], modal: false }, candidates: [{ id: '1', role: 'button', name: 'Save', kind: 'interactive', landmark: 'form', position: 'bottom' }, { id: '2', role: 'textbox', name: 'Amount', kind: 'field' }], ...extra } });
  const events = [
    ev(0, 'focus', 'QuickBooks', { window_title: 'Bills - ACME Corp', url: 'https://qbo.example/bills/new' }),
    state(100, 'QuickBooks', { under_pointer: { role: 'textbox', name: 'Amount', landmark: 'form', position: 'top' } }),
    ev(500, 'click', 'QuickBooks', { payload: { x: 1, y: 2 } }),
    state(2000, 'QuickBooks', { under_pointer: { role: 'button', name: 'Save', landmark: 'form', position: 'bottom' } }),
    ev(2500, 'click', 'QuickBooks', { payload: { x: 3, y: 4 } }),
    ev(3000, 'focus', 'QuickBooks', { window_title: 'Bills - Beta Ltd', url: 'https://qbo.example/bills/new' }),
    state(3100, 'QuickBooks', { under_pointer: null }),
    ev(3500, 'click', 'QuickBooks', { payload: { x: 5, y: 6 } }),
  ];
  const { graph, report } = compilePlan({ recordingId: 'rec-s', events, files: [] });
  assert.equal(report.leakage.ok, true, JSON.stringify(report.leakage.failures));
  assert.deepEqual(graph.vocabulary.words, ['amount', 'save'], 'candidate names recurring across frames are vocabulary');
  const clicks = graph.edges.filter((e) => e.action_class === 'click');
  const save = clicks.find((e) => e.descriptor.name === 'save');
  assert.equal(save.irreversibility, 'committing');
  assert.equal(save.policy, 'always_ask');
  assert.equal(save.descriptor.landmark, 'form');
  const amount = clicks.find((e) => e.descriptor.name === 'amount');
  assert.equal(amount.irreversibility, 'mutating');
  assert.ok(graph.nodes.every((n) => n.l0.includes('in:ab12cd34ef56ab12')), 'the sidecar screen class is the frame class');
  assert.deepEqual(graph.nodes[0].l1.landmarks, ['main', 'form']);
  assert.equal(graph.edges.filter((e) => e.action_class === 'navigate').length, 0, 'same path shape: the second bill is the same screen');
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

test('declared inputs align by value equality: the same text typed twice is one slot', () => {
  const keys = (ms, text) => [...text].map((c, i) => ev(ms + i * 50, 'key', 'QuickBooks', { text: c }));
  const events = [
    ev(0, 'focus', 'QuickBooks', { window_title: 'Bills', url: 'https://qbo.example/bills/new' }),
    ...keys(1000, 'ACME'), ev(1500, 'key', 'QuickBooks', { payload: { key: 'Tab' } }),
    ...keys(2000, 'Chairs'), ev(2500, 'key', 'QuickBooks', { payload: { key: 'Tab' } }),
    ev(3000, 'focus', 'QuickBooks', { window_title: 'Bills', url: 'https://qbo.example/bills/new' }),
    ...keys(4000, 'ACME'), ev(4500, 'key', 'QuickBooks', { payload: { key: 'Tab' } }),
  ];
  const { graph } = compilePlan({ recordingId: 'rec-v', events, files: [] });
  const typed = graph.edges.filter((e) => e.action_class === 'type_value');
  assert.deepEqual([...new Set(typed.map((e) => e.slot))].sort(), ['input_1', 'input_2']);
  assert.equal(typed.find((e) => e.slot === 'input_1').provenance[0].event_ids.length, 5, 'the repeat cites the first move (one edge from the same frame)');
  assert.ok(typed.every((e) => e.commit === 'tab'));
  assert.ok(!JSON.stringify(graph).includes('ACME') && !JSON.stringify(graph).includes('Chairs'));
});

test('a second recording of the same work merges onto the same frames and adds support', () => {
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
  assert.equal(merged.slot_table.find((s) => s.slot === 'input_1').single_recording, false, 'a slot seen in two recordings is no longer single-recording');
  assert.equal(merged.goal.node, one.goal.node);
  assert.equal(JSON.stringify(mergeGraphs([two, one])), JSON.stringify(merged), 'merge order does not matter');
});

test('review edits drop moves, make them ask (never the other way) and rename slots', () => {
  const { graph } = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  const click = graph.edges.find((e) => e.action_class === 'click');
  const read = graph.edges.find((e) => e.action_class === 'read');
  const edited = applyPlanEdits(graph, { [click.id]: { excluded: true }, [read.id]: { policy: 'always_ask' }, [graph.edges.find((e) => e.action_class === 'submit').id]: { policy: 'auto' }, slots: { 'fact:f1': 'fact:invoice_total' } });
  assert.ok(!edited.edges.some((e) => e.id === click.id));
  assert.equal(edited.edges.find((e) => e.id === read.id).policy, 'always_ask');
  assert.equal(edited.edges.find((e) => e.id === read.id).slot, 'fact:invoice_total');
  assert.deepEqual(edited.edges.find((e) => e.id === read.id).effect, ['read:fact:invoice_total']);
  assert.ok(edited.nodes.some((n) => n.l0.includes('read:fact:invoice_total')));
  assert.equal(edited.slot_table.find((s) => s.slot === 'fact:invoice_total').method, 'storyboard');
  assert.ok(edited.edges.every((e) => e.policy !== 'auto' || graph.edges.find((g) => g.id === e.id).policy === 'auto'));
  assert.ok(edited.nodes.length <= graph.nodes.length);
  const s = planSummary(edited);
  assert.equal(s.moves, graph.edges.length - 1);
  assert.deepEqual(s.roles, ['accounting', 'pdf']);
  assert.equal(s.submits, 1);
  assert.equal(s.committing, 1);
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
  for (const n of graph.nodes) assert.ok(n.l0.length <= 40);
});

test('a type_value edge carries every key of its run with its offset from the first, commit included; masked keys stay masked', () => {
  const events = invoiceEvents();
  const { graph } = compilePlan({ recordingId: 'rec-1', events, files });
  const typed = graph.edges.find((e) => e.action_class === 'type_value' && e.slot === 'input_1');
  assert.deepEqual(typed.keys, [
    { t: 0, key: 'A' },
    { t: 100, key: 'C' },
    { t: 200, key: 'M' },
    { t: 1000, key: 'Enter' },
  ]);
  assert.ok(graph.edges.filter((e) => e.action_class !== 'type_value').every((e) => !('keys' in e)), 'only typing carries keys');
  // Same keys typed on a second recording: the edge merges and keeps one script (structure is not the keys).
  const again = compilePlan({ recordingId: 'rec-2', events: invoiceEvents(), files });
  const merged = mergeGraphs([graph, again.graph]);
  const twice = merged.edges.find((e) => e.id === typed.id);
  assert.equal(twice.keys.length, 4);
  assert.equal(twice.stats.support, 2);
  // Masked keys (sign-in, payment, keyContent off) are counted and timed but never spelt out.
  const masked = events.map((e) => (e.event_type === 'key' && e.text ? { ...e, text: '', payload: { masked: true } } : e));
  const hidden = compilePlan({ recordingId: 'rec-3', events: masked, files }).graph.edges.find((e) => e.action_class === 'type_value' && e.keys?.length === 4);
  assert.deepEqual(hidden.keys.slice(0, 3), [{ t: 0, key: '•', masked: true }, { t: 100, key: '•', masked: true }, { t: 200, key: '•', masked: true }]);
  assert.ok(!JSON.stringify(hidden).includes('ACM'));
});

test('the compile report passes a key script but still fails a recorded value anywhere else', () => {
  const { graph, report } = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files });
  assert.equal(report.leakage.ok, true, JSON.stringify(report.leakage.failures));
  const typed = graph.edges.find((e) => e.keys);
  assert.ok(typed.keys.length > 0);
  const leaked = JSON.parse(JSON.stringify(graph));
  leaked.edges[0].keys = '1,250.00'; // a string in `keys` is not a key script
  const values = compileReport(leaked, { recordingId: 'rec-1', events: invoiceEvents(), files });
  assert.equal(values.leakage.ok, false);
});
