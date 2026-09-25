// The plan graph, v3 (design of record: docs/computer_use_system.md §3): a recording
// compiled into *frames* — L0 progress sets — and the typed moves between them.
//
// A node is its L0 set: `have:<slot>` (a field on this screen holds a value), `read:<slot>`
// (a fact was read), `open:<slot>` (a document is open), `in:<screen-class>` (URL path shape
// + landmark roles, or title shape on desktop, hashed) and `ctx:<dialog-class>` (a modal is
// up). Two frames are one node iff their L0 sets are equal; L1 (landmarks, modal, primary
// button, control classes) is context on the node and never identity. Slots are aligned on
// device before the graph exists: (1) transfer linkage — copied from control A and pasted
// into B is one slot named from the source; (2) declared inputs, by value equality across
// the recording; (3) leftover typed controls clustered by descriptor → `field:{descriptor}`;
// (4) the employee's storyboard rename (plan-edits). An edge is
// primitive × control descriptor × slot × policy × irreversibility class × stats × provenance;
// typing is one edge keyed on its commit. Values, titles, URLs, coordinates, selectors and
// clipboard text never enter the graph — they stay in anchors.json beside it, which never
// leaves this computer. Control names pass through the one normaliser (normalise.js).
// Deterministic, and the key functions match src/taskmining/state.py byte for byte.
import { createHash } from 'node:crypto';

import { Vocabulary, checkLeakage, keyName, normalise, recordingContext } from './normalise.js';
import { redactText } from './redact.js';
import { appRole } from './workflows.js';

export const PLAN_FILE = 'plan.json';
export const ANCHORS_FILE = 'anchors.json';
export const PLAN_EDITS_FILE = 'plan-edits.json';
export const COMPILED_BY = 'recorder-plan/3';
export const MAX_NODES = 200;
export const MAX_EDGES = 600;
export const MAX_L0 = 40;
export const UNDER_SEGMENTED_OUT = 6; // a node with more out-edges than this is counted as under-segmented
const MAX_PROVENANCE = 50;
/** Keystrokes kept on one `type_value` edge: every key with its offset from the first, in order. */
export const MAX_KEYS = 2000;
const STATE_WINDOW_MS = 1500; // a `state` frame this close before a click describes the control under it
// A frame holds at most this many of each; bounded like working memory.
const HELD = { open: 8, have: 20, read: 8 };
const last = (arr, n) => arr.slice(Math.max(0, arr.length - n));
const SUBMIT_COMBO = /\+(s|enter|return)$/i;
const PRIVATE = /private|1password|bitwarden|keychain|lastpass|keepass/i;
// Irreversibility classes, least to most; code assigns, Jev may only raise.
export const IRREVERSIBILITY = ['navigational', 'mutating', 'committing'];
export const COMMIT_VOCAB = new Set(['save', 'submit', 'send', 'post', 'delete', 'approve', 'confirm', 'pay', 'ok', 'yes', 'continue', 'apply', 'done', 'finish', 'complete']);
const NAV_CLICK_ROLES = new Set(['tab', 'link', 'row', 'menuitem', 'cell', 'treeitem', 'option']);

