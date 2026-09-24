import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { buildSubmissionPackage, CONSENT_VERSION, deviceId, packageLeakage, discoverWorkspaces, planPreview, selectWorkspace, SubmissionQueue, uploadBinding } from '../src/intake.js';
import { buildSections, parseEvents } from '../src/sections.js';

const sha = (data) => createHash('sha256').update(data).digest('hex');
const docId = 'abcdef123456';
function fixture() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vista-intake-'));
  const root = path.join(home, 'recordings');
  const dir = path.join(root, 'session-1');
  fs.mkdirSync(path.join(dir, 'files', docId), { recursive: true });
  fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify({
    recording_id: 'session-1', started_at: '2026-09-19T09:00:00Z', ended_at: '2026-09-19T09:10:00Z',
    active_seconds: 600, processing: 'skipped', user: 'PRIVATE_USER', settings: { openaiApiKey: 'PRIVATE_MODEL_KEY' },
  }));
  fs.writeFileSync(path.join(dir, 'events.jsonl'), [
    { timestamp: '2026-09-19T09:01:00Z', event_type: 'focus', app: 'Excel', window_title: 'PRIVATE_TITLE', url: 'https://private.example', text: 'PRIVATE_CLIPBOARD' },
    { timestamp: '2026-09-19T09:02:00Z', event_type: 'key', app: 'Excel', text: 'PRIVATE_TYPED', payload: { key: 'A' } },
    { timestamp: '2026-09-19T09:03:00Z', event_type: 'screen', app: 'Excel', payload: { image: 'shots/1.jpg' } },
    { timestamp: '2026-09-19T09:04:00Z', event_type: 'focus', app: '[Private]' },
  ].map((e) => JSON.stringify(e)).join('\n'));
  fs.writeFileSync(path.join(dir, 'screen.webm'), 'PRIVATE_VIDEO');
  fs.writeFileSync(path.join(dir, 'files', docId, 'invoice.csv'), 'invoice,total\nINV-1,125.00\n');
  fs.writeFileSync(path.join(dir, 'files.json'), JSON.stringify({ version: 1, files: [
    { id: docId, name: 'invoice.csv', ext: '.csv', include: true, snapshot: `files/${docId}/invoice.csv`, path: '/PRIVATE_PATH/invoice.csv' },
  ] }));
  const config = {
    protocol: 2, url: 'https://vista.example', userId: randomUUID(), tenantId: randomUUID(), deviceId: deviceId(home),
    workspace: { id: randomUUID(), kind: 'company' }, token: 'PRIVATE_ACCESS_KEY'.repeat(4),
    consentVersion: CONSENT_VERSION,
  };
  return { home, root, dir, config, cleanup: () => fs.rmSync(home, { recursive: true, force: true }) };
}

