// Agent-facing HTTP API. Runs in the main process next to the tray, with or
// without a window, so an external agent can drive the recorder headlessly.
// Every handler calls the same main.js function the dashboard calls (passed in
// as `actions`); long work goes through the JobStore and is polled at /jobs/:id.
// Auth: `Authorization: Bearer <token>` where the token comes from the
// environment (VISTA_RECORDER_API_TOKEN); the server refuses to start without one.
import { timingSafeEqual } from 'node:crypto';
import http from 'node:http';

import { reviewItem } from './jobs.js';

export const DEFAULT_HOST = '127.0.0.1';
export const DEFAULT_PORT = 47831;
export const BODY_LIMIT = 1024 * 1024;

export function apiConfig(env = process.env) {
  const token = env.VISTA_RECORDER_API_TOKEN ?? '';
  const port = Number(env.VISTA_RECORDER_API_PORT ?? DEFAULT_PORT);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('VISTA_RECORDER_API_PORT must be a port number.');
  return { token, host: env.VISTA_RECORDER_API_HOST || DEFAULT_HOST, port };
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function authorized(header, token) {
  const m = /^Bearer\s+(\S+)$/.exec(String(header ?? ''));
  if (!m || !token) return false;
  const a = Buffer.from(m[1]), b = Buffer.from(token);
  return a.length === b.length && timingSafeEqual(a, b);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > BODY_LIMIT) {
        reject(new ApiError(413, 'Request body too large.'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try {
        const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
        resolve(parsed);
      } catch {
        reject(new ApiError(400, 'Body must be a JSON object.'));
      }
    });
    req.on('error', reject);
  });
}

// Messages main.js throws for bad input map to 4xx; anything else is a 500.
function statusFor(message) {
  const m = String(message);
  if (/not found|No copy|ENOENT/i.test(m)) return 404;
  if (/^Invalid|must be|needs a|at most|non-empty/i.test(m)) return 400;
  if (/already|Stop the recording first|Wait until|was submitted|^Grant /i.test(m)) return 409;
  return 500;
}

class Reply {
  constructor(status, body) {
    this.status = status;
    this.body = body;
  }
}
const accepted = (job) => new Reply(202, { job_id: job.id, status: job.status, poll: `/jobs/${job.id}` });
const notFound = (what) => {
  throw new ApiError(404, `${what} not found.`);
};

