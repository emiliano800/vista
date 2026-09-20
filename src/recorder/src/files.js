// Documents the employee worked with during a recording, and when.
//
// Three signals, all macOS-only today:
//   AXDocument  the focused window of most document apps (Excel, Word, PowerPoint,
//               Preview, Numbers, Pages, Acrobat…) exposes its file URL through
//               Accessibility — read via System Events on every focus change.
//   lsof        fallback for apps without AXDocument: document files the
//               frontmost process has open.
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
const IGNORE = [/\/Library\//, /\/\.Trash\//, /\/node_modules\//, /\/\.git\//, /\/Vista\/(recordings|submitted)\//, /\/~\$[^/]*$/];

export const isDocument = (p) => !!FILE_TYPES[path.extname(String(p ?? '')).toLowerCase()] && !IGNORE.some((re) => re.test(p));

// A file name the workspace accepts (see MEDIA_NAME on the server): ASCII letters,
// digits, dot, dash, underscore; never starting with a dot.
export function safeName(name) {
  const cleaned = String(name).normalize('NFKD').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^[._-]+/, '');
  return (cleaned || 'file').slice(0, 120);
}

export const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');

/**
 * Pure bookkeeping: which documents are "open" (seen by a probe) and for how
 * long. `observe(paths, at)` is called with everything a probe currently sees;
 * files that stop being seen are closed at that instant. Intervals shorter than
 * `minSeconds` are dropped as noise (a Finder preview flicker).
 */
export class FileTracker {
  constructor({ minSeconds = 3 } = {}) {
    this.minSeconds = minSeconds;
    this.files = new Map(); // path -> { path, first_seen, intervals: [{start, end, app}], open: {start, app} | null, sources: Set }
  }

  _entry(p) {
    let f = this.files.get(p);
    if (!f) {
      f = { path: p, first_seen: null, intervals: [], open: null, sources: new Set(), point_uses: [] };
      this.files.set(p, f);
    }
    return f;
  }

  /** Probe result at `at` (ms): the set of document paths currently on screen / open in the frontmost app. */
  observe(paths, at, { app = '', source = 'ax' } = {}) {
    const seen = new Set(paths.filter(isDocument).map((p) => path.resolve(p)));
    for (const p of seen) {
      const f = this._entry(p);
      f.sources.add(source);
      if (f.first_seen == null) f.first_seen = at;
      if (!f.open) f.open = { start: at, app };
      else if (app && !f.open.app) f.open.app = app;
    }
    for (const f of this.files.values()) if (f.open && !seen.has(f.path)) this._close(f, at);
  }

  /** A pause or Stop: everything open closes now. */
  closeAll(at) {
    for (const f of this.files.values()) if (f.open) this._close(f, at);
  }

  _close(f, at) {
    const { start, app } = f.open;
    f.open = null;
    if (at - start >= this.minSeconds * 1000) f.intervals.push({ start, end: at, app });
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
      const intervals = f.intervals
        .flatMap((iv) => cutPauses(Math.max(iv.start, t0), Math.min(iv.end, t1), pauses).map(([s, e]) => ({ start: s, end: e, app: iv.app })))
        .filter((iv) => iv.end - iv.start >= this.minSeconds * 1000)
        .sort((a, b) => a.start - b.start);
      const points = f.point_uses.filter((t) => t >= t0 && t <= t1);
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
        used_at: points.map(iso),
        sources: [...f.sources].sort(),
        include: true,
      });
    }
    return out.sort((a, b) => a.first_opened.localeCompare(b.first_opened));
  }
}

const iso = (ms) => new Date(ms).toISOString();

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

const AX_SCRIPT = `
tell application "System Events"
  set out to ""
  try
    set p to first process whose frontmost is true
    repeat with w in windows of p
      try
        set d to value of attribute "AXDocument" of w
        if d is not missing value then set out to out & d & linefeed
      end try
    end repeat
  end try
  return out
end tell`;

function fromFileURL(s) {
  try {
    return s.startsWith('file://') ? decodeURIComponent(new URL(s).pathname) : s;
  } catch {
    return '';
  }
}

/** Documents shown in the frontmost app's windows (Accessibility). */
export async function axDocuments(exec = run) {
  if (!IS_MAC) return [];
  try {
    const { stdout } = await exec('osascript', ['-e', AX_SCRIPT], { timeout: 1500 });
    return stdout.split('\n').map((s) => fromFileURL(s.trim())).filter(isDocument);
  } catch {
    return [];
  }
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
  const q = (attr) => `${attr} >= $time.iso(${start}) && ${attr} <= $time.iso(${end})`;
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
