// Jobs and batches for the agent API. One in-process FIFO runner: the reviewers
// in main.js guard themselves with Sets and write whole JSON files, so work is
// serialised the same way the dashboard serialises it (one thing at a time).
// Every state change is persisted under <home>/jobs and <home>/batches so an
// agent can read a result after the app restarts. Nothing resumes on restart:
// submitting deletes the local recording, so a half-run job is marked failed.
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { FILE_TYPES } from './files.js';

export const JOB_STATUSES = new Set(['queued', 'running', 'succeeded', 'failed']);
export const BATCH_MAX_ITEMS = 100;
const ID_RE = /^[a-zA-Z0-9_-]{1,128}$/;

function writeJSON(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(data, null, 2), { mode: 0o600 });
  fs.renameSync(temp, file);
}

function readJSON(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

export const publicJob = ({ run: _r, ...job }) => job;

export class JobStore {
  constructor({ home, now = () => new Date().toISOString(), log = console.error }) {
    this.jobsDir = path.join(home, 'jobs');
    this.batchesDir = path.join(home, 'batches');
    this.now = now;
    this.log = log;
    this.jobs = new Map();
    this.queue = [];
    this.running = null;
    this._failInterrupted();
  }

  // Jobs left queued/running by a previous process never resume.
  _failInterrupted() {
    if (!fs.existsSync(this.jobsDir)) return;
    for (const f of fs.readdirSync(this.jobsDir)) {
      if (!f.endsWith('.json')) continue;
      const job = readJSON(path.join(this.jobsDir, f));
      if (!job || (job.status !== 'queued' && job.status !== 'running')) continue;
      writeJSON(path.join(this.jobsDir, f), { ...job, status: 'failed', finished_at: this.now(), error: 'The recorder app restarted before this job finished.' });
    }
  }

  _save(job) {
    writeJSON(path.join(this.jobsDir, `${job.id}.json`), publicJob(job));
  }

  // `run` does the work and returns the job result; it is called with no
  // arguments so the caller closes over whatever main.js function it needs.
  create(kind, target, run) {
    const job = { id: randomUUID(), kind, target: target ?? null, status: 'queued', created_at: this.now(), started_at: null, finished_at: null, result: null, error: null, run };
    this.jobs.set(job.id, job);
    this._save(job);
    this.queue.push(job);
    this._drain();
    return publicJob(job);
  }

  get(id) {
    if (!ID_RE.test(String(id))) return null;
    const job = this.jobs.get(id);
    return job ? publicJob(job) : readJSON(path.join(this.jobsDir, `${id}.json`));
  }

  _drain() {
    if (this.running || !this.queue.length) return;
    const job = this.queue.shift();
    this.running = job;
    job.status = 'running';
    job.started_at = this.now();
    this._save(job);
    Promise.resolve()
      .then(() => job.run())
      .then(
        (result) => {
          job.status = 'succeeded';
          job.result = result ?? null;
        },
        (e) => {
          job.status = 'failed';
          job.error = e?.message ?? String(e);
          this.log(`job ${job.kind} ${job.id} failed:`, job.error);
        },
      )
      .finally(() => {
        job.finished_at = this.now();
        this._save(job);
        this.running = null;
        this._drain();
      });
  }

  // ---- batches ----------------------------------------------------------------
  // A batch is one job whose work is every item in order; an item that throws is
  // recorded as failed and the next one still runs.

  _batchFile(batchId) {
    return path.join(this.batchesDir, batchId, 'batch.json');
  }

  _itemFile(batchId, itemId) {
    return path.join(this.batchesDir, batchId, 'items', `${itemId}.json`);
  }

  createBatch(items, options, reviewItem) {
    if (!Array.isArray(items) || !items.length) throw new Error('items must be a non-empty array.');
    if (items.length > BATCH_MAX_ITEMS) throw new Error(`A batch holds at most ${BATCH_MAX_ITEMS} items.`);
    for (const it of items) if (!it || !ID_RE.test(String(it.recording_id ?? ''))) throw new Error('Every item needs a recording_id.');
    const batchId = randomUUID();
    const records = items.map((it, i) => ({
      item_id: `i${String(i + 1).padStart(2, '0')}`,
      batch_id: batchId,
      recording_id: it.recording_id,
      options: {
        explain: !!(it.explain ?? options.explain),
        submit: !!(it.submit ?? options.submit),
        // Sharing consent is per item and never inherited from the batch.
        consent: it.consent === true,
        selectedFileIds: Array.isArray(it.selected_file_ids) ? it.selected_file_ids.map(String) : [],
      },
      status: 'queued',
      started_at: null,
      finished_at: null,
      parsed: null,
      flags: [],
      ingested: null,
      error: null,
    }));
    const batch = { batch_id: batchId, status: 'queued', created_at: this.now(), finished_at: null, counts: this._counts(records), items: records.map(itemSummary) };
    writeJSON(this._batchFile(batchId), batch);
    for (const r of records) writeJSON(this._itemFile(batchId, r.item_id), r);
    const job = this.create('batch', batchId, async () => {
      this._saveBatch(batchId, records, 'running');
      for (const r of records) {
        r.status = 'running';
        r.started_at = this.now();
        writeJSON(this._itemFile(batchId, r.item_id), r);
        try {
          Object.assign(r, await reviewItem(r.recording_id, r.options));
          r.status = r.error ? 'failed' : 'succeeded';
        } catch (e) {
          r.status = 'failed';
          r.error = { stage: r.error?.stage ?? 'pipeline', message: e?.message ?? String(e) };
        }
        r.finished_at = this.now();
        writeJSON(this._itemFile(batchId, r.item_id), r);
        this._saveBatch(batchId, records, 'running');
      }
      const done = this._saveBatch(batchId, records, 'completed', this.now());
      return done.counts;
    });
    return { ...batch, job_id: job.id };
  }

  _counts(records) {
    const counts = { queued: 0, running: 0, succeeded: 0, failed: 0 };
    for (const r of records) counts[r.status]++;
    return counts;
  }

  _saveBatch(batchId, records, status, finishedAt = null) {
    const prev = readJSON(this._batchFile(batchId)) ?? {};
    const batch = { ...prev, batch_id: batchId, status, finished_at: finishedAt, counts: this._counts(records), items: records.map(itemSummary) };
    writeJSON(this._batchFile(batchId), batch);
    return batch;
  }

  getBatch(batchId) {
    return ID_RE.test(String(batchId)) ? readJSON(this._batchFile(batchId)) : null;
  }

  getItem(batchId, itemId) {
    return ID_RE.test(String(batchId)) && ID_RE.test(String(itemId)) ? readJSON(this._itemFile(batchId, itemId)) : null;
  }
}

function itemSummary(r) {
  return { item_id: r.item_id, recording_id: r.recording_id, status: r.status, flags: r.flags.length, ingested: !!r.ingested && !r.ingested.skipped, error: r.error?.message ?? null };
}

// The per-item chain: file reviewer → record reviewer → what to document.
// `actions` are the main.js functions the dashboard already calls; nothing
// here parses a file or validates a recording on its own.
export async function reviewItem(recordingId, { explain = false, submit = false, consent = false, selectedFileIds = [] } = {}, actions) {
  const out = { parsed: null, flags: [], ingested: null, error: null };
  const flag = (stage, name, detail, fileId = null) => out.flags.push({ stage, flag: name, ...(fileId ? { file_id: fileId } : {}), detail: detail ?? null });

  // 1. File reviewer: snapshot the documents seen during the session, then read
  //    back what was parsed and what could not be.
  let dir, manifest;
  try {
    dir = actions.recDir(recordingId);
    manifest = actions.readManifest(dir);
  } catch (e) {
    out.error = { stage: 'file_reviewer', message: e.code === 'ENOENT' ? 'Recording not found.' : e.message };
    return out;
  }
  try {
    if (!manifest.submitted && manifest.ended_at) await actions.collectDocuments(dir, manifest);
  } catch (e) {
    flag('file_reviewer', 'collect_failed', e.message);
  }
  const files = actions.readFiles(dir).map(actions.publicFile);
  for (const f of files) {
    if (f.snapshot_error) flag('file_reviewer', 'unreadable', f.snapshot_error, f.id);
    if (f.include === false) flag('file_reviewer', 'excluded_by_employee', null, f.id);
    if (f.ext && !FILE_TYPES[String(f.ext).toLowerCase()]) flag('file_reviewer', 'unsupported_type', f.ext, f.id);
  }
  const sections = actions.sectionsFor(recordingId);
  out.parsed = {
    files: files.map((f) => ({ id: f.id, name: f.name, ext: f.ext, content_type: f.content_type ?? null, size_bytes: f.size_bytes ?? null, sha256: f.sha256 ?? null, snapshot: f.snapshot ?? null, include: f.include !== false })),
    sections: sections.sections.length,
    apps: sections.apps.map((a) => a.app ?? a),
    processing: manifest.processing ?? null,
    summary: manifest.summary ?? null,
  };

  // 2. Record reviewer: the same validation Submit / Upload session runs, then
  //    the optional AI summary, then ingestion into the workspace.
  let valid = true;
  let intake = null;
  if (manifest.submitted) {
    valid = false;
    flag('record_reviewer', 'already_submitted', manifest.submitted.at);
  } else {
    try {
      intake = actions.validateSubmission(recordingId) ?? null;
    } catch (e) {
      valid = false;
      flag('record_reviewer', 'record_invalid', e.message);
    }
  }
  if (explain && !manifest.submitted) {
    try {
      const r = await actions.explainRecording(recordingId);
      if (r?.error) flag('record_reviewer', 'summary_unavailable', r.message ?? r.error);
      for (const s of r?.sections ?? []) if (s.review?.status === 'failed') flag('record_reviewer', 'summary_failed', s.review.error ?? null, s.id);
      if (r?.review?.session?.status === 'failed') flag('record_reviewer', 'summary_failed', r.review.session.error ?? null, 'session');
    } catch (e) {
      flag('record_reviewer', 'summary_failed', e.message);
    }
  }
  if (!submit) out.ingested = { skipped: 'submit not requested' };
  else if (!valid) out.ingested = { skipped: 'record flagged' };
  else if (intake?.consent_required && consent !== true) {
    flag('record_reviewer', 'consent_required', 'Upload session needs the employee\'s explicit consent: pass consent: true and the approved selected_file_ids on the item.');
    out.ingested = { skipped: 'consent required' };
  } else {
    try {
      const r = await actions.submitRecording(recordingId, { consent: consent === true, selectedFileIds });
      out.ingested = r.upload?.protocol === 2
        ? { submission_id: r.upload.submissionId ?? null, upload_status: r.upload.status ?? 'queued', analysis_status: 'not_started', publication_status: 'draft' }
        : { cloud_recording_id: r.submitted?.recording_id ?? null, files_uploaded: r.submitted?.files ?? 0, submitted_at: r.submitted?.at ?? null };
    } catch (e) {
      out.error = { stage: 'record_reviewer', message: e.message };
      out.ingested = { skipped: 'submit failed' };
    }
  }
  return out;
}
