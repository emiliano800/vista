// The plan graph: a recording compiled into states and the moves between them.
//
// A state is (app role, activity, data signature) — which typed things the employee
// holds right now: a document open (`doc:d1`), a fact copied (`fact:f2`), a field
// filled (`field:input_3`). A move is one closed action class (navigate, read,
// type_value, press, submit, click) with the control it touched and the input or
// fact it used. Every move cites the event lines it came from. Values, titles, URLs,
// coordinates and clipboard text never enter the graph; they stay in the anchors
// file beside it, which never leaves this computer. Deterministic: the same
// events.jsonl always compiles to the same graph, and the key functions match
// src/taskmining/state.py byte for byte so the worker can compare states by key.
import { createHash } from 'node:crypto';

import { redactText } from './redact.js';
import { appRole } from './workflows.js';

export const PLAN_FILE = 'plan.json';
export const ANCHORS_FILE = 'anchors.json';
export const PLAN_EDITS_FILE = 'plan-edits.json';
export const COMPILED_BY = 'recorder-plan/1';
export const MAX_NODES = 200;
export const MAX_EDGES = 600;
const MAX_PROVENANCE = 50;
// A state holds at most this many of each: the most recent documents, filled fields and
// facts. Bounded like working memory, so a long session still compares by state.
const HELD = { docs: 8, fields: 20, facts: 8 };
const last = (arr, n) => arr.slice(Math.max(0, arr.length - n));
const SUBMIT_COMBO = /\+(s|enter|return)$/i;
const PRIVATE = /private|1password|bitwarden|keychain|lastpass|keepass/i;

