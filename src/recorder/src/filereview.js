// File agent: as soon as a recording stops, every document snapshot is parsed on
// this computer (no dependencies: plain text, CSV/TSV, JSON, XML and the Office
// zip formats via node:zlib), scanned for sensitive content, and — when a model is
// configured — summarised in one sentence. Results live on the file entry in
// files.json as `review`; the employee confirms or dismisses each flag.
import fs from 'node:fs';
import path from 'node:path';
import { inflateRawSync } from 'node:zlib';
import { OPENAI_URL } from './explain.js';

export const TEXT_EXTS = new Set(['.txt', '.md', '.csv', '.tsv', '.json', '.xml']);
export const ZIP_EXTS = new Set(['.xlsx', '.xlsm', '.docx', '.pptx']);
export const EXCERPT_CHARS = 6000;
export const MAX_PARSE_BYTES = 20 * 1024 * 1024;

// Patterns are checked on the extracted text (before any model sees it).
// `kind` names match the section flags in insights.js so the UI can group them.
export const SENSITIVE_PATTERNS = [
  ['card_number', /\b(?:\d[ -]?){13,19}\b/, 'looks like a card number'],
  ['iban', /\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b/, 'looks like an IBAN'],
  ['ssn', /\b\d{3}-\d{2}-\d{4}\b/, 'looks like a social security number'],
  ['credential', /\b(?:password|passwd|pwd|passcode|api[_ -]?key|secret[_ -]?key|access[_ -]?token)\b\s*[:=]/i, 'contains a credential assignment'],
  ['personal', /\b(?:date of birth|dob|passport|driver'?s licen[cs]e|national insurance|social security)\b/i, 'mentions personal identity data'],
  ['payroll', /\b(?:salary|salaries|payroll|net pay|gross pay|compensation)\b/i, 'mentions pay or compensation'],
  ['confidential', /\b(?:confidential|do not distribute|internal only|nda)\b/i, 'marked confidential'],
  ['email', /[\w.+-]+@[\w-]+\.[\w.-]+/, 'contains e-mail addresses'],
];

// ---- minimal zip reader (central directory → inflateRaw) -------------------------

export function zipEntries(buf) {
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('not a zip file');
  const count = buf.readUInt16LE(eocd + 10);
  let p = buf.readUInt32LE(eocd + 16);
  const entries = new Map();
  for (let n = 0; n < count && p + 46 <= buf.length; n++) {
    if (buf.readUInt32LE(p) !== 0x02014b50) break;
    const method = buf.readUInt16LE(p + 10);
    const csize = buf.readUInt32LE(p + 20);
    const usize = buf.readUInt32LE(p + 24);
    const nameLen = buf.readUInt16LE(p + 28), extraLen = buf.readUInt16LE(p + 30), commentLen = buf.readUInt16LE(p + 32);
    const offset = buf.readUInt32LE(p + 42);
    const name = buf.toString('utf8', p + 46, p + 46 + nameLen);
    entries.set(name, { method, csize, usize, offset });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return {
    names: [...entries.keys()],
    read(name) {
      const e = entries.get(name);
      if (!e) return null;
      const h = e.offset;
      if (buf.readUInt32LE(h) !== 0x04034b50) throw new Error('bad zip entry');
      const nameLen = buf.readUInt16LE(h + 26), extraLen = buf.readUInt16LE(h + 28);
      const start = h + 30 + nameLen + extraLen;
      const data = buf.subarray(start, start + e.csize);
      if (e.method === 0) return data;
      if (e.method === 8) return inflateRawSync(data);
      throw new Error(`unsupported zip compression ${e.method}`);
    },
  };
}

const decodeXml = (s) => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n)).replace(/&amp;/g, '&');
const xmlText = (xml, tag) => {
  const re = new RegExp(`<${tag}(?:\\s[^>]*)?>([^<]*)</${tag}>`, 'g');
  const out = [];
  let m;
  while ((m = re.exec(xml))) out.push(decodeXml(m[1]));
  return out;
};

function xlsxText(zip) {
  const shared = zip.names.includes('xl/sharedStrings.xml') ? xmlText(zip.read('xl/sharedStrings.xml').toString('utf8'), 't') : [];
  const sheets = zip.names.filter((n) => /^xl\/worksheets\/sheet\d+\.xml$/.test(n)).sort();
  const rows = [];
  for (const name of sheets) {
    const xml = zip.read(name).toString('utf8');
    const rowRe = /<row[^>]*>([\s\S]*?)<\/row>/g;
    let r;
    while ((r = rowRe.exec(xml))) {
      const cells = [];
      const cRe = /<c([^>]*)>([\s\S]*?)<\/c>/g;
      let c;
      while ((c = cRe.exec(r[1]))) {
        const attrs = c[1], inner = c[2];
        const v = /<v>([^<]*)<\/v>/.exec(inner)?.[1] ?? xmlText(inner, 't').join('');
        cells.push(/t="s"/.test(attrs) ? shared[+v] ?? '' : decodeXml(v));
      }
      if (cells.some(Boolean)) rows.push(cells.join('\t'));
    }
  }
  return rows.join('\n');
}

function docxText(zip) {
  const xml = zip.read('word/document.xml')?.toString('utf8') ?? '';
  return xml.replace(/<\/w:p>/g, '\n').replace(/<w:tab\/>/g, '\t').replace(/<[^>]+>/g, '').split('\n').map((l) => decodeXml(l).trim()).filter(Boolean).join('\n');
}

function pptxText(zip) {
  const slides = zip.names.filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n)).sort((a, b) => +a.match(/\d+/g).pop() - +b.match(/\d+/g).pop());
  return slides.map((n) => xmlText(zip.read(n).toString('utf8'), 'a:t').join(' ')).filter(Boolean).join('\n');
}