function server({ failDocumentOnce = false, badReceipt = false } = {}) {
  const calls = [];
  const objects = new Map();
  let manifest, accepted = false;
  const id = randomUUID();
  const response = () => ({
    id, source_id: manifest.source_id, device_id: manifest.device_id, workspace: manifest.workspace,
    manifest_hash: sha(JSON.stringify(manifest)), upload_status: accepted ? 'accepted' : 'uploading',
    analysis_status: 'not_started', publication_status: 'draft',
    receipt: accepted ? { submission_id: id, manifest_hash: badReceipt ? 'bad' : sha(JSON.stringify(manifest)), artifact_count: manifest.artifacts.length, verified_at: new Date().toISOString() } : null,
  });
  const fetchImpl = async (url, options) => {
    calls.push({ url, method: options.method });
    const pathname = new URL(url).pathname;
    if (url.startsWith('https://storage.example/')) {
      const artifactId = pathname.slice(1);
      if (artifactId.startsWith('document') && failDocumentOnce) {
        failDocumentOnce = false;
        return new Response('unavailable', { status: 503 });
      }
      objects.set(artifactId, options.body);
      return new Response('', { status: 200 });
    }
    if (pathname === '/api/recorder/submissions') {
      const next = JSON.parse(options.body);
      if (manifest) assert.deepEqual(next, manifest);
      manifest = next;
      return Response.json(response(), { status: 201 });
    }
    if (pathname.endsWith('/upload-urls')) return Response.json({ uploads: manifest.artifacts.map((a) => ({
      artifact_id: a.id, url: `https://storage.example/${a.id}`, headers: { 'Content-Type': a.content_type },
    })) });
    if (pathname.endsWith('/complete')) {
      for (const spec of manifest.artifacts) assert.equal(sha(objects.get(spec.id)), spec.sha256);
      accepted = true;
      return Response.json(response());
    }
    // Cloud analysis: the report lives on the server; the queue caches what it is told.
    if (pathname === `/api/recorder/submissions/${id}`) return Response.json({ ...response(), ...cloud });
    if (pathname === `/api/recorder/submissions/${id}/answers`) {
      const { answers } = JSON.parse(options.body);
      for (const q of cloud.report.questions) if (answers[q.id] !== undefined) q.answer = answers[q.id];
      return Response.json({ ...response(), ...cloud });
    }
    if (pathname === `/api/recorder/submissions/${id}/publish`) {
      assert.deepEqual(JSON.parse(options.body), { consent: true });
      cloud.publication_status = 'published';
      cloud.report.status = 'published';
      return Response.json({ ...response(), ...cloud });
    }
    if (pathname === `/api/recorder/submissions/${id}/analyze`) {
      cloud.analysis_status = 'queued';
      return Response.json({ ...response(), ...cloud });
    }
    throw new Error(`Unexpected request ${pathname}`);
  };
  const cloud = { analysis_status: 'queued', analysis_error: null, analysis_run_id: null, publication_status: 'draft', report: null };
  return { calls, objects, fetchImpl, cloud };
}

test('key-only enrollment resolves authorized companies and never guesses among several', async () => {
  const f = fixture();
  try {
    const w1 = { id: randomUUID(), kind: 'company', name: 'Company A' };
    const w2 = { id: randomUUID(), kind: 'deal', name: 'Company B' };
    const identity = await discoverWorkspaces(f.config, async (url, options) => {
      assert.equal(url, 'https://vista.example/api/recorder/workspaces');
      assert.equal(options.headers.Authorization, `Bearer ${f.config.token}`);
      return Response.json({ user_id: f.config.userId, tenant_id: f.config.tenantId, email: 'employee@example.com', workspaces: [w1, w2] });
    });
    assert.throws(() => selectWorkspace(identity), /Choose the company/);
    assert.deepEqual(selectWorkspace(identity, w2), w2);
    assert.throws(() => selectWorkspace(identity, { id: randomUUID(), kind: 'company' }), /authorized/);
    assert.deepEqual(selectWorkspace({ ...identity, workspaces: [w1] }), w1);
    assert.equal(deviceId(f.home), f.config.deviceId);
  } finally { f.cleanup(); }
});

test('an unprocessed session uploads only metadata and explicitly selected documents', () => {
  const f = fixture();
  try {
    const pack = buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true });
    assert.equal(pack.manifest.format_version, 2);
    assert.equal(pack.manifest.artifacts.length, 1);
    const data = pack.data.get('activity').toString();
    assert.ok(!data.includes('PRIVATE'));
    const events = JSON.parse(data).events;
    assert.equal(events.length, 2);
    assert.deepEqual(Object.keys(events[0]).sort(), ['app', 'count', 'event_type', 'timestamp']);
    const selected = buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true, selectedFileIds: [docId] });
    assert.equal(selected.manifest.artifacts.length, 2);
    assert.equal(selected.data.get(`document-${docId}`).toString(), 'invoice,total\nINV-1,125.00\n');
    assert.ok(!JSON.stringify(selected.manifest).includes('PRIVATE'));
    assert.throws(() => buildSubmissionPackage(f.root, 'session-1', f.config), /Confirm/);
    assert.throws(() => buildSubmissionPackage(f.root, '../other', f.config, { consent: true }), /Invalid recording/);
    assert.throws(() => buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true, selectedFileIds: ['unknown'] }), /snapshot/);
  } finally { f.cleanup(); }
});

