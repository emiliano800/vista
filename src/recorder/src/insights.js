// Insights over one recording, computed on this computer right after Stop:
//   - sensitive-content flags per video section (private windows, redactions,
//     sign-in / banking pages, suspected password entry) and per document,
//   - keyboard & mouse analysis (rates per app, typing bursts, re-keying, idle),
//   - trends against the employee's previous recordings,
//   - an optional one-paragraph model summary of the above.
// Everything is written to insights.json; the employee confirms or dismisses
// each flag and approves the analysis before Submit.
import { OPENAI_URL } from './explain.js';
import { shortApp } from './sections.js';

export const INSIGHTS_FILE = 'insights.json';
export const FLAG_DECISIONS = new Set(['confirmed', 'dismissed']);

export const SENSITIVE_TITLE = /\b(?:sign[ -]?in|log[ -]?in|login|password|passcode|authenticat|verify your identity|two[ -]factor|2fa|one[ -]time code|checkout|payment|billing|credit card|bank|banking|payroll|salary|tax return|medical|patient)\b/i;
export const SENSITIVE_URL = /(?:\/login|\/signin|\/auth|\/sso|\/oauth|\/checkout|\/payment|\/billing|accounts\.google|login\.microsoft|okta\.com|auth0\.com)/i;
export const REDACTION_TOKEN = /\[(?:IBAN|CARD|EMAIL|SSN|PHONE)\]/;

const ts = (e) => Date.parse(e.timestamp);
const inRange = (e, a, b) => { const t = ts(e); return t >= a && t < b; };

// A "burst" of typing: key events less than `gapMs` apart in the same window;
// Enter closes a burst.
export function typingBursts(events, gapMs = 1500) {
  const bursts = [];
  let cur = null;
  for (const e of events) {
    if (e.event_type !== 'key') continue;
    const t = ts(e);
    const enter = e.payload?.key === 'Enter';
    const same = cur && cur.app === e.app && cur.title === e.window_title;
    if (same && !cur.enter && t - cur.end <= gapMs) { cur.end = t; cur.keys += 1; cur.enter = enter; }
    else { cur = { start: t, end: t, keys: 1, app: e.app, title: e.window_title, url: e.url, enter }; bursts.push(cur); }
  }
  return bursts;
}

// Password entry is inferred, never read: a short burst (6–40 keys) that ends with
// Enter inside a sign-in / private window, or right after focusing one.
export function passwordCandidates(events) {
  const out = [];
  const focuses = events.filter((e) => e.event_type === 'focus');
  for (const b of typingBursts(events)) {
    if (!b.enter || b.keys < 6 || b.keys > 40) continue;
    const here = SENSITIVE_TITLE.test(b.title ?? '') || SENSITIVE_URL.test(b.url ?? '') || b.title === '(private)';
    const recentPrivate = focuses.some((f) => { const t = ts(f); return t <= b.start && b.start - t < 15000 && (f.window_title === '(private)' || SENSITIVE_TITLE.test(f.window_title ?? '') || SENSITIVE_URL.test(f.url ?? '')); });
    if (here || recentPrivate) out.push({ at: new Date(b.start).toISOString(), keys: b.keys, app: b.app, title: b.title });
  }
  return out;
}

let flagSeq = 0;
const mkFlag = (scope, target, kind, reason, at, extra = {}) => ({ id: `${scope}-${target}-${kind}-${++flagSeq}`, scope, [scope === 'file' ? 'file_id' : 'section_id']: target, kind, reason, at: at ?? null, severity: kind === 'password_entry' || kind === 'credential' || kind === 'card_number' || kind === 'iban' || kind === 'ssn' ? 'high' : 'medium', decision: null, ...extra });