const h = (parts) => createHash('sha1').update(parts.join('|'), 'utf8').digest('hex').slice(0, 16);
export const stateKey = (role, activity, signature) => h([role, activity, [...new Set(signature)].sort().join(',')]);
export const edgeId = (frm, to, action, control, slot) => h([frm, to, action, control ?? '', slot ?? '']);
const emptyStats = () => ({ support: 0, recorded: 0, executed: 0, verified_ok: 0, approved: 0, denied: 0, effect_missing: 0 });
const defaultPolicy = (action) => (action === 'submit' ? 'always_ask' : ['click', 'type_value', 'create_task'].includes(action) ? 'confirm' : 'auto');
const label = (s) => redactText(String(s ?? '')).replace(/[\x00-\x1f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 255) || null;

class Graph {
  constructor(recordingId) {
    this.recordingId = recordingId;
    this.nodes = new Map();
    this.edges = new Map();
    this.anchors = {};
    this.start = null;
    this.truncated = false;
  }

  node(role, activity, signature) {
    const sig = [...new Set(signature)].sort();
    const key = stateKey(role, activity, sig);
    if (!this.nodes.has(key)) {
      if (this.nodes.size >= MAX_NODES) return null;
      this.nodes.set(key, { key, app_role: role, activity, signature: sig, terminal: false });
    }
    if (!this.start) this.start = key;
    return key;
  }

  move(frm, to, action, { control = null, slot = null, produces = [], effect = [], events = [], anchor = null }) {
    const id = edgeId(frm, to, action, control, slot);
    let e = this.edges.get(id);
    if (!e) {
      if (this.edges.size >= MAX_EDGES) return null;
      e = {
        id, frm, to, action_class: action, control, slot,
        produces: [...produces], effect: [...effect], stats: emptyStats(),
        provenance: [{ source: 'recording', id: this.recordingId, event_ids: [] }],
        policy: defaultPolicy(action), anchor_ref: `${this.recordingId}:${id}`,
      };
      e.stats.support = e.stats.recorded = 1;
      this.edges.set(id, e);
      this.anchors[id] = [];
    }
    const ids = e.provenance[0].event_ids;
    for (const i of events) if (ids.length < MAX_PROVENANCE) ids.push(`e${i}`);
    if (anchor && this.anchors[id].length < MAX_PROVENANCE) this.anchors[id].push(anchor);
    return id;
  }

  toJSON() {
    const nodes = [...this.nodes.values()].sort((a, b) => (a.key < b.key ? -1 : 1));
    const edges = [...this.edges.values()].sort((a, b) => (a.id < b.id ? -1 : 1));
    return { start: this.start ? [this.start] : [], nodes, edges, trajectories: 1, truncated: this.truncated, compiled_by: COMPILED_BY };
  }
}

// Documents open in `app` at `t`, as ordinal names in order of first appearance.
function docsOpen(files, app, t) {
  const out = [];
  files.forEach((f, i) => {
    if ((f.intervals ?? []).some((iv) => iv.app === app && iv.start <= t && t <= iv.end)) out.push(`doc:d${i + 1}`);
  });
  return out;
}

/**
 * Compile one recording's events into a plan graph plus the local anchors that
 * let a run find the same controls again. `events` are the parsed events.jsonl
 * lines in order (their index is the provenance id); `excluded` are time ranges
 * the employee struck out and `files` the tracked documents (files.json).
 */
export function compilePlan({ recordingId, events = [], files = [], excluded = [], manifest = {} }) {
  const g = new Graph(recordingId);
  const hidden = [...excluded, ...(manifest.pauses ?? [])]
    .map((r) => [Date.parse(r.start), Date.parse(r.end ?? manifest.ended_at)])
    .filter(([a, b]) => Number.isFinite(a) && Number.isFinite(b));
  const filled = new Map(); // app -> field tokens, oldest first
  const facts = new Map(); // clip hash -> fact name, oldest first
  let cur = null; // current node key
  let app = null;
  let role = null;
  let lastClip = null; // { fact } for the copy the next paste may realise
  let typing = null; // { start, events, chars } pending key run
  let clicks = null; // { events } pending click run
  let inputs = 0;

  const fill = (token) => {
    const held = (filled.get(app) ?? []).filter((x) => x !== token);
    held.push(token);
    filled.set(app, held);
  };
  const sig = (t) => [
    ...last(docsOpen(files, app, t), HELD.docs),
    ...last(filled.get(app) ?? [], HELD.fields),
    ...last([...facts.values()], HELD.facts).map((f) => `fact:${f}`),
  ];
  const advance = (action, opts, t) => {
    if (!cur || g.truncated) return;
    const to = g.node(role, role, sig(t));
    if (to === null) { g.truncated = true; return; }
    if (g.move(cur, to, action, opts) === null) g.truncated = true;
    cur = to;
  };
  const flushTyping = () => {
    if (!typing) return;
    const { events: ids, anchor } = typing;
    typing = null;
    inputs += 1;
    const slot = `input_${inputs}`;
    fill(`field:${slot}`);
    advance('type_value', { slot, effect: [`field:${slot}`], events: ids, anchor }, anchor.t);
  };
  const flushClicks = () => {
    if (!clicks) return;
    const { events: ids, anchor } = clicks;
    clicks = null;
    advance('click', { events: ids, anchor }, anchor.t);
  };
  const flush = () => { flushTyping(); flushClicks(); };
  const anchorOf = (e, t, extra = {}) => ({ t, app: e.app, window_title: e.window_title ?? '', url: e.url ?? '', element: e.element ?? '', ...extra });

  events.forEach((e, i) => {
    const t = Date.parse(e.timestamp);
    if (!Number.isFinite(t) || hidden.some(([a, b]) => t >= a && t < b)) return;
    if (!e.app || PRIVATE.test(e.app) || appRole(e.app, e.window_title) === 'password_manager') return;
    if (e.app !== app) {
      flush();
      app = e.app;
      role = appRole(app, e.window_title);
      if (!cur) cur = g.node(role, role, sig(t));
      else advance('navigate', { events: [i], anchor: anchorOf(e, t) }, t);
      if (e.event_type === 'focus') return;
    }
    switch (e.event_type) {
      case 'focus':
        return;
      case 'key': {
        flushClicks();
        const key = e.payload?.key;
        if (key && key !== 'Enter' && key !== 'Return' && key !== 'Tab' && key !== 'Backspace') {
          flushTyping();
          advance('press', { control: key, events: [i], anchor: anchorOf(e, t) }, t);
          return;
        }
        if (key === 'Enter' || key === 'Return') {
          flushTyping();
          advance('press', { control: 'Enter', events: [i], anchor: anchorOf(e, t) }, t);
          return;
        }
        if (!typing) typing = { events: [], anchor: anchorOf(e, t) };
        if (typing.events.length < MAX_PROVENANCE) typing.events.push(i);
        return;
      }
      case 'click':
        flushTyping();
        if (!clicks) clicks = { events: [], anchor: anchorOf(e, t, { x: e.payload?.x, y: e.payload?.y, points: [] }) };
        if (clicks.events.length < MAX_PROVENANCE) clicks.events.push(i);
        if (clicks.anchor.points.length < MAX_PROVENANCE) clicks.anchor.points.push([e.payload?.x, e.payload?.y]);
        return;
      case 'copy': {
        flush();
        const hash = e.payload?.clip_hash || `e${i}`;
        if (!facts.has(hash)) facts.set(hash, `f${facts.size + 1}`);
        const fact = facts.get(hash);
        lastClip = { fact, hash };
        advance('read', { control: label(e.element), produces: [fact], effect: [`fact:${fact}`], events: [i], anchor: anchorOf(e, t) }, t);
        return;
      }
      case 'paste': {
        flush();
        const hash = e.payload?.clip_hash;
        const fact = hash && facts.has(hash) ? facts.get(hash) : lastClip?.fact ?? null;
        let slot = fact;
        if (!slot) { inputs += 1; slot = `input_${inputs}`; }
        const control = label(e.element);
        const token = `field:${control ? control.replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 120) : slot}`;
        fill(token);
        advance('type_value', { control, slot, effect: [token], events: [i], anchor: anchorOf(e, t) }, t);
        return;
      }
      case 'shortcut': {
        flush();
        const combo = label(e.text) ?? 'shortcut';
        if (SUBMIT_COMBO.test(combo)) {
          const cleared = last(filled.get(app) ?? [], HELD.fields);
          filled.set(app, []);
          advance('submit', { control: combo, effect: cleared, events: [i], anchor: anchorOf(e, t) }, t);
        } else advance('press', { control: combo, events: [i], anchor: anchorOf(e, t) }, t);
        return;
      }
      default:
        return;
    }
  });
  flush();
  if (cur && g.nodes.has(cur)) g.nodes.get(cur).terminal = true;
  return { graph: g.toJSON(), anchors: { recording_id: recordingId, edges: g.anchors } };
}

/** Union of graphs: nodes by key, edges by id, stats summed, provenance appended, stricter policy kept. */
const byProvenance = (a, b) => (a.source < b.source ? -1 : a.source > b.source ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
export function mergeGraphs(graphs) {
  const order = ['auto', 'confirm', 'always_ask'];
  const nodes = new Map();
  const edges = new Map();
  const start = new Set();
  let trajectories = 0;
  let truncated = false;
  for (const g of graphs) {
    trajectories += g.trajectories ?? 0;
    truncated = truncated || !!g.truncated;
    for (const s of g.start ?? []) start.add(s);
    for (const n of g.nodes ?? []) {
      const cur = nodes.get(n.key);
      if (!cur) nodes.set(n.key, { ...n, signature: [...n.signature].sort(), terminal: !!n.terminal });
      else cur.terminal = cur.terminal || !!n.terminal;
    }
    for (const e of g.edges ?? []) {
      const cur = edges.get(e.id);
      if (!cur) {
        edges.set(e.id, { ...e, produces: [...e.produces].sort(), effect: [...e.effect].sort(), stats: { ...emptyStats(), ...e.stats }, provenance: [...e.provenance].sort(byProvenance).slice(0, MAX_PROVENANCE), policy: e.policy ?? defaultPolicy(e.action_class) });
        continue;
      }
      for (const k of Object.keys(cur.stats)) cur.stats[k] += e.stats?.[k] ?? 0;
      cur.provenance = [...cur.provenance, ...e.provenance].sort(byProvenance).slice(0, MAX_PROVENANCE);
      if (e.anchor_ref && (!cur.anchor_ref || e.anchor_ref < cur.anchor_ref)) cur.anchor_ref = e.anchor_ref;
      cur.policy = order[Math.max(order.indexOf(cur.policy), order.indexOf(e.policy ?? defaultPolicy(e.action_class)))];
      cur.produces = [...new Set([...cur.produces, ...e.produces])].sort();
      cur.effect = [...new Set([...cur.effect, ...e.effect])].sort();
    }
  }
  const by = (k) => (a, b) => (a[k] < b[k] ? -1 : a[k] > b[k] ? 1 : 0);
  return {
    start: [...start].sort(),
    nodes: [...nodes.values()].sort(by('key')),
    edges: [...edges.values()].sort(by('id')),
    trajectories,
    truncated,
    compiled_by: graphs.find((g) => g.compiled_by)?.compiled_by ?? COMPILED_BY,
  };
}

/**
 * The employee's review of the graph: `{ [edgeId]: { excluded?: true, policy?: 'always_ask' } }`.
 * An excluded move is dropped (its dangling states with it); a policy edit can only make a move
 * ask a person more often, never less.
 */
export function applyPlanEdits(graph, edits = {}) {
  const order = ['auto', 'confirm', 'always_ask'];
  const edges = graph.edges
    .filter((e) => !edits[e.id]?.excluded)
    .map((e) => {
      const p = edits[e.id]?.policy;
      return p && order.indexOf(p) > order.indexOf(e.policy) ? { ...e, policy: p } : e;
    });
  const used = new Set([...graph.start, ...edges.flatMap((e) => [e.frm, e.to])]);
  return { ...graph, edges, nodes: graph.nodes.filter((n) => used.has(n.key)) };
}

/** What crosses the upload boundary, in words: counts and the roles involved, nothing else. */
export function planSummary(graph) {
  if (!graph?.nodes?.length) return null;
  const roles = [...new Set(graph.nodes.map((n) => n.app_role))].sort();
  const actions = {};
  for (const e of graph.edges) actions[e.action_class] = (actions[e.action_class] ?? 0) + 1;
  const inputs = [...new Set(graph.edges.map((e) => e.slot).filter((s) => s && s.startsWith('input_')))].length;
  return { states: graph.nodes.length, moves: graph.edges.length, roles, actions, inputs, submits: actions.submit ?? 0, truncated: !!graph.truncated };
}