test('the plan graph is uploaded only when ticked, after the employee\'s edits, and carries no values', () => {
  const f = fixture();
  try {
    fs.appendFileSync(path.join(f.dir, 'events.jsonl'), '\n' + [
      { timestamp: '2026-09-19T09:05:00Z', event_type: 'copy', app: 'Excel', element: 'B2', text: 'PRIVATE_CLIPBOARD', payload: { clip_hash: 'h1', chars: 6 } },
      { timestamp: '2026-09-19T09:06:00Z', event_type: 'focus', app: 'QuickBooks', window_title: 'PRIVATE_TITLE', url: 'https://private.example/bill' },
      { timestamp: '2026-09-19T09:06:30Z', event_type: 'click', app: 'QuickBooks', payload: { x: 500, y: 301 } },
      { timestamp: '2026-09-19T09:07:00Z', event_type: 'paste', app: 'QuickBooks', element: 'Amount', text: 'PRIVATE_CLIPBOARD', payload: { clip_hash: 'h1' } },
      { timestamp: '2026-09-19T09:08:00Z', event_type: 'shortcut', app: 'QuickBooks', text: 'Cmd+S' },
    ].map((e) => JSON.stringify(e)).join('\n'));
    assert.equal(buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true }).manifest.artifacts.length, 1);
    assert.ok(planPreview(f.root, 'session-1').moves >= 4);
    const pack = buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true, sharePlan: true });
    const spec = pack.manifest.artifacts.find((a) => a.kind === 'plan');
    assert.equal(spec.filename, 'plan.json');
    assert.equal(spec.id, 'plan');
    const text = pack.data.get('plan').toString();
    for (const s of ['PRIVATE', 'private.example', '500', '301', 'h1', 'Excel', 'QuickBooks']) assert.ok(!text.includes(s), `plan leaks ${s}`);
    const plan = JSON.parse(text);
    assert.equal(plan.compiled_by, 'recorder-plan/3');
    assert.equal(pack.manifest.consent_version, CONSENT_VERSION);
    assert.deepEqual([...new Set(plan.nodes.map((n) => n.app_role))].sort(), ['accounting', 'spreadsheet']);
    const submit = plan.edges.find((e) => e.action_class === 'submit');
    assert.equal(submit.policy, 'always_ask');
    const click = plan.edges.find((e) => e.action_class === 'click');
    fs.writeFileSync(path.join(f.dir, 'plan-edits.json'), JSON.stringify({ [click.id]: { excluded: true } }));
    const edited = JSON.parse(buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true, sharePlan: true }).data.get('plan'));
    assert.ok(!edited.edges.some((e) => e.id === click.id));
    assert.equal(edited.edges.length, plan.edges.length - 1);
  } finally { f.cleanup(); }
});

test('a plan is never packaged under an older consent version; activity metadata still is', () => {
  const f = fixture();
  try {
    const stale = { ...f.config, consentVersion: 'activity-metadata-v1' };
    assert.equal(buildSubmissionPackage(f.root, 'session-1', stale, { consent: true }).manifest.consent_version, 'activity-metadata-v1');
    assert.throws(() => buildSubmissionPackage(f.root, 'session-1', stale, { consent: true, sharePlan: true }), /Reconnect/);
    assert.throws(() => buildSubmissionPackage(f.root, 'session-1', { ...f.config, consentVersion: 'made-up' }, { consent: true }), /Reconnect/);
  } finally { f.cleanup(); }
});

