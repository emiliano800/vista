// AI explanations of a recording, one per video section plus the whole
// session. After Stop the recorder describes each section (window, duration,
// interaction counts, copy/paste flows — never keystrokes, screenshots only
// when enabled) to an OpenAI-compatible model, which answers with a label, a
// one-paragraph explanation and a confidence. The employee then approves or
// fixes the explanation; below CONFIDENCE_THRESHOLD the model's questions are
// shown and the employee has to explain the stretch themselves.
//
// Everything here is pure or takes an injectable fetch so it runs in tests
// without a key. Persistence (review.json) lives in main.js.

import fs from 'node:fs';
import path from 'node:path';

export const OPENAI_URL = 'https://api.openai.com/v1/chat/completions';
export const CONFIDENCE_THRESHOLD = 0.88;
export const SESSION_ID = 'session';

// Review item lifecycle:
//   proposed  — confident explanation, waiting for Approve / Fix
//   unsure    — below threshold, employee must explain
//   approved  — employee accepted the AI label as-is
//   fixed     — employee corrected a confident explanation
//   explained — employee explained an unsure stretch
//   failed    — the model call failed; treated like unsure in the UI
export const OPEN_STATUSES = new Set(['proposed', 'unsure', 'failed']);
export const RESOLVED_STATUSES = new Set(['approved', 'fixed', 'explained']);

export const OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions';
export const DEFAULT_MODEL = 'gpt-4.1-nano';
export const DEFAULT_OPENROUTER_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free';

// Any OpenAI-compatible chat endpoint. Key precedence: OPENAI_API_KEY, then
// VISTA_OPENAI_API_KEY (the backend's name, so one .env serves both), then Settings.
// An `sk-or-` key or VISTA_OPENAI_BASE_URL=https://openrouter.ai/api/v1 selects OpenRouter;
// VISTA_OPENAI_PROVIDER_ONLY=nvidia pins OpenRouter to one provider with no fallbacks.
// `source` tells the UI where the key came from; the key itself never leaves this module.
export function openaiConfig(env = process.env, settings = {}) {
  const envKey = env.OPENAI_API_KEY || env.VISTA_OPENAI_API_KEY || '';
  const key = envKey || settings.openaiApiKey || '';
  if (!key) return null;
  const base = (env.VISTA_OPENAI_BASE_URL || '').replace(/\/+$/, '');
  const openrouter = key.startsWith('sk-or-') || /openrouter\.ai/.test(base);
  const url = env.VISTA_OPENAI_URL || (base ? `${base}/chat/completions` : openrouter ? OPENROUTER_URL : OPENAI_URL);
  const model = env.VISTA_OPENAI_MODEL || settings.openaiModel || (openrouter ? DEFAULT_OPENROUTER_MODEL : DEFAULT_MODEL);
  const only = (env.VISTA_OPENAI_PROVIDER_ONLY || '').split(',').map((s) => s.trim()).filter(Boolean);
  // Reasoning models spend the token budget thinking; the review only needs the JSON answer.
  const extra = openrouter ? { reasoning: { enabled: false } } : {};
  if (only.length) extra.provider = { only, allow_fallbacks: false };
  return { key, model, url, extra, source: envKey ? 'env' : 'settings', provider: openrouter ? 'openrouter' : 'openai' };
}

const SYSTEM = `You are Vista, a process analyst helping a small company understand how its employees actually work.
You are shown what a desktop recorder observed during one stretch of an employee's day (or the whole session), plus anything the employee has already said.
Explain, in plain language, what the employee was most likely doing and why. Then rate how sure you are.
Rules:
- "label": 3 to 8 words naming the task, as an analyst would write it on a process map (e.g. "Enter vendor bills in QuickBooks").
- "explanation": 1 to 3 sentences, plain language, no jargon, only what the evidence supports. Say what was done, from what to what, and how it fits the day.
- "confidence": a number from 0 to 1. Be honest: 0.9+ only when the window titles, interaction pattern and copy/paste flows leave little doubt about the task; 0.5-0.8 when the app is clear but the purpose is not; below 0.5 when you are guessing.
- "unclear": short list of what the screen cannot tell you (why, what triggered it, what happened off-screen, what decision was made).
- "questions": 2 to 4 short questions the employee could answer in one sentence each to resolve "unclear". Never ask for passwords, personal data or named customers.
Respond as JSON: {"label": "...", "explanation": "...", "confidence": 0.0, "unclear": ["..."], "questions": ["..."]}`;

