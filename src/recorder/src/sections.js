// Split a recording into "video sections": the long stretches of work in one
// window, derived from the recorder's own `focus` events. Quick hops (checking
// a notification, an Alt+Tab past something) are folded into the section they
// interrupt so the employee sees a handful of meaningful spans, not every flick.
//
// Pure functions over parsed events.jsonl rows so they are unit-testable and
// reusable by the dashboard, the clarifying-question prompt and auto-naming.

export const SECTION_DEFAULTS = {
  minSeconds: 45, // spans shorter than this are absorbed by a neighbour
  maxSections: 8, // keep only the largest N (by duration)
  mergeSameWindowGapSeconds: 5, // A → B → A with B this short is treated as one A span
};

const iso = (ms) => new Date(ms).toISOString();

export function parseEvents(text) {
  const out = [];
  for (const line of String(text).split('\n')) {
    const s = line.trim();
    if (!s) continue;
    try {
      out.push(JSON.parse(s));
    } catch {
      /* skip corrupt line */
    }
  }
  return out;
}

// Raw focus spans: one per focus event, closed by the next focus event (or the
// end of the recording). Pauses are cut out so a lunch break is not "Excel".
export function focusSpans(events, { startedAt, endedAt, pauses = [] } = {}) {
  const focus = events.filter((e) => e.event_type === 'focus').sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  const t0 = startedAt ? Date.parse(startedAt) : focus.length ? Date.parse(focus[0].timestamp) : 0;
  const tEnd = endedAt ? Date.parse(endedAt) : events.length ? Math.max(...events.map((e) => Date.parse(e.timestamp))) : t0;
  const spans = [];
  for (let i = 0; i < focus.length; i++) {
    const s = Date.parse(focus[i].timestamp);
    const e = i + 1 < focus.length ? Date.parse(focus[i + 1].timestamp) : tEnd;
    if (e <= s) continue;
    for (const piece of cutPauses(s, e, pauses)) {
      spans.push({ start: piece[0], end: piece[1], app: focus[i].app || '', title: focus[i].window_title || '', url: focus[i].url || '' });
    }
  }
  return { spans, t0, tEnd };
}

function cutPauses(s, e, pauses) {
  let pieces = [[s, e]];
  for (const p of pauses) {
    const ps = Date.parse(p.start), pe = Date.parse(p.end ?? p.start);
    if (!(pe > ps)) continue;
    const next = [];
    for (const [a, b] of pieces) {
      if (pe <= a || ps >= b) next.push([a, b]);
      else {
        if (ps > a) next.push([a, ps]);
        if (pe < b) next.push([pe, b]);
      }
    }
    pieces = next;
  }
  return pieces;
}

// Merge: (1) A→B→A with a tiny B becomes one A span; (2) spans shorter than
// minSeconds are absorbed by the longer neighbour. Then keep the largest N,
// returned in chronological order with per-section stats.
export function buildSections(events, manifest = {}, opts = {}) {
  const o = { ...SECTION_DEFAULTS, ...opts };
  const { spans, t0 } = focusSpans(events, { startedAt: manifest.started_at, endedAt: manifest.ended_at, pauses: manifest.pauses ?? [] });
  if (!spans.length) return [];
  if (opts.minSeconds == null) {
    // "quick" is relative to the session: a 5-minute session still gets a few sections
    const active = spans.reduce((s, sp) => s + (sp.end - sp.start), 0) / 1000;
    o.minSeconds = Math.min(SECTION_DEFAULTS.minSeconds, Math.max(8, Math.round(active / 20)));
  }

  // (1) same-window round trips. Nothing merges across a pause (a gap between
  // spans): a section must map to one continuous stretch of the video.
  let merged = [];
  for (const sp of spans) {
    const prev = merged[merged.length - 1];
    const prev2 = merged[merged.length - 2];
    if (prev && prev2 && sameWindow(prev2, sp) && touching(prev2, prev) && touching(prev, sp) && prev.end - prev.start <= o.mergeSameWindowGapSeconds * 1000) {
      merged.pop();
      prev2.end = sp.end;
      continue;
    }
    if (prev && sameWindow(prev, sp) && touching(prev, sp)) prev.end = sp.end;
    else merged.push({ ...sp });
  }

  // (2) absorb short spans into the longer neighbour, repeatedly
  let changed = true;
  while (changed && merged.length > 1) {
    changed = false;
    for (let i = 0; i < merged.length; i++) {
      const sp = merged[i];
      if (sp.end - sp.start >= o.minSeconds * 1000) continue;
      const left = merged[i - 1] && touching(merged[i - 1], sp) ? merged[i - 1] : null;
      const right = merged[i + 1] && touching(sp, merged[i + 1]) ? merged[i + 1] : null;
      const target = !left ? right : !right ? left : left.end - left.start >= right.end - right.start ? left : right;
      if (!target) continue;
      if (target === left) left.end = sp.end;
      else right.start = sp.start;
      merged.splice(i, 1);
      changed = true;
      break;
    }
  }
  // coalesce identical neighbours produced by absorption
  merged = merged.reduce((acc, sp) => {
    const prev = acc[acc.length - 1];
    if (prev && sameWindow(prev, sp) && touching(prev, sp)) prev.end = sp.end;
    else acc.push(sp);
    return acc;
  }, []);

  const picked = [...merged].sort((a, b) => b.end - b.start - (a.end - a.start)).slice(0, o.maxSections).sort((a, b) => a.start - b.start);
  return picked.map((sp, i) => decorate(sp, i, events, t0, manifest.pauses ?? []));
}