test('a recorded value or title surviving into the cloud-bound payload fails the privacy check without being echoed', () => {
  const f = fixture();
  try {
    fs.appendFileSync(path.join(f.dir, 'events.jsonl'), '\n' + [
      { timestamp: '2026-09-19T09:06:00Z', event_type: 'focus', app: 'QuickBooks', window_title: 'PRIVATE_TITLE', url: 'https://private.example/bill' },
      { timestamp: '2026-09-19T09:07:00Z', event_type: 'paste', app: 'QuickBooks', element: 'Amount', text: 'PRIVATE_CLIPBOARD', payload: { clip_hash: 'h1' } },
    ].map((e) => JSON.stringify(e)).join('\n'));
    const raw = fs.readFileSync(path.join(f.dir, 'events.jsonl'), 'utf8');
    const manifest = JSON.parse(fs.readFileSync(path.join(f.dir, 'manifest.json'), 'utf8'));
    const clean = buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true, sharePlan: true });
    assert.ok(packageLeakage({ plan: JSON.parse(clean.data.get('plan')) }, raw, manifest).ok);
    for (const leaked of [{ note: 'PRIVATE_CLIPBOARD' }, { title: 'PRIVATE_TITLE' }, { name: 'not-a-normaliser-token {x}' }]) {
      const report = packageLeakage({ plan: leaked }, raw, manifest);
      assert.equal(report.ok, false);
      assert.ok(!JSON.stringify(report.failures).includes('PRIVATE'), 'the report must not echo the value');
    }
  } finally { f.cleanup(); }
});

test('excluded sections and paused intervals are not included in activity metadata', () => {
  const f = fixture();
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(f.dir, 'manifest.json'), 'utf8'));
    const raw = fs.readFileSync(path.join(f.dir, 'events.jsonl'), 'utf8');
    const sections = buildSections(parseEvents(raw), manifest);
    fs.writeFileSync(path.join(f.dir, 'sections.json'), JSON.stringify({ [sections[0].id]: { excluded: true } }));
    const excluded = buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true });
    assert.equal(JSON.parse(excluded.data.get('activity')).events.length, 0);
    fs.writeFileSync(path.join(f.dir, 'sections.json'), '{}');
    manifest.pauses = [{ start: '2026-09-19T09:01:30Z', end: '2026-09-19T09:03:00Z' }];
    fs.writeFileSync(path.join(f.dir, 'manifest.json'), JSON.stringify(manifest));
    const paused = buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true });
    assert.deepEqual(JSON.parse(paused.data.get('activity')).events.map((e) => e.event_type), ['focus']);
  } finally { f.cleanup(); }
});

test('snapshots outside the recording directory and workspace changes are rejected', () => {
  const f = fixture();
  try {
    const snapshot = path.join(f.dir, 'files', docId, 'invoice.csv');
    fs.unlinkSync(snapshot);
    const external = path.join(f.home, 'outside.csv');
    fs.writeFileSync(external, 'private');
    fs.symlinkSync(external, snapshot);
    assert.throws(() => buildSubmissionPackage(f.root, 'session-1', f.config, { consent: true, selectedFileIds: [docId] }), /outside/);
    fs.writeFileSync(path.join(f.dir, 'workspace-binding.json'), JSON.stringify(uploadBinding(f.config)));
    assert.throws(() => buildSubmissionPackage(f.root, 'session-1', { ...f.config, workspace: { id: randomUUID(), kind: 'company' } }, { consent: true }), /Reconnect/);
  } finally { f.cleanup(); }
});

