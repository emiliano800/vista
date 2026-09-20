// Documents the employee worked with during a recording, and when.
//
// Four signals, all macOS-only today:
//   AXDocument  every window of every running app (Excel, Word, PowerPoint,
//               Preview, Numbers, Pages, Acrobat…) exposes its file URL through
//               Accessibility — read via System Events on focus changes and every
//               few seconds, so a document open in the background still counts.
//   lsof        fallback for apps without AXDocument: document files the
//               frontmost process has open.
//   title       the frontmost window's title, when it is a document name, is
//               resolved to a path through Spotlight (apps that expose neither).
//   Spotlight   at Stop: every file whose "last used" or "date added" falls inside
//               the session (kMDItemLastUsedDate / kMDItemDateAdded), which also
//               catches downloads and anything the live probes missed.
//
// The tracker turns probe results into per-file open/close intervals aligned with
// the video, and at Stop snapshots the *last* version of each file into
// files/<sha12>/<name> so it can be ingested with the recording. Nothing is copied
// while recording; nothing outside the allowlist is ever read.
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';

const run = promisify(execFile);
const IS_MAC = process.platform === 'darwin';

export const FILE_TYPES = {
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xlsm': 'application/vnd.ms-excel.sheet.macroenabled.12',
  '.xls': 'application/vnd.ms-excel',
  '.csv': 'text/csv',
  '.tsv': 'text/tab-separated-values',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.doc': 'application/msword',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  '.ppt': 'application/vnd.ms-powerpoint',
  '.pdf': 'application/pdf',
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.rtf': 'application/rtf',
  '.json': 'application/json',
  '.xml': 'application/xml',
};
export const FILE_MAX_BYTES = 50 * 1024 * 1024;
export const FILES_FILE = 'files.json';
export const FILES_DIR = 'files';