// Route table: method + path pattern → handler({ params, body, query }) returning
// a body (200) or a Reply with its own status.
export function routes(actions, jobs) {
  const item = (recordingId, options) => reviewItem(recordingId, options, actions);
  return [
    ['GET', /^\/health$/, () => ({ ok: true, version: actions.version, recorder_state: actions.status().state })],

    ['POST', /^\/recorder\/start$/, ({ body }) => {
      const status = actions.startRecording({ ui: false });
      return body.intent && status.state !== 'idle' ? actions.setIntent(body.intent) : status;
    }],
    ['POST', /^\/recorder\/pause$/, () => actions.pauseRecording()],
    ['POST', /^\/recorder\/resume$/, () => actions.resumeRecording()],
    ['POST', /^\/recorder\/stop$/, () => accepted(jobs.create('recorder_stop', actions.status().recordingId, () => actions.stopRecording({ ui: false })))],
    ['GET', /^\/recorder\/status$/, () => actions.status()],

    ['GET', /^\/recordings$/, ({ query }) => {
      const rows = actions.listRecordings().map(({ dir: _d, ...r }) => r);
      const state = query.get('state');
      if (state === 'pending') return rows.filter((r) => !r.submitted);
      if (state === 'submitted') return rows.filter((r) => r.submitted);
      return rows;
    }],
    ['GET', /^\/recordings\/([^/]+)$/, ({ params: [id] }) => actions.sectionsFor(id)],

    ['POST', /^\/recordings\/([^/]+)\/analyze$/, ({ params: [id] }) => {
      const manifest = actions.readManifest(actions.recDir(id));
      if (!manifest.ended_at) throw new ApiError(409, 'Stop the recording first.');
      if (manifest.submitted) throw new ApiError(409, 'This session was submitted; analysis lives in the workspace.');
      return accepted(jobs.create('analyze', id, () => actions.analyze(manifest)));
    }],
    ['GET', /^\/recordings\/([^/]+)\/analysis$/, ({ params: [id] }) => {
      const m = actions.readManifest(actions.recDir(id));
      if (!m.processing) notFound('Analysis');
      return { recording_id: id, processing: m.processing, processing_note: m.processing_note ?? null, summary: m.summary ?? null };
    }],

    ['POST', /^\/recordings\/([^/]+)\/summary$/, ({ params: [id], body }) => {
      actions.readManifest(actions.recDir(id));
      return accepted(jobs.create('summary', id, async () => {
        const r = await actions.explainRecording(id, { force: !!body.force });
        if (r?.error) throw new Error(r.message ?? r.error);
        return summaryOf(id, actions.readReview(actions.recDir(id)), actions);
      }));
    }],
    ['GET', /^\/recordings\/([^/]+)\/summary$/, ({ params: [id] }) => {
      const review = actions.readReview(actions.recDir(id));
      if (!review.generated_at && !review.generating && !Object.keys(review.items ?? {}).length) notFound('Summary');
      return summaryOf(id, review, actions);
    }],

    // File agent + insights (flags, keyboard/mouse analysis, trends): computed after Stop, re-runnable.
    ['GET', /^\/recordings\/([^/]+)\/insights$/, ({ params: [id] }) => {
      const s = actions.sectionsFor(id);
      return { recording_id: id, insights: s.insights, documents: s.files.map((f) => ({ id: f.id, name: f.name, include: f.include !== false, review: f.review ?? null })) };
    }],
    ['POST', /^\/recordings\/([^/]+)\/insights$/, ({ params: [id], body }) => {
      const manifest = actions.readManifest(actions.recDir(id));
      if (!manifest.ended_at) throw new ApiError(409, 'Stop the recording first.');
      if (manifest.submitted) throw new ApiError(409, 'This session was submitted.');
      return accepted(jobs.create('insights', id, async () => {
        await actions.runAgents(id, { force: !!body.force });
        return actions.sectionsFor(id).insights;
      }));
    }],
    ['POST', /^\/recordings\/([^/]+)\/flags\/([^/]+)$/, ({ params: [id, flagId], body }) => {
      if (!body.decision) throw new ApiError(400, 'decision (confirmed|dismissed) is required.');
      return actions.decideFlag(id, flagId, String(body.decision)).insights;
    }],
    ['POST', /^\/recordings\/([^/]+)\/insights\/approve$/, ({ params: [id], body }) => actions.approveInsights(id, body.approved !== false).insights],
    ['POST', /^\/recordings\/([^/]+)\/sections\/([^/]+)\/exclude$/, ({ params: [id, sectionId], body }) => {
      const s = actions.excludeSection(id, sectionId, body.excluded !== false);
      return s.sections.find((x) => x.id === sectionId) ?? notFound('Section');
    }],

    // Computer-use sessions: the same consent contract as uploads — nothing starts without
    // `consent: true`, which the caller may only send after showing the employee the notice.
    ['GET', /^\/computer-use\/status$/, () => actions.computerUse.status()],
    ['GET', /^\/computer-use\/sessions$/, () => actions.computerUse.tick()],
    ['POST', /^\/computer-use\/sessions\/([^/]+)\/start$/, async ({ params: [id], body }) => {
      if (body.consent !== true) throw new ApiError(400, 'Employee consent is required before a computer-use session: pass consent: true after showing the notice.');
      try {
        return await actions.computerUse.start(id, { consent: true, shareScreenshots: body.share_screenshots === true });
      } catch (e) {
        throw new ApiError(e.code === 'busy' ? 409 : e.code ? 400 : 500, e.message);
      }
    }],
    ['POST', /^\/computer-use\/sessions\/([^/]+)\/stop$/, ({ body }) => actions.computerUse.stop(body.reason || 'stopped through the agent API')],
    ['GET', /^\/computer-use\/sessions\/([^/]+)\/steps$/, ({ params: [id] }) => ({ session_id: id, steps: actions.computerUse.steps(id) })],
    ['GET', /^\/jobs\/([^/]+)$/, ({ params: [id] }) => jobs.get(id) ?? notFound('Job')],

    ['POST', /^\/batches$/, ({ body }) => {
      const batch = jobs.createBatch(body.items, { explain: !!body.explain, submit: !!body.submit }, item);
      return new Reply(202, { ...batch, poll: `/batches/${batch.batch_id}` });
    }],
    ['GET', /^\/batches\/([^/]+)$/, ({ params: [id] }) => jobs.getBatch(id) ?? notFound('Batch')],
    ['GET', /^\/batches\/([^/]+)\/items\/([^/]+)$/, ({ params: [batchId, itemId] }) => jobs.getItem(batchId, itemId) ?? notFound('Batch item')],
  ];
}

function summaryOf(recordingId, review, actions) {
  const summary = actions.reviewSummary(review.items ?? {}, review.threshold);
  return {
    recording_id: recordingId,
    generating: !!review.generating,
    generated_at: review.generated_at ?? null,
    model: review.model ?? null,
    source: review.source ?? 'local',
    sync_error: review.sync_error ?? null,
    summary,
    session: review.items?.session ?? null,
    items: review.items ?? {},
  };
}

export function createHandler({ token, actions, jobs, log = console.error }) {
  const table = routes(actions, jobs);
  return async (req, res) => {
    const send = (status, body) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    try {
      if (!authorized(req.headers.authorization, token)) return send(401, { error: 'unauthorized' });
      const url = new URL(req.url, 'http://localhost');
      const pathname = decodeURIComponent(url.pathname).replace(/\/+$/, '') || '/';
      let matched = false;
      for (const [method, re, handler] of table) {
        const m = re.exec(pathname);
        if (!m) continue;
        matched = true;
        if (method !== req.method) continue;
        const body = req.method === 'POST' ? await readBody(req) : {};
        const out = await handler({ params: m.slice(1), body, query: url.searchParams });
        return out instanceof Reply ? send(out.status, out.body) : send(200, out ?? null);
      }
      return send(matched ? 405 : 404, { error: matched ? 'method not allowed' : 'not found' });
    } catch (e) {
      const status = e instanceof ApiError ? e.status : statusFor(e?.message);
      if (status >= 500) log('agent API error:', e);
      return send(status, { error: e?.message ?? String(e) });
    }
  };
}

// Starts listening; resolves with the server. Without a token nothing listens —
// the recorder must never accept unauthenticated commands.
export function startApi({ host, port, token, actions, jobs, log = console.error }) {
  if (!token) throw new Error('VISTA_RECORDER_API_TOKEN is not set; the agent API stays off.');
  const server = http.createServer(createHandler({ token, actions, jobs, log }));
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.off('error', reject);
      resolve(server);
    });
  });
}