test('upload queue survives restart, resumes missing artifacts, keeps originals and does not persist credentials', async () => {
  const f = fixture();
  try {
    const remote = server({ failDocumentOnce: true });
    const queue = new SubmissionQueue(f.home, { fetchImpl: remote.fetchImpl });
    queue.enqueue(f.root, 'session-1', f.config, { consent: true, selectedFileIds: [docId] });
    await queue.flush(f.config, { force: true });
    const failed = queue.entries()[0];
    assert.equal(failed.status, 'failed');
    assert.deepEqual(failed.uploaded, ['activity'], failed.error);
    assert.equal(fs.existsSync(path.join(f.dir, 'events.jsonl')), true);
    assert.ok(!JSON.stringify(failed).includes(f.config.token));
    assert.ok(!JSON.stringify(failed).includes('https://storage.example'));
    const resumed = new SubmissionQueue(f.home, { fetchImpl: remote.fetchImpl });
    await resumed.flush(f.config, { force: true });
    const accepted = resumed.entries()[0];
    assert.equal(accepted.status, 'accepted');
    assert.equal(accepted.submission.analysis_status, 'not_started');
    assert.equal(accepted.submission.publication_status, 'draft');
    assert.equal(remote.calls.filter((c) => c.method === 'PUT' && c.url.endsWith('/activity')).length, 1);
    const count = remote.calls.length;
    await resumed.flush(f.config, { force: true });
    assert.equal(remote.calls.length, count);
    assert.equal(fs.existsSync(path.join(f.dir, 'events.jsonl')), true);
  } finally { f.cleanup(); }
});

test('an offline queue never uploads using another account or company', async () => {
  const f = fixture();
  try {
    const remote = server();
    const queue = new SubmissionQueue(f.home, { fetchImpl: async () => { throw new Error('Offline'); } });
    queue.enqueue(f.root, 'session-1', f.config, { consent: true });
    await queue.flush(f.config, { force: true });
    assert.equal(queue.entries()[0].status, 'failed');
    const resumed = new SubmissionQueue(f.home, { fetchImpl: remote.fetchImpl });
    await resumed.flush({ ...f.config, userId: randomUUID() }, { force: true });
    await resumed.flush({ ...f.config, workspace: { id: randomUUID(), kind: 'company' } }, { force: true });
    assert.equal(remote.calls.length, 0);
    await resumed.flush(f.config, { force: true });
    assert.equal(resumed.entries()[0].status, 'accepted');
  } finally { f.cleanup(); }
});

test('disconnecting pauses the package before the next artifact and reconnecting resumes it', async () => {
  const f = fixture();
  try {
    const remote = server();
    let connected = true;
    const queue = new SubmissionQueue(f.home, {
      isCurrent: () => connected,
      fetchImpl: async (url, options) => {
        const response = await remote.fetchImpl(url, options);
        if (options.method === 'PUT' && url.endsWith('/activity')) connected = false;
        return response;
      },
    });
    queue.enqueue(f.root, 'session-1', f.config, { consent: true, selectedFileIds: [docId] });
    await queue.flush(f.config, { force: true });
    assert.equal(queue.entries()[0].status, 'failed');
    assert.match(queue.entries()[0].error, /paused/);
    assert.equal(remote.calls.some((c) => c.url.endsWith('/complete')), false);
    connected = true;
    await queue.flush(f.config, { force: true });
    assert.equal(queue.entries()[0].status, 'accepted');
  } finally { f.cleanup(); }
});

test('a lost completion response is recovered from the same receipt without uploading again', async () => {
  const f = fixture();
  try {
    const remote = server();
    let loseResponse = true;
    const queue = new SubmissionQueue(f.home, { fetchImpl: async (url, options) => {
      const response = await remote.fetchImpl(url, options);
      if (url.endsWith('/complete') && loseResponse) { loseResponse = false; throw new Error('Connection lost'); }
      return response;
    } });
    queue.enqueue(f.root, 'session-1', f.config, { consent: true });
    await queue.flush(f.config, { force: true });
    assert.equal(queue.entries()[0].status, 'failed');
    await queue.flush(f.config, { force: true });
    assert.equal(queue.entries()[0].status, 'accepted');
    assert.equal(remote.calls.filter((c) => c.method === 'PUT').length, 1);
  } finally { f.cleanup(); }
});