// Flags for one video section from the events inside it.
export function sectionFlags(section, events) {
  flagSeq = 0;
  const a = Date.parse(section.start), b = Date.parse(section.end);
  const inside = events.filter((e) => inRange(e, a, b));
  const flags = [];
  const priv = inside.filter((e) => e.event_type === 'focus' && e.window_title === '(private)');
  if (priv.length) flags.push(mkFlag('section', section.id, 'private_window', `${priv.length} switch${priv.length === 1 ? '' : 'es'} to a private app (nothing recorded there)`, priv[0].timestamp, { count: priv.length }));
  const signin = inside.filter((e) => e.event_type === 'focus' && (SENSITIVE_TITLE.test(e.window_title ?? '') || SENSITIVE_URL.test(e.url ?? '')));
  if (signin.length || SENSITIVE_TITLE.test(section.title ?? '') || SENSITIVE_URL.test(section.url ?? '')) {
    const t = signin[0]?.window_title ?? section.title;
    flags.push(mkFlag('section', section.id, 'sensitive_page', `sign-in, payment or personal-data page on screen: "${String(t).slice(0, 80)}"`, signin[0]?.timestamp ?? section.start));
  }
  const redacted = inside.filter((e) => REDACTION_TOKEN.test(e.window_title ?? '') || REDACTION_TOKEN.test(e.text ?? ''));
  if (redacted.length) {
    const kinds = [...new Set(redacted.flatMap((e) => [...`${e.window_title} ${e.text}`.matchAll(/\[(IBAN|CARD|EMAIL|SSN|PHONE)\]/g)].map((m) => m[1])))];
    flags.push(mkFlag('section', section.id, 'redacted_data', `${kinds.join(', ')} redacted on this device in ${redacted.length} event${redacted.length === 1 ? '' : 's'} — may be visible in the video`, redacted[0].timestamp, { count: redacted.length }));
  }
  const clip = inside.filter((e) => (e.event_type === 'copy' || e.event_type === 'paste') && /\b(?:\d[ -]?){13,19}\b|\b\d{3}-\d{2}-\d{4}\b|\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b/.test(e.text ?? ''));
  if (clip.length) flags.push(mkFlag('section', section.id, 'card_number', `card, account or ID number copied to the clipboard (${clip.length}×)`, clip[0].timestamp));
  for (const p of passwordCandidates(inside)) flags.push(mkFlag('section', section.id, 'password_entry', `a ${p.keys}-key entry ending in Enter in "${String(p.title).slice(0, 60)}" looks like a password being typed`, p.at));
  return flags;
}

export function fileFlags(files) {
  const out = [];
  for (const f of files) {
    if (f.include === false) continue;
    flagSeq = 0;
    for (const fl of f.review?.flags ?? []) out.push({ ...mkFlag('file', f.id, fl.kind, `${f.name}: ${fl.reason}`, null), file_name: f.name, line: fl.line ?? null, source: fl.source });
  }
  return out;
}

// ---- keyboard & mouse -----------------------------------------------------------

export function inputAnalysis(events, manifest = {}) {
  const t0 = Date.parse(manifest.started_at), t1 = Date.parse(manifest.ended_at ?? manifest.started_at) || t0;
  const active = Math.max(1, (manifest.active_seconds ?? Math.round((t1 - t0) / 1000)) || 1);
  const apps = new Map();
  const totals = { click: 0, key: 0, shortcut: 0, copy: 0, paste: 0, scroll: 0, focus: 0 };
  const shortcuts = new Map();
  let lastT = t0, idleMs = 0, longestIdle = 0, idleGaps = 0;
  const sorted = [...events].sort((a, b) => ts(a) - ts(b));
  for (const e of sorted) {
    const t = ts(e);
    if (t - lastT > 60000) { idleMs += t - lastT; longestIdle = Math.max(longestIdle, t - lastT); idleGaps += 1; }
    lastT = t;
    if (e.event_type in totals) totals[e.event_type] += 1;
    if (e.event_type === 'shortcut' && e.text) shortcuts.set(e.text, (shortcuts.get(e.text) ?? 0) + 1);
    const a = apps.get(e.app) ?? { app: e.app, click: 0, key: 0, shortcut: 0, copy: 0, paste: 0, scroll: 0, first: t, last: t };
    if (e.event_type in a) a[e.event_type] += 1;
    a.last = t;
    apps.set(e.app, a);
  }
  const bursts = typingBursts(sorted);
  const transfers = sorted.filter((e) => e.event_type === 'paste' && e.payload?.source_app && e.payload.source_app !== e.app).length;
  const perApp = [...apps.values()].filter((a) => a.app).map((a) => {
    const mins = Math.max(0.25, (a.last - a.first) / 60000);
    return { app: a.app, short: shortApp(a.app), clicks: a.click, keys: a.key, shortcuts: a.shortcut, copies: a.copy, pastes: a.paste, scrolls: a.scroll, minutes: Math.round(mins * 10) / 10, clicks_per_min: Math.round((a.click / mins) * 10) / 10, keys_per_min: Math.round((a.key / mins) * 10) / 10 };
  }).sort((x, y) => y.keys + y.clicks - (x.keys + x.clicks));
  const mins = active / 60;
  return {
    active_seconds: active,
    totals,
    clicks_per_min: Math.round((totals.click / mins) * 10) / 10,
    keys_per_min: Math.round((totals.key / mins) * 10) / 10,
    typing_bursts: bursts.length,
    longest_burst_keys: bursts.reduce((m, b) => Math.max(m, b.keys), 0),
    mouse_to_key_ratio: totals.key ? Math.round((totals.click / totals.key) * 100) / 100 : null,
    rekeyed_between_apps: transfers,
    idle_seconds: Math.round(idleMs / 1000),
    idle_gaps: idleGaps,
    longest_idle_seconds: Math.round(longestIdle / 1000),
    top_shortcuts: [...shortcuts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([combo, n]) => ({ combo, n })),
    per_app: perApp.slice(0, 8),
  };
}