// Paused time before `t`. The screen video is paused together with the hooks,
// so a wall-clock instant maps to (t - t0 - pausedBefore(t)) in screen.webm.
export function pausedBefore(t, pauses = []) {
  let ms = 0;
  for (const p of pauses) {
    const ps = Date.parse(p.start), pe = Date.parse(p.end ?? p.start);
    if (pe > ps && ps < t) ms += Math.min(pe, t) - ps;
  }
  return ms;
}

const sameWindow = (a, b) => a.app === b.app && a.title === b.title;
const touching = (a, b) => a.end === b.start;

function decorate(sp, index, events, t0, pauses) {
  const inside = events.filter((e) => {
    const t = Date.parse(e.timestamp);
    return t >= sp.start && t < sp.end;
  });
  const counts = {};
  const shots = [];
  const apps = {};
  for (const e of inside) {
    counts[e.event_type] = (counts[e.event_type] ?? 0) + 1;
    if (e.event_type === 'screen' && e.payload?.image) shots.push(e.payload.image);
    if (e.app) apps[e.app] = (apps[e.app] ?? 0) + 1;
  }
  const seconds = Math.round((sp.end - sp.start) / 1000);
  return {
    index,
    id: `S${index + 1}`,
    start: iso(sp.start),
    end: iso(sp.end),
    seconds,
    offset_s: Math.max(0, Math.round((sp.start - t0 - pausedBefore(sp.start, pauses)) / 1000)), // seek position in screen.webm
    app: sp.app,
    title: sp.title,
    url: sp.url,
    counts,
    shots,
    other_apps: Object.keys(apps).filter((a) => a !== sp.app),
    name: sectionName({ start: sp.start, end: sp.end, app: sp.app, title: sp.title }),
  };
}

// ---- naming -------------------------------------------------------------------

export const shortApp = (app) => String(app ?? '').replace(/^Microsoft /, '').replace(/ Desktop$/, '').replace(/\.exe$/i, '');

const hhmm = (ms) => {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

// "09:12–09:48 · Excel — AP tracker.xlsx"
export function sectionName({ start, end, app, title }) {
  const s = typeof start === 'string' ? Date.parse(start) : start;
  const e = typeof end === 'string' ? Date.parse(end) : end;
  const what = [shortApp(app), cleanTitle(title, app)].filter(Boolean).join(' — ');
  return `${hhmm(s)}–${hhmm(e)} · ${what || 'Unknown window'}`;
}

// Drop the " - Excel" / " — Outlook" suffix apps append to their own titles.
function cleanTitle(title, app) {
  let t = String(title ?? '').trim();
  const a = shortApp(app);
  if (a) t = t.replace(new RegExp(`\\s*[-–—|]\\s*(Microsoft )?${escapeRe(a)}( Desktop)?$`, 'i'), '');
  return t.length > 60 ? t.slice(0, 57) + '…' : t;
}
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Recording name: "Tue 09:00–11:30 · Excel, Outlook, SAP" + the employee's own
// summary when they gave one ("… — Month-end AP run").
export function recordingName(manifest, { summary = '', sections = [] } = {}) {
  const s = Date.parse(manifest.started_at);
  const e = manifest.ended_at ? Date.parse(manifest.ended_at) : Date.now();
  const day = new Date(s).toLocaleDateString([], { weekday: 'short' });
  const apps = (manifest.apps ?? []).filter((a) => a.app && a.app !== '(private)').slice(0, 3).map((a) => shortApp(a.app));
  const activity = apps.length ? apps.join(', ') : sections.length ? shortApp(sections[0].app) : 'Session';
  const base = `${day} ${hhmm(s)}–${hhmm(e)} · ${activity}`;
  const sum = String(summary ?? '').trim();
  return sum ? `${base} — ${sum.length > 80 ? sum.slice(0, 77) + '…' : sum}` : base;
}