// Folders we never look into: the recorder's own data, app bundles, caches.
// ~/Library is skipped except the two places macOS keeps real documents: iCloud
// Drive (Desktop & Documents when synced) and CloudStorage (OneDrive, Dropbox, Drive).
const IGNORE = [/\/Library\/(?!Mobile Documents\/|CloudStorage\/)/, /\/\.Trash\//, /\/node_modules\//, /\/\.git\//, /\/Vista\/(recordings|submitted)\//, /\/~\$[^/]*$/];

export const isDocument = (p) => !!FILE_TYPES[path.extname(String(p ?? '')).toLowerCase()] && !IGNORE.some((re) => re.test(p));

// A file name the workspace accepts (see MEDIA_NAME on the server): ASCII letters,
// digits, dot, dash, underscore; never starting with a dot.
export function safeName(name) {
  const clean = (s) => s.normalize('NFKD').replace(/[^A-Za-z0-9._-]+/g, '_');
  const ext = clean(path.extname(String(name))).toLowerCase().slice(0, 16);
  const base = clean(path.basename(String(name), path.extname(String(name)))).replace(/^[._-]+/, '');
  return (base || 'file').slice(0, 120 - ext.length) + ext;
}

export const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');

/**
 * Pure bookkeeping: which documents are "open" (seen by a probe) and for how
 * long. `observe(docs, at)` is called with everything a probe currently sees;
 * files that stop being seen are closed at that instant. Every file ever seen
 * stays in the history: a sighting too short for an interval (< `minSeconds`)
 * is kept as a point use instead of being dropped.
 */
export class FileTracker {
  constructor({ minSeconds = 3 } = {}) {
    this.minSeconds = minSeconds;
    this.files = new Map(); // path -> { path, first_seen, intervals: [{start, end, app}], open: {start, app, wide} | null, sources: Set, point_uses: [] }
  }

  _entry(p) {
    let f = this.files.get(p);
    if (!f) {
      f = { path: p, first_seen: null, intervals: [], open: null, sources: new Set(), point_uses: [] };
      this.files.set(p, f);
    }
    return f;
  }

  /**
   * Probe result at `at` (ms). `docs` are the documents currently open: strings
   * (paths, attributed to `app`) or `{ path, app, wide }`. `wide` marks a sighting
   * from a probe that sees every app (AXDocument across processes): such a file
   * closes as soon as it is no longer seen. Files seen only through the frontmost
   * app (lsof, window title) close when that app is frontmost again without
   * them, or when their app is no longer running (`running`, when known).
   */
  observe(docs, at, { app = '', source = 'ax', running = null } = {}) {
    const seen = new Map();
    for (const d of docs) {
      const item = typeof d === 'string' ? { path: d, app, wide: true } : d;
      if (!isDocument(item.path)) continue;
      seen.set(path.resolve(item.path), { app: item.app ?? app, wide: item.wide !== false, source: item.source ?? source });
    }
    for (const [p, s] of seen) {
      const f = this._entry(p);
      f.sources.add(s.source);
      if (f.first_seen == null) f.first_seen = at;
      if (!f.open) f.open = { start: at, app: s.app, wide: s.wide };
      else {
        if (s.app && !f.open.app) f.open.app = s.app;
        f.open.wide = f.open.wide || s.wide;
      }
    }
    for (const f of this.files.values()) {
      if (!f.open || seen.has(f.path)) continue;
      const gone = running?.size ? !isRunning(f.open.app, running) : false;
      if (f.open.wide || gone || (app && f.open.app === app)) this._close(f, at);
    }
  }

  /** A known span (rebuilding from files.json). */
  addInterval(p, start, end, { app = '', source = 'ax' } = {}) {
    if (!isDocument(p) || !(end > start)) return;
    const f = this._entry(path.resolve(p));
    f.sources.add(source);
    if (f.first_seen == null || start < f.first_seen) f.first_seen = start;
    f.intervals.push({ start, end, app });
  }

  /** A pause or Stop: everything open closes now. */
  closeAll(at) {
    for (const f of this.files.values()) if (f.open) this._close(f, at);
  }

  _close(f, at) {
    const { start, app } = f.open;
    f.open = null;
    if (at - start >= this.minSeconds * 1000) f.intervals.push({ start, end: at, app });
    else f.point_uses.push(start);
  }

  /** Spotlight-style evidence: the file was used at `at` but we have no span. */
  touch(p, at, { source = 'spotlight' } = {}) {
    if (!isDocument(p)) return;
    const f = this._entry(path.resolve(p));
    f.sources.add(source);
    if (f.first_seen == null || at < f.first_seen) f.first_seen = at;
    f.point_uses.push(at);
  }

  /**
   * Final list for files.json. `t0`/`t1` bound the session so Spotlight points
   * outside it are ignored; `pauses` are cut out of intervals.
   */
  finish({ t0, t1, pauses = [] }) {
    const out = [];
    for (const f of this.files.values()) {
      const pieces = f.intervals
        .flatMap((iv) => cutPauses(Math.max(iv.start, t0), Math.min(iv.end, t1), pauses).map(([s, e]) => ({ start: s, end: e, app: iv.app })))
        .sort((a, b) => a.start - b.start);
      const intervals = pieces.filter((iv) => iv.end - iv.start >= this.minSeconds * 1000);
      const points = [...f.point_uses, ...pieces.filter((iv) => iv.end - iv.start < this.minSeconds * 1000).map((iv) => iv.start)].filter((t) => t >= t0 && t <= t1).sort((a, b) => a - b);
      if (!intervals.length && !points.length) continue;
      const opened = intervals.length ? intervals[0].start : Math.min(...points);
      const closed = intervals.length ? intervals[intervals.length - 1].end : Math.max(...points);
      out.push({
        id: sha256(f.path).slice(0, 12),
        path: f.path,
        name: path.basename(f.path),
        ext: path.extname(f.path).toLowerCase(),
        folder: path.basename(path.dirname(f.path)),
        first_opened: iso(opened),
        last_closed: iso(closed),
        seconds: Math.round(intervals.reduce((s, iv) => s + (iv.end - iv.start), 0) / 1000),
        intervals: intervals.map((iv) => ({ start: iso(iv.start), end: iso(iv.end), app: iv.app })),
        used_at: [...new Set(points)].map(iso),
        sources: [...f.sources].sort(),
        include: true,
      });
    }
    return out.sort((a, b) => a.first_opened.localeCompare(b.first_opened));
  }
}

const iso = (ms) => new Date(ms).toISOString();

// active-win and System Events name the same app slightly differently at times
// ("Microsoft Excel" vs "Excel"); an unknown app counts as running.
function isRunning(app, running) {
  const a = String(app ?? '').toLowerCase();
  if (!a) return true;
  return [...running].some((r) => { const n = r.toLowerCase(); return n === a || n.includes(a) || a.includes(n); });
}

function cutPauses(s, e, pauses) {
  let pieces = e > s ? [[s, e]] : [];
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

// ---- snapshots ----------------------------------------------------------------

/**
 * Copy the current (last) version of every included file into
 * <dir>/files/<id>/<safe name>; records sha256, size, mtime and whether the file
 * changed during the session. Missing or oversized files are kept in the list
 * (the markers are still true) but flagged, not copied.
 */
export function snapshotFiles(dir, files, { startedAt, maxBytes = FILE_MAX_BYTES } = {}) {
  const t0 = startedAt ? Date.parse(startedAt) : 0;
  for (const f of files) {
    f.snapshot = null;
    f.snapshot_error = null;
    try {
      const st = fs.statSync(f.path);
      if (!st.isFile()) throw new Error('not a regular file');
      f.size_bytes = st.size;
      f.modified_at = st.mtime.toISOString();
      f.edited = st.mtimeMs >= t0;
      if (!f.include) continue;
      if (st.size > maxBytes) throw new Error(`larger than ${Math.round(maxBytes / 1048576)} MB`);
      const buf = fs.readFileSync(f.path);
      f.sha256 = sha256(buf);
      const rel = path.posix.join(FILES_DIR, f.id, safeName(f.name));
      fs.mkdirSync(path.join(dir, FILES_DIR, f.id), { recursive: true });
      fs.writeFileSync(path.join(dir, rel), buf);
      f.snapshot = rel;
      f.content_type = FILE_TYPES[f.ext] ?? 'application/octet-stream';
    } catch (err) {
      f.snapshot_error = err.message;
    }
  }
  return files;
}

export function readFiles(dir) {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(dir, FILES_FILE), 'utf8'));
    return Array.isArray(raw?.files) ? raw.files : [];
  } catch {
    return [];
  }
}