const hm = (sec) => {
  const m = Math.round(sec / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m || 1} min`;
};

// Build the user message for one section (or the whole session when
// section.whole is true) from the recording's manifest, sections and events.
export function describeSection(section, { manifest = {}, sections = [], events = [], answers = [] } = {}) {
  const lines = [];
  if (section.whole) {
    lines.push(`WHOLE SESSION: ${manifest.started_at} → ${manifest.ended_at}, ${hm(manifest.active_seconds ?? 0)} of active work.`);
    lines.push(`Applications by time: ${(manifest.apps ?? []).map((a) => `${a.app} ${hm(a.seconds)}`).join('; ') || 'unknown'}.`);
    if (sections.length) lines.push('Main stretches of work:\n' + sections.map((s) => `  - ${s.name} (${hm(s.seconds)})`).join('\n'));
  } else {
    lines.push(`SECTION: ${section.name}`);
    lines.push(`Duration ${hm(section.seconds)}, application "${section.app}", window "${section.title}"${section.url ? `, url ${section.url}` : ''}.`);
    const c = section.counts ?? {};
    lines.push(`Interactions: ${c.click ?? 0} clicks, ${c.key ?? 0} key bursts, ${c.shortcut ?? 0} shortcuts, ${c.copy ?? 0} copies, ${c.paste ?? 0} pastes, ${c.scroll ?? 0} scrolls, ${(section.shots ?? []).length} screen changes.`);
    if (section.other_apps?.length) lines.push(`Brief visits to: ${section.other_apps.join(', ')}.`);
    const flows = pasteFlows(events, section);
    if (flows.length) lines.push('Copied from → pasted into: ' + flows.map((f) => `${f.from} → ${f.to} (${f.n}×)`).join('; ') + '.');
    const shortcuts = topShortcuts(events, section);
    if (shortcuts.length) lines.push(`Most used shortcuts: ${shortcuts.join(', ')}.`);
  }
  const notes = (manifest.annotations_preview ?? []).filter(Boolean);
  if (notes.length) lines.push('Employee notes on this session so far: ' + notes.join(' | '));
  if (answers.length) {
    lines.push("Previous questions and the employee's answers:");
    for (const a of answers) lines.push(`  Q: ${a.q}\n  A: ${a.a}`);
  }
  return lines.join('\n');
}

function inRange(e, s) {
  if (s.whole) return true;
  const t = Date.parse(e.timestamp);
  return t >= Date.parse(s.start) && t < Date.parse(s.end);
}

function pasteFlows(events, s) {
  const m = new Map();
  for (const e of events) {
    if (e.event_type !== 'paste' || !e.payload?.source_app || !inRange(e, s)) continue;
    const k = `${e.payload.source_app}→${e.app}`;
    m.set(k, (m.get(k) ?? 0) + 1);
  }
  return [...m.entries()].map(([k, n]) => ({ from: k.split('→')[0], to: k.split('→')[1], n })).sort((a, b) => b.n - a.n).slice(0, 4);
}

function topShortcuts(events, s) {
  const m = new Map();
  for (const e of events) if (e.event_type === 'shortcut' && e.text && inRange(e, s)) m.set(e.text, (m.get(e.text) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, n]) => `${k} ×${n}`);
}

function imageParts(section, dir, max = 3) {
  const shots = section.shots ?? [];
  if (!shots.length || !dir) return [];
  const step = Math.max(1, Math.floor(shots.length / max));
  const pick = shots.filter((_, i) => i % step === 0).slice(0, max);
  const parts = [];
  for (const rel of pick) {
    try {
      const b64 = fs.readFileSync(path.join(dir, rel)).toString('base64');
      parts.push({ type: 'image_url', image_url: { url: `data:image/jpeg;base64,${b64}`, detail: 'low' } });
    } catch {
      /* shot missing */
    }
  }
  return parts;
}

const strList = (v, max) => (Array.isArray(v) ? v : []).map((q) => String(q).trim()).filter(Boolean).slice(0, max);

// Parse the model's reply. Confidence is clamped to [0, 1]; a reply we cannot
// parse at all is reported with confidence 0 so it lands in the "unsure" bucket.
export function parseExplanation(text) {
  let s = String(text ?? '').trim();
  if (s.startsWith('```')) s = s.split('```')[1].replace(/^json/i, '').trim();
  try {
    const j = JSON.parse(s);
    const c = Number(j.confidence);
    return {
      label: String(j.label ?? '').trim().slice(0, 80),
      explanation: String(j.explanation ?? '').trim(),
      confidence: Number.isFinite(c) ? Math.min(1, Math.max(0, c > 1 ? c / 100 : c)) : 0,
      unclear: strList(j.unclear, 4),
      questions: strList(j.questions, 4),
    };
  } catch {
    const qs = s.split('\n').map((l) => l.replace(/^[\s\-*\d.)]+/, '').trim()).filter((l) => l.endsWith('?'));
    return { label: '', explanation: s.slice(0, 300), confidence: 0, unclear: [], questions: qs.slice(0, 4) };
  }
}

export const statusFor = (confidence, threshold = CONFIDENCE_THRESHOLD) => (confidence >= threshold ? 'proposed' : 'unsure');

// POST to the model for one section. `fetchFn` is injectable for tests.
export async function explainSection(section, ctx, api, { fetchFn = globalThis.fetch, dir = null, screenshots = false, threshold = CONFIDENCE_THRESHOLD } = {}) {
  const text = describeSection(section, ctx);
  const content = [{ type: 'text', text }];
  if (screenshots && !section.whole) content.push(...imageParts(section, dir));
  const res = await fetchFn(api.url ?? OPENAI_URL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${api.key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...(api.extra ?? {}),
      model: api.model,
      max_tokens: 500,
      temperature: 0.2,
      response_format: { type: 'json_object' },
      messages: [
        { role: 'system', content: SYSTEM },
        { role: 'user', content },
      ],
    }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`${api.provider === 'openrouter' ? 'OpenRouter' : 'OpenAI'} ${res.status}: ${body.slice(0, 200) || res.statusText}`);
  }
  const data = await res.json();
  const parsed = parseExplanation(data.choices?.[0]?.message?.content);
  return {
    id: section.whole ? SESSION_ID : section.id,
    ...parsed,
    status: statusFor(parsed.confidence, threshold),
    threshold,
    model: data.model ?? api.model,
    at: new Date().toISOString(),
    prompt: text,
    usage: data.usage ?? null,
  };
}

// Employee decision on one review item. Pure: returns the updated item plus
// the annotation row the caller should persist (provenance stays explicit —
// what the AI said vs. what the employee decided).
//   approve — keep the AI label/explanation as-is (only allowed when proposed)
//   fix     — employee corrects label/note of a confident explanation
//   explain — employee explains an unsure/failed stretch (label required)
export function applyDecision(item, action, { label = '', note = '', answers = [] } = {}) {
  if (!item) throw new Error('unknown review item');
  const l = label.trim(), n = note.trim();
  let status, finalLabel, finalNote;
  if (action === 'approve') {
    if (item.status !== 'proposed') throw new Error('only a confident explanation can be approved');
    status = 'approved';
    finalLabel = item.label;
    finalNote = item.explanation;
  } else if (action === 'fix') {
    if (!l) throw new Error('a corrected label is required');
    status = 'fixed';
    finalLabel = l;
    finalNote = n;
  } else if (action === 'explain') {
    if (!l && !n) throw new Error('an explanation is required');
    status = 'explained';
    finalLabel = l || n.slice(0, 80);
    finalNote = n;
  } else {
    throw new Error(`unknown action ${action}`);
  }
  const qa = answers.map((a) => ({ q: String(a.q ?? '').trim(), a: String(a.a ?? '').trim() })).filter((a) => a.q && a.a);
  const noteWithAnswers = [finalNote, ...qa.map((x) => `${x.q} ${x.a}`)].filter(Boolean).join(' · ');
  return {
    item: { ...item, status, decision: action, final_label: finalLabel, final_note: noteWithAnswers, resolved_at: new Date().toISOString() },
    annotation: {
      label: finalLabel,
      note: noteWithAnswers,
      qa,
      author: action === 'approve' ? 'ai' : 'employee',
      ai: { label: item.label, explanation: item.explanation, confidence: item.confidence, decision: action },
    },
  };
}

// What the analyst (and the review banner) needs at a glance.
export function reviewSummary(items = {}, threshold = CONFIDENCE_THRESHOLD) {
  const list = Object.values(items).filter((i) => i.id !== SESSION_ID);
  const s = { total: list.length, threshold, awaiting: 0, unclear: 0, approved: 0, fixed: 0, explained: 0, failed: 0, unclear_ids: [], awaiting_ids: [] };
  for (const i of list) {
    if (i.status === 'proposed') { s.awaiting++; s.awaiting_ids.push(i.id); }
    else if (i.status === 'unsure') { s.unclear++; s.unclear_ids.push(i.id); }
    else if (i.status === 'failed') { s.failed++; s.unclear_ids.push(i.id); }
    else if (i.status in s) s[i.status]++;
  }
  s.open = s.awaiting + s.unclear + s.failed;
  s.resolved = s.approved + s.fixed + s.explained;
  s.session = items[SESSION_ID] ?? null;
  return s;
}