// { status: 'parsed' | 'unsupported' | 'failed', text, chars, note }
export function extractText(buf, ext) {
  ext = String(ext ?? '').toLowerCase();
  try {
    if (buf.length > MAX_PARSE_BYTES) return { status: 'unsupported', text: '', chars: 0, note: 'too large to parse on this computer' };
    if (TEXT_EXTS.has(ext)) {
      const text = buf.toString('utf8').replace(/\u0000/g, '');
      return { status: 'parsed', text, chars: text.length, note: null };
    }
    if (ZIP_EXTS.has(ext)) {
      const zip = zipEntries(buf);
      const text = ext === '.docx' ? docxText(zip) : ext === '.pptx' ? pptxText(zip) : xlsxText(zip);
      return { status: 'parsed', text, chars: text.length, note: null };
    }
    const why = { '.pdf': 'PDF text extraction is not available on this computer', '.xls': 'legacy Excel format', '.doc': 'legacy Word format', '.ppt': 'legacy PowerPoint format', '.rtf': 'rich text format' }[ext] ?? 'file type not parsed';
    return { status: 'unsupported', text: '', chars: 0, note: why };
  } catch (e) {
    return { status: 'failed', text: '', chars: 0, note: e.message };
  }
}

// Deterministic sensitive-content scan: one flag per kind, with the line it was seen on.
export function scanSensitive(text) {
  const flags = [];
  if (!text) return flags;
  const lines = text.split('\n');
  for (const [kind, re, reason] of SENSITIVE_PATTERNS) {
    const idx = lines.findIndex((l) => re.test(l));
    if (idx < 0) continue;
    const hits = lines.filter((l) => re.test(l)).length;
    flags.push({ kind, reason: hits > 1 ? `${reason} (${hits} lines)` : reason, line: idx + 1, source: 'scan' });
  }
  return flags;
}

const DOC_SYSTEM = `You are Vista's file reviewer. You are shown the beginning of a document an employee had open while their work was recorded.
Respond as JSON: {"summary": "one sentence, plain language, what this document is and what it is for", "kind": "invoice|spreadsheet|report|email|contract|list|notes|form|other", "sensitive": [{"kind": "personal|payroll|credential|financial_account|confidential|other", "reason": "short reason"}], "confidence": 0.0}
Only list "sensitive" items the text actually shows (personal identity data, pay, credentials, bank/card numbers, confidential markings). Never repeat the sensitive values themselves.`;

export async function describeDocument(file, text, api, { fetchFn = globalThis.fetch } = {}) {
  const excerpt = text.slice(0, EXCERPT_CHARS);
  const res = await fetchFn(api.url ?? OPENAI_URL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${api.key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...(api.extra ?? {}),
      model: api.model,
      max_tokens: 300,
      temperature: 0.1,
      response_format: { type: 'json_object' },
      messages: [
        { role: 'system', content: DOC_SYSTEM },
        { role: 'user', content: `FILE: ${file.name} (${file.ext}, ${file.size_bytes ?? '?'} bytes${file.edited ? ', edited during the session' : ''})\n\n${excerpt}${text.length > excerpt.length ? '\n[... truncated]' : ''}` },
      ],
    }),
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
  const conf = Number(parsed.confidence);
  return {
    summary: String(parsed.summary ?? '').trim().slice(0, 400),
    kind: String(parsed.kind ?? 'other').slice(0, 40),
    sensitive: Array.isArray(parsed.sensitive) ? parsed.sensitive.slice(0, 8).map((s) => ({ kind: String(s?.kind ?? 'other').slice(0, 40), reason: String(s?.reason ?? '').slice(0, 200), source: 'model' })) : [],
    confidence: Number.isFinite(conf) ? Math.min(1, Math.max(0, conf)) : 0,
    model: data.model ?? api.model,
    usage: data.usage ?? null,
    at: new Date().toISOString(),
  };
}

// Parse + scan every included snapshot (skipping ones already reviewed), then ask
// the model about each parsed document when `api` is set. Mutates and returns `files`.
export async function reviewFiles(dir, files, api, { fetchFn = globalThis.fetch, onFile = null, force = false } = {}) {
  for (const f of files) {
    if (f.include === false || !f.snapshot) continue;
    if (f.review && !force && f.review.status !== 'failed') continue;
    let buf;
    try {
      buf = fs.readFileSync(path.join(dir, f.snapshot));
    } catch (e) {
      f.review = { status: 'failed', chars: 0, note: e.message, flags: [], ai: null, at: new Date().toISOString() };
      continue;
    }
    const ex = extractText(buf, f.ext);
    const flags = scanSensitive(ex.text);
    f.review = { status: ex.status, chars: ex.chars, note: ex.note, flags, ai: null, at: new Date().toISOString() };
    if (api && ex.status === 'parsed' && ex.text.trim()) {
      try {
        f.review.ai = await describeDocument(f, ex.text, api, { fetchFn });
        for (const s of f.review.ai.sensitive) if (!flags.some((x) => x.kind === s.kind)) flags.push({ kind: s.kind, reason: s.reason, line: null, source: 'model' });
      } catch (e) {
        f.review.ai = { error: e.message, at: new Date().toISOString() };
      }
    }
    if (onFile) onFile(f);
  }
  return files;
}