export function writeFiles(dir, files) {
  fs.writeFileSync(path.join(dir, FILES_FILE), JSON.stringify({ version: 1, files }, null, 2));
}

/** What travels with the recording: everything except the absolute path. */
export function publicFile(f) {
  const { path: _p, ...rest } = f;
  return rest;
}

// ---- macOS probes -------------------------------------------------------------

// Every window of every foreground app: "<app>\t<AXDocument>" per document
// window, plus "<app>\t" for apps without one so the caller knows what is running.
const AX_SCRIPT = `
tell application "System Events"
  set out to ""
  repeat with p in (every process whose background only is false)
    try
      set n to name of p
      set out to out & n & tab & linefeed
      repeat with w in windows of p
        try
          set d to value of attribute "AXDocument" of w
          if d is not missing value then set out to out & n & tab & d & linefeed
        end try
      end repeat
    end try
  end repeat
  return out
end tell`;

function fromFileURL(s) {
  try {
    return s.startsWith('file://') ? decodeURIComponent(new URL(s).pathname) : s;
  } catch {
    return '';
  }
}

/**
 * Documents open in any running app (Accessibility), as `{ path, app }`, and the
 * set of running app names — or null when the probe failed (timeout, no
 * permission), so the caller keeps its current state instead of closing everything.
 */
export async function axDocuments(exec = run) {
  if (!IS_MAC) return null;
  try {
    const { stdout } = await exec('osascript', ['-e', AX_SCRIPT], { timeout: 4000, maxBuffer: 4 * 1024 * 1024 });
    return parseAxOutput(stdout);
  } catch {
    return null;
  }
}

