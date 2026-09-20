import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { apiConfig, startApi } from '../src/api.js';
import { JobStore, reviewItem } from '../src/jobs.js';

const TOKEN = 'test-token-0123456789abcdef0123456789abcdef';

// Stand-in for the main.js functions: two recordings on disk, one finished.
function fakeActions(home) {
  const recordings = path.join(home, 'recordings');
  const manifests = {
    done1: { recording_id: 'done1', started_at: '2026-01-01T00:00:00Z', ended_at: '2026-01-01T00:10:00Z', processing: 'done', summary: { steps: 3 } },
    live2: { recording_id: 'live2', started_at: '2026-01-01T01:00:00Z', ended_at: null },
  };
  for (const [id, m] of Object.entries(manifests)) {
    fs.mkdirSync(path.join(recordings, id), { recursive: true });
    fs.writeFileSync(path.join(recordings, id, 'manifest.json'), JSON.stringify(m));
  }
  fs.writeFileSync(
    path.join(recordings, 'done1', 'files.json'),
    JSON.stringify({ files: [{ id: 'a'.repeat(12), path: '/x/a.xlsx', name: 'a.xlsx', ext: '.xlsx', snapshot: 'files/a/a.xlsx', sha256: 'h', size_bytes: 10, include: true }, { id: 'b'.repeat(12), path: '/x/b.pdf', name: 'b.pdf', ext: '.pdf', snapshot: null, snapshot_error: 'larger than 50 MB', include: true }] }),
  );
  const state = { state: 'idle', recordingId: null };
  const calls = [];
  return {
    calls,
    version: '0.1.0-test',
    status: () => ({ ...state }),
    startRecording: (opts) => {
      calls.push(['start', opts]);
      state.state = 'recording';
      state.recordingId = 'live2';
      return { ...state };
    },
    pauseRecording: () => ({ ...state, state: 'paused' }),
    resumeRecording: () => ({ ...state }),
    stopRecording: async (opts) => {
      calls.push(['stop', opts]);
      state.state = 'idle';
      return { ...state, recordingId: 'live2' };
    },
    setIntent: (text) => ({ ...state, intent: text }),
    listRecordings: () => Object.values(manifests).map((m) => ({ ...m, dir: path.join(recordings, m.recording_id) })),
    recDir: (id) => {
      if (!/^[a-zA-Z0-9_-]{1,128}$/.test(String(id))) throw new Error('Invalid recording ID.');
      return path.join(recordings, id);
    },
    sectionsFor(id) {
      this.readManifest(this.recDir(id));
      return { recording_id: id, sections: [{ id: 'S1', review: null }], apps: [{ app: 'Excel' }] };
    },
    readManifest: (dir) => JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8')),
    readReview: () => ({ threshold: 0.88, model: null, generated_at: null, generating: false, items: {} }),
    reviewSummary: () => ({ open: 0, total: 0 }),
    analyze: async (m) => {
      calls.push(['analyze', m.recording_id]);
      return { processing: 'done', summary: { steps: 3 } };
    },
    explainRecording: async (id) => ({ recording_id: id, sections: [{ id: 'S1', review: { status: 'proposed' } }], review: { session: { status: 'failed', error: 'model timeout' } } }),
    collectDocuments: async () => calls.push(['collect']),
    readFiles: (dir) => {
      try {
        return JSON.parse(fs.readFileSync(path.join(dir, 'files.json'), 'utf8')).files;
      } catch {
        return [];
      }
    },
    publicFile: ({ path: _p, ...rest }) => rest,
    reportBundle: (id) => {
      if (id !== 'done1') throw new Error('Wait until this session has finished analysis.');
    },
    submitRecording: async (id) => {
      calls.push(['submit', id]);
      return { submitted: { at: '2026-01-02T00:00:00Z', recording_id: 'cloud-1', files: 1 } };
    },
  };
}