// Compare with the employee's previous recordings (their manifests' counts).
export function trends(current, previous = []) {
  const prev = previous.filter((m) => m?.counts && m.active_seconds > 0);
  if (!prev.length) return { baseline: 0, metrics: [] };
  const perMin = (n, sec) => n / (Math.max(1, sec) / 60);
  const rate = (m, k) => perMin(m.counts[k] ?? 0, m.active_seconds);
  const avg = (f) => prev.reduce((s, m) => s + f(m), 0) / prev.length;
  const mk = (key, label, now, base) => ({ key, label, now: Math.round(now * 10) / 10, baseline: Math.round(base * 10) / 10, delta_pct: base ? Math.round(((now - base) / base) * 100) : null });
  return {
    baseline: prev.length,
    metrics: [
      mk('clicks_per_min', 'Clicks per minute', current.clicks_per_min, avg((m) => rate(m, 'click'))),
      mk('keys_per_min', 'Keystrokes per minute', current.keys_per_min, avg((m) => rate(m, 'key'))),
      mk('copy_paste_per_min', 'Copy/paste per minute', perMin(current.totals.copy + current.totals.paste, current.active_seconds), avg((m) => rate(m, 'copy') + rate(m, 'paste'))),
      mk('shortcuts_per_min', 'Shortcuts per minute', perMin(current.totals.shortcut, current.active_seconds), avg((m) => rate(m, 'shortcut'))),
      mk('active_minutes', 'Active minutes', current.active_seconds / 60, avg((m) => m.active_seconds / 60)),
    ],
  };
}

const SUMMARY_SYSTEM = `You are Vista, a process analyst. You get keyboard/mouse statistics for one recorded work session, a comparison with the employee's earlier sessions, and any sensitive-content flags.
Write for the employee, in plain language, 2 to 4 sentences: what the input pattern says about how the work was done (typing-heavy vs clicking, re-keying between apps, idle stretches), how it compares with earlier sessions (only if a baseline exists), and — if there are flags — a neutral one-sentence reminder to check them before submitting. Use only the numbers given; do not estimate durations. Never speculate about what was typed.
Respond as JSON: {"summary": "...", "highlights": ["...", "..."]}`;

export async function summarizeInsights({ input, trends: tr, flags }, api, { fetchFn = globalThis.fetch } = {}) {
  const user = JSON.stringify({ input, trends: tr, flags: flags.map((f) => ({ kind: f.kind, scope: f.scope, reason: f.reason })) });
  const res = await fetchFn(api.url ?? OPENAI_URL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${api.key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...(api.extra ?? {}), model: api.model, max_tokens: 400, temperature: 0.2, response_format: { type: 'json_object' }, messages: [{ role: 'system', content: SUMMARY_SYSTEM }, { role: 'user', content: user }] }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`${api.provider === 'openrouter' ? 'OpenRouter' : 'OpenAI'} ${res.status}: ${body.slice(0, 200) || res.statusText}`);
  }
  const data = await res.json();
  let parsed = {};
  try {
    parsed = JSON.parse(data.choices?.[0]?.message?.content ?? '{}');
  } catch {
    parsed = {};
  }
  return { text: String(parsed.summary ?? '').trim().slice(0, 1200), highlights: Array.isArray(parsed.highlights) ? parsed.highlights.slice(0, 5).map((h) => String(h).slice(0, 200)) : [], model: data.model ?? api.model, usage: data.usage ?? null, at: new Date().toISOString() };
}

// Deterministic part; keeps earlier flag decisions and the approval when re-run.
export function buildInsights({ manifest, events, sections, files, previous = [] }, prior = null) {
  const flags = [...sections.flatMap((s) => sectionFlags(s, events)), ...fileFlags(files)];
  const decided = new Map((prior?.flags ?? []).map((f) => [f.id, f]));
  for (const f of flags) {
    const p = decided.get(f.id);
    f.decision = p?.decision ?? null;
    if (p?.decided_at) f.decided_at = p.decided_at;
  }
  const input = inputAnalysis(events, manifest);
  return {
    version: 1,
    generated_at: new Date().toISOString(),
    flags,
    input,
    trends: trends(input, previous),
    summary: prior?.summary ?? null,
    approved_at: prior?.approved_at ?? null,
    approved_by: prior?.approved_by ?? null,
  };
}

export function insightsSummary(insights) {
  const flags = insights?.flags ?? [];
  return {
    flags: flags.length,
    open_flags: flags.filter((f) => !f.decision).length,
    confirmed: flags.filter((f) => f.decision === 'confirmed').length,
    high: flags.filter((f) => f.severity === 'high' && f.decision !== 'dismissed').length,
    approved: !!insights?.approved_at,
  };
}