export function parseAxOutput(stdout) {
  const docs = [], running = new Set();
  for (const line of String(stdout).split('\n')) {
    const i = line.indexOf('\t');
    if (i < 0) continue;
    const app = line.slice(0, i).trim(), p = fromFileURL(line.slice(i + 1).trim());
    if (app) running.add(app);
    if (p && isDocument(p)) docs.push({ path: p, app, wide: true, source: 'ax' });
  }
  return { docs, running };
}

// "Q3 budget.xlsx — Edited", "invoice.pdf (page 2 of 9)", "report.docx - Word" → the file name.
export function documentNameFromTitle(title) {
  const exts = Object.keys(FILE_TYPES).map((e) => e.replace('.', '\\.')).join('|');
  const m = String(title ?? '').match(new RegExp(`([^/\\\\:*?"<>|\\t]+?(?:${exts}))(?=$|\\s*[—–\\-|(·:]|\\s+\\()`, 'i'));
  return m ? m[1].trim() : null;
}

const titleCache = new Map();
/** Resolve a window title that names a document to a path via Spotlight (cached per name). */
export async function documentFromTitle(title, exec = run, home = os.homedir()) {
  const name = documentNameFromTitle(title);
  if (!IS_MAC || !name) return null;
  if (titleCache.has(name)) return titleCache.get(name);
  let found = null;
  try {
    const { stdout } = await exec('mdfind', ['-onlyin', home, `kMDItemFSName == ${JSON.stringify(name)}`], { timeout: 3000 });
    const hits = stdout.split('\n').filter(isDocument);
    if (hits.length === 1) found = hits[0];
    else if (hits.length > 1) found = await mostRecentlyUsed(hits, exec);
  } catch {
    /* Spotlight unavailable */
  }
  titleCache.set(name, found);
  return found;
}

async function mostRecentlyUsed(paths, exec) {
  let best = null, bestAt = -1;
  for (const p of paths.slice(0, 8)) {
    const at = (await fileUsedAt(p, 'kMDItemLastUsedDate', exec)) ?? 0;
    if (at > bestAt) { best = p; bestAt = at; }
  }
  return best;
}

/** Document files a process has open (fallback for apps without AXDocument). */
export async function lsofDocuments(pid, exec = run) {
  if (!IS_MAC || !pid) return [];
  try {
    const { stdout } = await exec('lsof', ['-p', String(pid), '-Fn', '-w'], { timeout: 1500 });
    return stdout.split('\n').filter((l) => l.startsWith('n/')).map((l) => l.slice(1)).filter(isDocument);
  } catch {
    return [];
  }
}

/**
 * Spotlight sweep after Stop: files last used or added during [start, end].
 * Returns [{path, at, source}] with `source` 'download' when the file was added
 * (downloaded / saved new) in the window, else 'spotlight'.
 */
export async function spotlightSweep({ start, end }, exec = run, home = os.homedir()) {
  if (!IS_MAC) return [];
  const stamp = (s) => new Date(s).toISOString().replace(/\.\d{3}Z$/, 'Z');
  const q = (attr) => `${attr} >= $time.iso(${stamp(start)}) && ${attr} <= $time.iso(${stamp(end)})`;
  const out = [];
  for (const [attr, source] of [['kMDItemDateAdded', 'download'], ['kMDItemLastUsedDate', 'spotlight'], ['kMDItemContentModificationDate', 'spotlight']]) {
    try {
      const { stdout } = await exec('mdfind', ['-onlyin', home, q(attr)], { timeout: 8000, maxBuffer: 8 * 1024 * 1024 });
      for (const p of stdout.split('\n')) {
        if (!isDocument(p)) continue;
        const at = await fileUsedAt(p, attr, exec);
        out.push({ path: p, at: at ?? Date.parse(start), source });
      }
    } catch {
      /* Spotlight disabled or timed out */
    }
  }
  return out;
}

async function fileUsedAt(p, attr, exec) {
  try {
    const { stdout } = await exec('mdls', ['-raw', '-name', attr, p], { timeout: 1500 });
    const t = Date.parse(stdout.trim().replace(' +0000', 'Z').replace(' ', 'T'));
    return Number.isFinite(t) ? t : null;
  } catch {
    return null;
  }
}