async function serve(t) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vista-api-'));
  const actions = fakeActions(home);
  const jobs = new JobStore({ home, log: () => {} });
  const server = await startApi({ host: '127.0.0.1', port: 0, token: TOKEN, actions, jobs, log: () => {} });
  t.after(() => server.close());
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (method, p, body, token = TOKEN) => {
    const res = await fetch(base + p, { method, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    return { status: res.status, body: await res.json() };
  };
  const poll = async (p, done) => {
    for (let i = 0; i < 50; i++) {
      const r = await call('GET', p);
      if (done(r.body)) return r;
      await new Promise((r) => setTimeout(r, 10));
    }
    throw new Error(`timed out polling ${p}`);
  };
  return { home, actions, jobs, call, poll };
}

test('apiConfig reads host/port/token from the environment and stays off without a token', () => {
  assert.deepEqual(apiConfig({}), { token: '', host: '127.0.0.1', port: 47831 });
  assert.deepEqual(apiConfig({ VISTA_RECORDER_API_TOKEN: 't', VISTA_RECORDER_API_HOST: '0.0.0.0', VISTA_RECORDER_API_PORT: '9000' }), { token: 't', host: '0.0.0.0', port: 9000 });
  assert.throws(() => apiConfig({ VISTA_RECORDER_API_PORT: 'abc' }), /port number/);
  assert.throws(() => startApi({ token: '', actions: {}, jobs: {} }), /VISTA_RECORDER_API_TOKEN/);
});

test('requests without a valid bearer token are rejected', async (t) => {
  const { call } = await serve(t);
  assert.equal((await call('GET', '/health', null, null)).status, 401);
  assert.equal((await call('GET', '/health', null, 'wrong')).status, 401);
  assert.equal((await call('POST', '/recorder/start', {}, TOKEN + 'x')).status, 401);
  const ok = await call('GET', '/health');
  assert.equal(ok.status, 200);
  assert.deepEqual(ok.body, { ok: true, version: '0.1.0-test', recorder_state: 'idle' });
});

test('recorder control: start, status, stop (async job) without opening a window', async (t) => {
  const { call, poll, actions } = await serve(t);
  const start = await call('POST', '/recorder/start', { intent: 'invoices' });
  assert.equal(start.status, 200);
  assert.equal(start.body.state, 'recording');
  assert.equal(start.body.intent, 'invoices');
  assert.equal((await call('GET', '/recorder/status')).body.state, 'recording');
  const stop = await call('POST', '/recorder/stop');
  assert.equal(stop.status, 202);
  assert.match(stop.body.job_id, /^[0-9a-f-]{36}$/);
  const job = await poll(stop.body.poll, (j) => j.status === 'succeeded');
  assert.equal(job.body.kind, 'recorder_stop');
  assert.equal(job.body.result.recordingId, 'live2');
  assert.deepEqual(actions.calls.filter(([c]) => c === 'start' || c === 'stop'), [['start', { ui: false }], ['stop', { ui: false }]]);
  const list = await call('GET', '/recordings');
  assert.equal(list.status, 200);
  assert.equal(list.body.length, 2);
  assert.ok(list.body.every((r) => !('dir' in r)));
  assert.equal((await call('GET', '/recordings/done1')).body.recording_id, 'done1');
  assert.equal((await call('GET', '/recordings/nope')).status, 404);
  assert.equal((await call('GET', '/recordings/../x')).status, 404);
  assert.equal((await call('DELETE', '/recordings')).status, 405);
  assert.equal((await call('GET', '/jobs/not-a-job')).status, 404);
});

test('analysis bot and video summary run as polled jobs', async (t) => {
  const { call, poll, actions } = await serve(t);
  assert.equal((await call('POST', '/recordings/live2/analyze')).status, 409);
  const a = await call('POST', '/recordings/done1/analyze');
  assert.equal(a.status, 202);
  const done = await poll(a.body.poll, (j) => j.status !== 'queued' && j.status !== 'running');
  assert.equal(done.body.status, 'succeeded');
  assert.deepEqual(done.body.result, { processing: 'done', summary: { steps: 3 } });
  assert.deepEqual(actions.calls.filter(([c]) => c === 'analyze'), [['analyze', 'done1']]);
  const analysis = await call('GET', '/recordings/done1/analysis');
  assert.equal(analysis.body.processing, 'done');
  assert.equal((await call('GET', '/recordings/live2/analysis')).status, 404);

  assert.equal((await call('GET', '/recordings/done1/summary')).status, 404);
  const s = await call('POST', '/recordings/done1/summary', { force: true });
  assert.equal(s.status, 202);
  const sj = await poll(s.body.poll, (j) => j.status === 'succeeded');
  assert.equal(sj.body.result.recording_id, 'done1');
  assert.equal(sj.body.result.source, 'local');
});

test('a batch runs every item through file reviewer → record reviewer → written record; one bad item does not stop the rest', async (t) => {
  const { call, poll, actions, home } = await serve(t);
  assert.equal((await call('POST', '/batches', { items: [] })).status, 400);
  assert.equal((await call('POST', '/batches', { items: [{}] })).status, 400);
  const b = await call('POST', '/batches', { items: [{ recording_id: 'done1', submit: true }, { recording_id: 'missing' }, { recording_id: 'live2' }], explain: true });
  assert.equal(b.status, 202);
  assert.match(b.body.batch_id, /^[0-9a-f-]{36}$/);
  assert.deepEqual(b.body.items.map((i) => [i.item_id, i.status]), [['i01', 'queued'], ['i02', 'queued'], ['i03', 'queued']]);
  const batch = await poll(b.body.poll, (x) => x.status === 'completed');
  assert.deepEqual(batch.body.counts, { queued: 0, running: 0, succeeded: 2, failed: 1 });
  assert.deepEqual(batch.body.items.map((i) => [i.item_id, i.status, i.ingested]), [['i01', 'succeeded', true], ['i02', 'failed', false], ['i03', 'succeeded', false]]);

  const i1 = (await call('GET', `/batches/${b.body.batch_id}/items/i01`)).body;
  assert.equal(i1.parsed.files.length, 2);
  assert.equal(i1.parsed.processing, 'done');
  assert.deepEqual(i1.flags.map((f) => [f.stage, f.flag, f.file_id ?? null]), [
    ['file_reviewer', 'unreadable', 'b'.repeat(12)],
    ['record_reviewer', 'summary_failed', 'session'],
  ]);
  assert.deepEqual(i1.ingested, { cloud_recording_id: 'cloud-1', files_uploaded: 1, submitted_at: '2026-01-02T00:00:00Z' });
  assert.equal(i1.error, null);
  assert.ok(fs.existsSync(path.join(home, 'batches', b.body.batch_id, 'items', 'i01.json')));

  const i2 = (await call('GET', `/batches/${b.body.batch_id}/items/i02`)).body;
  assert.equal(i2.status, 'failed');
  assert.equal(i2.error.stage, 'file_reviewer');
  assert.equal(i2.error.message, 'Recording not found.');

  const i3 = (await call('GET', `/batches/${b.body.batch_id}/items/i03`)).body;
  assert.deepEqual(i3.flags.map((f) => f.flag), ['record_invalid', 'summary_failed']);
  assert.deepEqual(i3.ingested, { skipped: 'submit not requested' });
  assert.deepEqual(actions.calls.filter(([c]) => c === 'submit'), [['submit', 'done1']]);
  assert.equal((await call('GET', `/batches/${b.body.batch_id}/items/i09`)).status, 404);
  assert.equal((await call('GET', '/batches/nope')).status, 404);
});

test('reviewItem skips ingestion for flagged records and records submit failures on the item', async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vista-api-'));
  const actions = fakeActions(home);
  const flagged = await reviewItem('live2', { submit: true }, actions);
  assert.deepEqual(flagged.ingested, { skipped: 'record flagged' });
  actions.submitRecording = async () => {
    throw new Error('This computer is not connected to a workspace yet. Ask your Vista admin.');
  };
  const failed = await reviewItem('done1', { submit: true }, actions);
  assert.equal(failed.error.stage, 'record_reviewer');
  assert.deepEqual(failed.ingested, { skipped: 'submit failed' });
  assert.equal(failed.parsed.files.length, 2);
});

test('jobs interrupted by a restart are marked failed, finished jobs stay readable', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vista-api-'));
  fs.mkdirSync(path.join(home, 'jobs'));
  fs.writeFileSync(path.join(home, 'jobs', 'j1.json'), JSON.stringify({ id: 'j1', kind: 'summary', status: 'running' }));
  fs.writeFileSync(path.join(home, 'jobs', 'j2.json'), JSON.stringify({ id: 'j2', kind: 'analyze', status: 'succeeded', result: 1 }));
  const jobs = new JobStore({ home, log: () => {} });
  assert.equal(jobs.get('j1').status, 'failed');
  assert.match(jobs.get('j1').error, /restarted/);
  assert.equal(jobs.get('j2').result, 1);
  assert.equal(jobs.get('../j2'), null);
});