const h = (parts) => createHash('sha1').update(parts.join('|'), 'utf8').digest('hex').slice(0, 16);
/** v1/v2 key, kept for graphs compiled before v3. */
export const stateKey = (role, activity, signature) => h([role, activity, [...new Set(signature)].sort().join(',')]);
/** v3 key: the L0 set alone. */
export const frameKey = (l0) => h(['v3', [...new Set(l0)].sort().join(',')]);
export const edgeId = (frm, to, action, control, slot) => h([frm, to, action, control ?? '', slot ?? '']);
export const screenClass = (role, shape) => h(['screen', role, shape]);
const emptyStats = () => ({ support: 0, recorded: 0, executed: 0, verified_ok: 0, approved: 0, denied: 0, effect_missing: 0 });
export const defaultPolicy = (action, irreversibility = null) => {
  if (action === 'submit' || irreversibility === 'committing') return 'always_ask';
  if (irreversibility === 'navigational') return 'auto';
  return ['click', 'type_value', 'create_task'].includes(action) ? 'confirm' : 'auto';
};
const label = (s) => redactText(String(s ?? '')).replace(/[\x00-\x1f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 255) || null;
// A control has a name only when a vocabulary word survives; class tokens alone (`{id}`) name data, not a control.
const named = (n) => !!n && n.split(' ').some((w) => !/^\{[a-z]+\}$/.test(w));
const slug = (name) => name.replace(/[^A-Za-z0-9_.-]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '').slice(0, 120);

/**
 * Irreversibility class from what code can see: the primitive, the control descriptor and
 * whether a confirm dialog is up. Jev's `p_irreversible` may raise this later, never lower it.
 */
export function irreversibilityOf(action, descriptor = null, { ctx = null } = {}) {
  if (action === 'submit') return 'committing';
  if (['navigate', 'read', 'extract', 'wait', 'http_get'].includes(action)) return 'navigational';
  if (action === 'click' || action === 'press') {
    const words = String(descriptor?.name ?? '').split(' ');
    if (words.some((w) => COMMIT_VOCAB.has(w))) return 'committing';
    if (ctx === 'confirm' && action === 'press' && /enter|return/.test(descriptor?.name ?? '')) return 'committing';
    if (action === 'click' && (NAV_CLICK_ROLES.has(descriptor?.role) || !descriptor?.role || descriptor.role === 'unknown')) return 'navigational';
    return 'mutating';
  }
  return 'mutating'; // type_value, select, press into a field, create_task
}

// URL path shape: identifiers, numbers and hashes in the path become `{id}`; the query is dropped.
export function pathShape(url) {
  let path;
  try {
    path = new URL(String(url)).pathname;
  } catch {
    return null;
  }
  return path
    .split('/')
    .map((seg) => (/\d/.test(seg) || /^[0-9a-f]{8,}$/i.test(seg) || seg.length > 24 ? '{id}' : seg.toLowerCase()))
    .join('/')
    .replace(/\/+$/, '') || '/';
}

// Title shape on desktop: the normalised title — every data word already a class token.
export function titleShape(title, vocab) {
  return normalise(label(title) ?? '', vocab) || '{text}';
}

/** Events the compiler may look at: in time, not struck out, not in a private app. */
function visibleEvents(events, hidden) {
  return events.filter((e) => {
    const t = Date.parse(e.timestamp);
    if (!Number.isFinite(t) || hidden.some(([a, b]) => t >= a && t < b)) return false;
    return !!e.app && !PRIVATE.test(e.app) && appRole(e.app, e.window_title) !== 'password_manager';
  });
}

/** The recording's control vocabulary: element names that recur across (app, window) screens. */
export function recordingVocabulary(events, { extra = [] } = {}) {
  const screens = new Map();
  const titles = new Map(); // app -> last title seen, for events written without one
  for (const e of events) {
    if (e.window_title) titles.set(e.app, e.window_title);
    const k = `${e.app}\0${e.window_title || titles.get(e.app) || ''}`;
    if (!screens.has(k)) screens.set(k, new Set());
    if (e.element) screens.get(k).add(String(e.element));
    // Sidecar frames carry the screen's candidates: the same AX names, from the same observe().
    if (e.event_type === 'state') for (const c of e.payload?.candidates ?? []) if (c?.name) screens.get(k).add(String(c.name));
  }
  return Vocabulary.build([...screens.values()], { extra });
}

/**
 * Everything the recording saw that must not appear in anything cloud-bound: typed and
 * clipboard text, URLs, titles, file names and paths, the intent text. Element names are
 * not values — they face the vocabulary rule instead — and key combos name no data.
 */
export function recordingValues(events, files = [], manifest = {}) {
  const values = new Set(), titles = new Set();
  for (const e of events) {
    if (e.text && !keyName(e.text)) values.add(String(e.text));
    if (e.url) values.add(String(e.url));
    if (e.window_title) titles.add(String(e.window_title));
    for (const k of ['clip_hash', 'source_title', 'text']) if (e.payload?.[k]) values.add(String(e.payload[k]));
  }
  for (const f of files) for (const k of ['name', 'path']) if (f[k]) values.add(String(f[k]));
  if (manifest.summary_text) values.add(String(manifest.summary_text));
  return { values: [...values], titles: [...titles] };
}

class Graph {
  constructor(recordingId) {
    this.recordingId = recordingId;
    this.nodes = new Map();
    this.edges = new Map();
    this.anchors = {};
    this.screens = {}; // screen class -> local shape (anchors only)
    this.start = null;
    this.truncated = false;
  }

  node(role, l0, l1) {
    const set = [...new Set(l0)].sort();
    const key = frameKey(set);
    if (!this.nodes.has(key)) {
      if (this.nodes.size >= MAX_NODES) return null;
      this.nodes.set(key, { key, app_role: role, activity: role, signature: set, l0: set, l1, terminal: false });
    }
    if (!this.start) this.start = key;
    return key;
  }

  move(frm, to, action, { control = null, descriptor = null, slot = null, irreversibility = null, produces = [], effect = [], events = [], anchor = null, commit = null, keys = null }) {
    const id = edgeId(frm, to, action, control, slot);
    let e = this.edges.get(id);
    if (!e) {
      if (this.edges.size >= MAX_EDGES) return null;
      const cls = irreversibility ?? irreversibilityOf(action, descriptor);
      e = {
        id, frm, to, action_class: action, control, slot,
        descriptor, irreversibility: cls, commit,
        produces: [...produces], effect: [...effect], stats: emptyStats(),
        provenance: [{ source: 'recording', id: this.recordingId, event_ids: [] }],
        policy: defaultPolicy(action, cls), anchor_ref: `${this.recordingId}:${id}`,
        ...(keys ? { keys } : {}),
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

// One recorded keystroke as the graph keeps it: the key (a typed character or a key name;
// `•` where the recorder masked it in a sign-in, payment or private window) and its offset
// from the first key of the run. Uploaded with the graph: the employee chose to share how
// the field was typed so a run can reproduce it.
function keyOf(e) {
  if (e.payload?.masked) return { key: '•', masked: true };
  const k = e.text ? String(e.text) : e.payload?.key ? String(e.payload.key) : '';
  return { key: k.slice(0, 32) || '•', masked: !k };
}
function pushKey(keys, t0, t, key) {
  if (!keys || keys.length >= MAX_KEYS || !key) return;
  const k = typeof key === 'string' ? { key, masked: false } : key;
  keys.push({ t: Math.max(0, Math.round(t - t0)), key: k.key, ...(k.masked ? { masked: true } : {}) });
}

// Documents open in `app` at `t`, as ordinal names in order of first appearance.
function docsOpen(files, app, t) {
  const out = [];
  files.forEach((f, i) => {
    if ((f.intervals ?? []).some((iv) => iv.app === app && iv.start <= t && t <= iv.end)) out.push(`doc:d${i + 1}`);
  });
  return out;
}

// A control descriptor: (role, normalised name, landmark, position class). Never a raw string.
function describe(candidate, control, vocab) {
  const name = candidate?.name ? normalise(label(candidate.name) ?? '', vocab) : control;
  return {
    role: candidate?.role ?? (control ? 'textbox' : 'unknown'),
    name: named(name) ? name : null,
    landmark: candidate?.landmark || null,
    position: candidate?.position || null,
  };
}

/**
 * Compile one recording's events into a plan graph plus the local anchors that
 * let a run find the same controls again. `events` are the parsed events.jsonl
 * lines in order (their index is the provenance id); `excluded` are time ranges
 * the employee struck out and `files` the tracked documents (files.json).
 */
export function compilePlan({ recordingId, events = [], files = [], excluded = [], manifest = {}, vocabularyExtra = [] }) {
  const g = new Graph(recordingId);
  const hidden = [...excluded, ...(manifest.pauses ?? [])]
    .map((r) => [Date.parse(r.start), Date.parse(r.end ?? manifest.ended_at)])
    .filter(([a, b]) => Number.isFinite(a) && Number.isFinite(b));
  const visible = visibleEvents(events, hidden);
  const vocab = recordingVocabulary(visible, { extra: vocabularyExtra });
  const controls = new Map(); // raw element name -> ordinal, for names outside the vocabulary
  const slots = new Map(); // slot -> { method, controls:Set, single_recording }
  const declareSlot = (slot, method, control = null) => {
    if (!slots.has(slot)) slots.set(slot, { method, controls: new Set(), single_recording: method === 'descriptor' });
    if (control) slots.get(slot).controls.add(control);
  };
  // Rule 3: a filled control's identity — `field:{descriptor}` when the name is vocabulary, else an ordinal.
  const controlOf = (element) => {
    const raw = label(element);
    if (!raw) return { control: null, token: null };
    const name = normalise(raw, vocab);
    if (named(name)) return { control: name, token: `field:${slug(name)}` };
    if (!controls.has(raw)) controls.set(raw, controls.size + 1);
    return { control: null, token: `field:c${controls.get(raw)}` };
  };
  const have = new Map(); // app -> have: tokens, oldest first
  const read = new Map(); // clip hash -> fact slot, oldest first (rule 1: named from the source control)
  const typedValues = new Map(); // text -> input slot (rule 2: declared inputs by value equality)
  let cur = null; // current node key
  let app = null;
  let role = null;
  let screen = null; // { cls, shape } of the current window
  let ctx = null; // dialog class from the last sidecar frame
  let l1 = {};
  let lastState = null; // last `state` event: { t, payload }
  let lastClip = null;
  let typing = null; // { events, anchor, text, element, keys, t0 } pending key run
  let clicks = null; // { events, anchor, state } pending click run
  let inputs = 0;

  const fill = (token) => {
    const held = (have.get(app) ?? []).filter((x) => x !== token);
    held.push(token);
    have.set(app, held);
  };
  const l0 = (t) => [
    ...(screen ? [`in:${screen.cls}`] : []),
    ...(ctx ? [`ctx:${ctx}`] : []),
    ...last(docsOpen(files, app, t), HELD.open).map((d) => `open:${d}`),
    ...last(have.get(app) ?? [], HELD.have).map((f) => `have:${f}`),
    ...last([...read.values()], HELD.read).map((f) => `read:${f}`),
  ].slice(0, MAX_L0);
  const setScreen = (e) => {
    const shape = e.url ? pathShape(e.url) : null;
    const s = shape ?? titleShape(e.window_title, vocab);
    const marks = lastState && lastState.app === e.app ? lastState.payload.l1?.landmarks ?? [] : [];
    const cls = lastState && lastState.app === e.app && lastState.payload.screen_class ? lastState.payload.screen_class : screenClass(role, `${s}|${[...marks].sort().join(',')}`);
    screen = { cls, shape: s };
    g.screens[cls] = { role, shape: s, landmarks: [...marks].sort() };
  };
  const advance = (action, opts, t) => {
    if (!cur || g.truncated) return;
    const to = g.node(role, l0(t), l1);
    if (to === null) { g.truncated = true; return; }
    if (g.move(cur, to, action, opts) === null) g.truncated = true;
    cur = to;
  };
  const underPointer = (t) => (lastState && t - lastState.t <= STATE_WINDOW_MS ? lastState.payload.under_pointer : null);
  const flushTyping = (commit = null, commitEvent = null, commitAt = null, commitKey = null) => {
    if (!typing) return;
    const { events: ids, anchor, text, element, keys, t0 } = typing;
    typing = null;
    if (commitEvent !== null) {
      ids.push(commitEvent);
      pushKey(keys, t0, commitAt ?? t0, commitKey ?? commit);
    }
    let slot;
    if (text && typedValues.has(text)) slot = typedValues.get(text);
    else {
      inputs += 1;
      slot = `input_${inputs}`;
      if (text) typedValues.set(text, slot);
    }
    const { control, token } = controlOf(element);
    const effect = token ?? `field:${slot}`;
    declareSlot(slot, 'declared', control);
    fill(effect);
    const descriptor = describe(null, control, vocab);
    advance('type_value', { control, descriptor, slot, effect: [`have:${effect}`], events: ids, anchor, commit, keys }, anchor.t);
  };
  const flushClicks = () => {
    if (!clicks) return;
    const { events: ids, anchor, candidate } = clicks;
    clicks = null;
    const { control } = controlOf(candidate?.name ?? null);
    const descriptor = describe(candidate, control, vocab);
    advance('click', { control: descriptor.name, descriptor, irreversibility: irreversibilityOf('click', descriptor, { ctx }), events: ids, anchor }, anchor.t);
  };
  const flush = () => { flushTyping(); flushClicks(); };
  const anchorOf = (e, t, extra = {}) => ({ t, app: e.app, window_title: e.window_title ?? '', url: e.url ?? '', element: e.element ?? '', ...extra });

  const index = new Map(events.map((e, i) => [e, i]));
  for (const e of visible) {
    const i = index.get(e);
    const t = Date.parse(e.timestamp);
    if (e.event_type === 'state') {
      lastState = { t, app: e.app, payload: e.payload ?? {} };
      ctx = lastState.payload.l0?.find((x) => x.startsWith('ctx:'))?.slice(4) ?? null;
      l1 = { landmarks: lastState.payload.l1?.landmarks ?? [], modal: !!lastState.payload.l1?.modal, primary: lastState.payload.l1?.primary ?? null, controls: lastState.payload.l1?.controls ?? [] };
      if (lastState.payload.screen_class && screen && screen.cls !== lastState.payload.screen_class && e.app === app) {
        // The sidecar's class replaces the URL/title guess. A frame no move has touched yet is
        // simply re-keyed; otherwise the correction is a settle (`wait`) move.
        screen = { ...screen, cls: lastState.payload.screen_class };
        g.screens[screen.cls] = g.screens[screen.cls] ?? { role, shape: screen.shape, landmarks: l1.landmarks };
        if (cur && ![...g.edges.values()].some((x) => x.frm === cur || x.to === cur)) {
          const wasStart = g.start === cur;
          g.nodes.delete(cur);
          if (wasStart) g.start = null;
          cur = g.node(role, l0(t), l1);
        } else advance('wait', { descriptor: { role: 'screen', name: null, landmark: null, position: null }, events: [i], anchor: anchorOf(e, t) }, t);
      }
      continue;
    }
    const newWindow = e.app !== app || (e.event_type === 'focus' && (e.url || e.window_title) && (e.url ? pathShape(e.url) !== screen?.shape : titleShape(e.window_title, vocab) !== screen?.shape));
    if (newWindow) {
      flush();
      const switched = e.app !== app;
      app = e.app;
      role = appRole(app, e.window_title);
      if (switched) { ctx = null; l1 = {}; }
      setScreen(e);
      if (!cur) cur = g.node(role, l0(t), l1);
      else advance('navigate', { descriptor: { role: 'screen', name: null, landmark: null, position: null }, events: [i], anchor: anchorOf(e, t) }, t);
      if (e.event_type === 'focus') continue;
    }
    switch (e.event_type) {
      case 'focus':
        break;
      case 'key': {
        flushClicks();
        const key = e.payload?.key;
        if (key === 'Enter' || key === 'Return' || key === 'Tab') {
          if (typing) { flushTyping(keyName(key), i, t, String(key)); break; }
          const descriptor = { role: 'key', name: keyName(key), landmark: null, position: null };
          advance('press', { control: descriptor.name, descriptor, irreversibility: irreversibilityOf('press', descriptor, { ctx }), events: [i], anchor: anchorOf(e, t) }, t);
          break;
        }
        if (key && key !== 'Backspace') {
          flushTyping();
          const descriptor = { role: 'key', name: keyName(key) ?? 'key', landmark: null, position: null };
          advance('press', { control: descriptor.name, descriptor, events: [i], anchor: anchorOf(e, t) }, t);
          break;
        }
        if (!typing) typing = { events: [], anchor: anchorOf(e, t), text: '', element: e.element ?? lastState?.payload?.under_pointer?.name ?? null, keys: [], t0: t };
        if (typing.events.length < MAX_PROVENANCE) typing.events.push(i);
        pushKey(typing.keys, typing.t0, t, keyOf(e));
        if (key === 'Backspace') typing.text = typing.text.slice(0, -1);
        else if (e.text) typing.text += String(e.text);
        break;
      }
      case 'click':
        flushTyping();
        {
          const candidate = underPointer(t);
          // Clicks are one move only while they land on the same control in quick succession.
          if (clicks && (t - clicks.last > STATE_WINDOW_MS || JSON.stringify(candidate) !== JSON.stringify(clicks.candidate))) flushClicks();
          if (!clicks) clicks = { events: [], anchor: anchorOf(e, t, { x: e.payload?.x, y: e.payload?.y, points: [] }), candidate };
          clicks.last = t;
        }
        if (clicks.events.length < MAX_PROVENANCE) clicks.events.push(i);
        if (clicks.anchor.points.length < MAX_PROVENANCE) clicks.anchor.points.push([e.payload?.x, e.payload?.y]);
        break;
      case 'copy': {
        flush();
        const hash = e.payload?.clip_hash || `e${i}`;
        const { control } = controlOf(e.element);
        if (!read.has(hash)) read.set(hash, control ? `fact:${slug(control)}` : `fact:f${read.size + 1}`);
        const fact = read.get(hash);
        declareSlot(fact, 'transfer', control);
        lastClip = { fact, hash };
        advance('read', { control, descriptor: describe(null, control, vocab), slot: fact, produces: [fact], effect: [`read:${fact}`], events: [i], anchor: anchorOf(e, t) }, t);
        break;
      }
      case 'paste': {
        flush();
        const hash = e.payload?.clip_hash;
        const fact = hash && read.has(hash) ? read.get(hash) : lastClip?.fact ?? null;
        let slot = fact;
        if (!slot) { inputs += 1; slot = `input_${inputs}`; declareSlot(slot, 'declared'); }
        const { control, token } = controlOf(e.element);
        const effect = token ?? `field:${slot}`;
        if (fact) declareSlot(fact, 'transfer', control);
        fill(effect);
        advance('type_value', { control, descriptor: describe(null, control, vocab), slot, effect: [`have:${effect}`], events: [i], anchor: anchorOf(e, t), commit: 'paste' }, t);
        break;
      }
      case 'shortcut': {
        flush();
        const combo = keyName(e.text) ?? 'shortcut';
        const descriptor = { role: 'key', name: combo, landmark: null, position: null };
        if (SUBMIT_COMBO.test(combo)) {
          const cleared = last(have.get(app) ?? [], HELD.have).map((f) => `have:${f}`);
          have.set(app, []);
          advance('submit', { control: combo, descriptor, effect: cleared, events: [i], anchor: anchorOf(e, t) }, t);
        } else advance('press', { control: combo, descriptor, events: [i], anchor: anchorOf(e, t) }, t);
        break;
      }
      case 'done':
        flush();
        break;
      default:
        break;
    }
  }
  flush();
  let goal = null;
  if (cur && g.nodes.has(cur)) {
    const n = g.nodes.get(cur);
    n.terminal = true;
    // The goal frame: the terminal L0, with draft `present` criteria over what it holds.
    goal = { node: cur, l0: n.l0, criteria: n.l0.filter((x) => x.startsWith('have:') || x.startsWith('read:')).map((x) => ({ type: 'present', slot: x.replace(/^(have|read):/, ''), source: 'draft' })) };
  }
  const slotTable = [...slots.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([slot, s]) => ({ slot, method: s.method, controls: [...s.controls].sort(), single_recording: s.single_recording || s.controls.size === 0 }));
  const graph = { ...g.toJSON(), vocabulary: vocab.toJSON(), slot_table: slotTable, ...(goal ? { goal } : {}) };
  const report = compileReport(graph, { events: visible, files, manifest, vocab });
  return { graph, anchors: { recording_id: recordingId, edges: g.anchors, screens: g.screens }, report };
}

/**
 * The compile report: what the FDE reads instead of a diagram of hashed keys — the vocabulary
 * the graph may speak, the slot alignment method per slot, the under-segmentation count and
 * the leakage check of the graph against every value, element name, URL and title the
 * recording saw. `ok: false` means the graph must not leave the device.
 */
export function compileReport(graph, { events = [], files = [], manifest = {}, vocab = null } = {}) {
  const { values, titles } = recordingValues(events, files, manifest);
  const v = vocab ?? new Vocabulary({ words: graph.vocabulary?.words ?? [], extra: graph.vocabulary?.extra ?? [] });
  const ctx = recordingContext({ values, titles, vocab: v });
  const { vocabulary: _omit, ...checked } = graph; // the vocabulary lists its own words by design
  const leakage = checkLeakage(checked, ctx);
  const out = new Map();
  for (const e of graph.edges) out.set(e.frm, (out.get(e.frm) ?? 0) + 1);
  const classes = {};
  for (const e of graph.edges) if (e.irreversibility) classes[e.irreversibility] = (classes[e.irreversibility] ?? 0) + 1;
  return {
    compiled_by: graph.compiled_by,
    states: graph.nodes.length,
    moves: graph.edges.length,
    vocabulary: v.toJSON(),
    slot_table: graph.slot_table ?? [],
    irreversibility: classes,
    under_segmented: [...out.values()].filter((n) => n > UNDER_SEGMENTED_OUT).length,
    goal: graph.goal ? { criteria: graph.goal.criteria.length } : null,
    leakage: { ok: leakage.ok, strings_checked: leakage.strings_checked, failures: leakage.failures, known_limits: leakage.known_limits },
    values_checked: ctx.values.size,
    titles_checked: ctx.titles.size,
  };
}

/** Union of graphs: nodes by key, edges by id, stats summed, provenance appended, stricter policy kept. */
const byProvenance = (a, b) => (a.source < b.source ? -1 : a.source > b.source ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
const stricterClass = (a, b) => IRREVERSIBILITY[Math.max(IRREVERSIBILITY.indexOf(a ?? 'navigational'), IRREVERSIBILITY.indexOf(b ?? 'navigational'))];
export function mergeGraphs(graphs) {
  const order = ['auto', 'confirm', 'always_ask'];
  const nodes = new Map();
  const edges = new Map();
  const start = new Set();
  const slots = new Map();
  let trajectories = 0;
  let truncated = false;
  let goal = null;
  for (const g of graphs) {
    trajectories += g.trajectories ?? 0;
    truncated = truncated || !!g.truncated;
    for (const s of g.start ?? []) start.add(s);
    for (const n of g.nodes ?? []) {
      const cur = nodes.get(n.key);
      if (!cur) nodes.set(n.key, { ...n, signature: [...n.signature].sort(), ...(n.l0 ? { l0: [...n.l0].sort() } : {}), terminal: !!n.terminal });
      else cur.terminal = cur.terminal || !!n.terminal;
    }
    for (const e of g.edges ?? []) {
      const cur = edges.get(e.id);
      if (!cur) {
        edges.set(e.id, { ...e, produces: [...e.produces].sort(), effect: [...e.effect].sort(), stats: { ...emptyStats(), ...e.stats }, provenance: [...e.provenance].sort(byProvenance).slice(0, MAX_PROVENANCE), policy: e.policy ?? defaultPolicy(e.action_class, e.irreversibility) });
        continue;
      }
      for (const k of Object.keys(cur.stats)) cur.stats[k] += e.stats?.[k] ?? 0;
      cur.provenance = [...cur.provenance, ...e.provenance].sort(byProvenance).slice(0, MAX_PROVENANCE);
      if (e.anchor_ref && (!cur.anchor_ref || e.anchor_ref < cur.anchor_ref)) cur.anchor_ref = e.anchor_ref;
      cur.policy = order[Math.max(order.indexOf(cur.policy), order.indexOf(e.policy ?? defaultPolicy(e.action_class, e.irreversibility)))];
      if (cur.irreversibility || e.irreversibility) cur.irreversibility = stricterClass(cur.irreversibility, e.irreversibility);
      cur.produces = [...new Set([...cur.produces, ...e.produces])].sort();
      cur.effect = [...new Set([...cur.effect, ...e.effect])].sort();
    }
    for (const s of g.slot_table ?? []) {
      const cur = slots.get(s.slot);
      if (!cur) slots.set(s.slot, { ...s, controls: [...s.controls] });
      else { cur.controls = [...new Set([...cur.controls, ...s.controls])].sort(); cur.single_recording = false; }
    }
    if (g.goal && (!goal || g.goal.node < goal.node)) goal = g.goal;
  }
  const by = (k) => (a, b) => (a[k] < b[k] ? -1 : a[k] > b[k] ? 1 : 0);
  const vocabularies = graphs.map((g) => g.vocabulary).filter(Boolean);
  const vocabulary = vocabularies.length
    ? {
        method: vocabularies[0].method,
        min_screens: Math.min(...vocabularies.map((v) => v.min_screens)),
        screens: vocabularies.reduce((n, v) => n + (v.screens ?? 0), 0),
        words: [...new Set(vocabularies.flatMap((v) => v.words ?? []))].sort(),
        extra: [...new Set(vocabularies.flatMap((v) => v.extra ?? []))].sort(),
      }
    : null;
  if (vocabulary) vocabulary.size = vocabulary.words.length + vocabulary.extra.length;
  return {
    start: [...start].sort(),
    nodes: [...nodes.values()].sort(by('key')),
    edges: [...edges.values()].sort(by('id')),
    trajectories,
    truncated,
    compiled_by: graphs.find((g) => g.compiled_by)?.compiled_by ?? COMPILED_BY,
    ...(vocabulary ? { vocabulary } : {}),
    ...(slots.size ? { slot_table: [...slots.values()].sort(by('slot')) } : {}),
    ...(goal ? { goal } : {}),
  };
}

/**
 * The employee's review of the graph: `{ [edgeId]: { excluded?: true, policy?: 'always_ask' } }`
 * plus `slots: { [oldSlot]: newSlot }` (rule 4: the storyboard rename/merge). An excluded move is
 * dropped (its dangling states with it); a policy edit can only make a move ask a person more
 * often, never less; a slot rename applies to edges, node L0 sets and the slot table alike.
 */
export function applyPlanEdits(graph, edits = {}) {
  const order = ['auto', 'confirm', 'always_ask'];
  const { slots: renames = {}, ...edgeEdits } = edits;
  const rename = (slot) => (slot && renames[slot] ? String(renames[slot]).slice(0, 120) : slot);
  const renameToken = (tok) => { const m = /^(have|read|open):(.+)$/.exec(tok); return m ? `${m[1]}:${rename(m[2])}` : tok; };
  const edges = graph.edges
    .filter((e) => !edgeEdits[e.id]?.excluded)
    .map((e) => {
      const p = edgeEdits[e.id]?.policy;
      const out = p && order.indexOf(p) > order.indexOf(e.policy) ? { ...e, policy: p } : { ...e };
      if (Object.keys(renames).length) {
        out.slot = rename(out.slot);
        out.produces = out.produces.map(rename);
        out.effect = out.effect.map(renameToken);
      }
      return out;
    });
  const used = new Set([...graph.start, ...edges.flatMap((e) => [e.frm, e.to])]);
  const nodes = graph.nodes.filter((n) => used.has(n.key)).map((n) => (Object.keys(renames).length && n.l0 ? { ...n, l0: n.l0.map(renameToken), signature: n.signature.map(renameToken) } : n));
  const slots = graph.slot_table ? Object.values(graph.slot_table.reduce((acc, s) => { const k = rename(s.slot); acc[k] = acc[k] ? { ...acc[k], controls: [...new Set([...acc[k].controls, ...s.controls])].sort(), method: 'storyboard', single_recording: false } : { ...s, slot: k, ...(k !== s.slot ? { method: 'storyboard' } : {}) }; return acc; }, {})).sort((a, b) => (a.slot < b.slot ? -1 : 1)) : undefined;
  return { ...graph, edges, nodes, ...(slots ? { slot_table: slots } : {}) };
}

/** What crosses the upload boundary, in words: counts and the roles involved, nothing else. */
export function planSummary(graph) {
  if (!graph?.nodes?.length) return null;
  const roles = [...new Set(graph.nodes.map((n) => n.app_role))].sort();
  const actions = {};
  for (const e of graph.edges) actions[e.action_class] = (actions[e.action_class] ?? 0) + 1;
  const inputs = [...new Set(graph.edges.map((e) => e.slot).filter((s) => s && s.startsWith('input_')))].length;
  const committing = graph.edges.filter((e) => e.irreversibility === 'committing').length;
  return { states: graph.nodes.length, moves: graph.edges.length, roles, actions, inputs, submits: actions.submit ?? 0, committing, truncated: !!graph.truncated };
}
