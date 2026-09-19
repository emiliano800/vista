// Clarifying questions via the OpenAI API. Given what the recorder saw in a
// section of the video (window, duration, interaction counts, copy/paste
// flows, optional screenshots) plus anything the employee already said, ask
// the model for the 2-4 questions an analyst would need answered to turn the
// footage into a process step. Answers come back through the same call so
// the exchange can continue and be saved as an annotation.
//
// Screenshots are NOT sent unless `settings.clarifyScreenshots` is on: window
// titles and copied text are redacted on-device, pixels are not.

import fs from 'node:fs';
import path from 'node:path';

export const OPENAI_URL = 'https://api.openai.com/v1/chat/completions';

export function openaiConfig(env = process.env, settings = {}) {
  const key = env.OPENAI_API_KEY || settings.openaiApiKey || '';
  const model = env.VISTA_OPENAI_MODEL || settings.openaiModel || 'gpt-4o-mini';
  const url = env.VISTA_OPENAI_URL || OPENAI_URL; // OpenAI-compatible proxies (Azure, LiteLLM, a company gateway)
  return key ? { key, model, url } : null;
}

const SYSTEM = `You are Vista, a process analyst helping a small company understand how its employees actually work.
You are shown what a desktop recorder observed during one stretch of an employee's day, and what the employee has already told you.
Your job is to ask the few clarifying questions that the screen cannot answer: why the work was done, what triggered it, what happened off-screen (phone, paper, colleagues), what decision was made, what would have happened next, how often this occurs.
Rules: ask 2 to 4 short questions, plain language, no jargon, each answerable in one sentence. Never ask for passwords, personal data or anything about specific customers by name. If the employee has answered previous questions, acknowledge in one short sentence and ask only what is still unclear; when nothing is unclear, say so and give a one-sentence summary of the step instead.
Respond as JSON: {"summary": "<one sentence of what this stretch of work appears to be>", "questions": ["...", "..."]}`;

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
    lines.push('Previous questions and the employee\'s answers:');
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

export function parseReply(text) {
  let s = String(text ?? '').trim();
  if (s.startsWith('```')) s = s.split('```')[1].replace(/^json/i, '').trim();
  try {
    const j = JSON.parse(s);
    return { summary: String(j.summary ?? '').trim(), questions: (j.questions ?? []).map((q) => String(q).trim()).filter(Boolean).slice(0, 4) };
  } catch {
    const qs = s.split('\n').map((l) => l.replace(/^[\s\-*\d.)]+/, '').trim()).filter((l) => l.endsWith('?'));
    return { summary: '', questions: qs.slice(0, 4) };
  }
}

// POST to OpenAI. `fetchFn` is injectable for tests.
export async function askClarifying(section, ctx, api, { fetchFn = globalThis.fetch, dir = null, screenshots = false } = {}) {
  const text = describeSection(section, ctx);
  const content = [{ type: 'text', text }];
  if (screenshots && !section.whole) content.push(...imageParts(section, dir));
  const res = await fetchFn(api.url ?? OPENAI_URL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${api.key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: api.model,
      max_tokens: 400,
      temperature: 0.3,
      response_format: { type: 'json_object' },
      messages: [
        { role: 'system', content: SYSTEM },
        { role: 'user', content },
      ],
    }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`OpenAI ${res.status}: ${body.slice(0, 200) || res.statusText}`);
  }
  const data = await res.json();
  const reply = parseReply(data.choices?.[0]?.message?.content);
  return { ...reply, model: data.model ?? api.model, prompt: text, usage: data.usage ?? null };
}