test('authentication failures pause automatic retries and preserve the package', async () => {
  const f = fixture();
  try {
    let calls = 0;
    const queue = new SubmissionQueue(f.home, { fetchImpl: async () => {
      calls++;
      return Response.json({ detail: 'Invalid access key' }, { status: 401 });
    } });
    queue.enqueue(f.root, 'session-1', f.config, { consent: true });
    await queue.flush(f.config);
    assert.equal(queue.entries()[0].status, 'failed');
    await queue.flush(f.config);
    assert.equal(calls, 1);
    assert.equal(fs.existsSync(path.join(f.dir, 'events.jsonl')), true);
  } finally { f.cleanup(); }
});

test('a mismatched receipt is not accepted and never deletes local evidence', async () => {
  const f = fixture();
  try {
    const remote = server({ badReceipt: true });
    const queue = new SubmissionQueue(f.home, { fetchImpl: remote.fetchImpl });
    queue.enqueue(f.root, 'session-1', f.config, { consent: true });
    await queue.flush(f.config, { force: true });
    assert.equal(queue.entries()[0].status, 'failed');
    assert.equal(queue.entries()[0].receipt, null);
    assert.equal(fs.existsSync(path.join(f.dir, 'events.jsonl')), true);
  } finally { f.cleanup(); }
});


test('accepted uploads poll for analysis, cache the draft report, take answers and publish only with consent', async () => {
  const f = fixture();
  try {
    let clock = 1_000_000;
    const srv = server();
    const changes = [];
    const queue = new SubmissionQueue(f.home, { fetchImpl: srv.fetchImpl, now: () => clock, onChange: (s) => changes.push(s.analysis?.status ?? null) });
    queue.enqueue(f.root, 'session-1', f.config, { consent: true });
    await queue.flush(f.config);
    assert.equal(queue.entries()[0].status, 'accepted');
    assert.throws(() => queue.acceptedEntry(f.config, 'session-2'), /Upload this session/);

    await queue.refresh(f.config);
    assert.equal(queue.entries()[0].analysis.status, 'queued');
    const polls = () => srv.calls.filter((c) => c.method === undefined && /submissions\/[0-9a-f-]{36}$/.test(c.url)).length;
    assert.equal(polls(), 1);
    await queue.refresh(f.config); // too soon: no second poll
    assert.equal(polls(), 1);
    clock += 31000;
    srv.cloud.analysis_status = 'succeeded';
    srv.cloud.report = { id: 'r1', status: 'draft', questions: [{ id: 'q1', question: 'Which task?', answer: null }], observed: { switches: 3 } };
    await queue.refresh(f.config);
    assert.equal(polls(), 2);
    assert.equal(queue.entries()[0].analysis.status, 'succeeded');
    assert.equal(queue.entries()[0].analysis.report.observed.switches, 3);
    clock += 31000;
    await queue.refresh(f.config); // terminal: polling stops until forced
    assert.equal(polls(), 2);
    await queue.refresh(f.config, { force: true });
    assert.equal(polls(), 3);

    assert.throws(() => queue.answer(f.config, 'session-1', {}), /at least one/);
    const answered = await queue.answer(f.config, 'session-1', { q1: 'Month-end close' });
    assert.equal(answered.report.questions[0].answer, 'Month-end close');
    assert.throws(() => queue.publish(f.config, 'session-1', {}), /Confirm/);
    assert.equal(queue.entries()[0].analysis.publication, 'draft');
    const published = await queue.publish(f.config, 'session-1', { consent: true });
    assert.equal(published.publication, 'published');
    assert.equal(queue.entries()[0].analysis.publication, 'published');
    const requeued = await queue.reanalyze(f.config, 'session-1');
    assert.equal(requeued.status, 'queued');
    assert.ok(changes.includes('queued') && changes.includes('succeeded'));
    const other = { ...f.config, workspace: { id: randomUUID(), kind: 'company' } };
    assert.throws(() => queue.acceptedEntry(other, 'session-1'), /Reconnect the workspace/);
    assert.ok(!JSON.stringify(queue.entries()).includes('PRIVATE'));
  } finally { f.cleanup(); }
});
