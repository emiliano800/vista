// Uploads to the company workspace: the report (summary + evidence + the
// employee's names/notes as metadata) through the API, then the media files
// (screenshots, screen video, raw events) straight to object storage via signed
// URLs. Desktop paths never leave the machine.
import fs from "node:fs";
import path from "node:path";

const LIMIT = 8 * 1024 * 1024;
export function workspaceURL(input) {
  const url = new URL(input);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (
    (url.protocol !== "https:" && !(local && url.protocol === "http:")) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  ) {
    throw new Error(
      "Use an HTTPS website address with no path (HTTP is allowed on localhost).",
    );
  }
  return url.origin;
}
export function companyID(value) {
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value,
    )
  )
    throw new Error("Enter the company ID shown on the website.");
  return value;
}
function readBounded(file) {
  if (fs.statSync(file).size > LIMIT)
    throw new Error("Report exceeds the 8 MiB upload limit.");
  return fs.readFileSync(file, "utf8");
}
export function reportBundle(root, id) {
  if (typeof id !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(id))
    throw new Error("Invalid recording ID.");
  const dir = path.join(root, id);
  // Reject symlinked recording directories so the bridge cannot read outside recordings.
  if (!fs.realpathSync(dir).startsWith(fs.realpathSync(root) + path.sep))
    throw new Error("Invalid recording directory.");
  const m = JSON.parse(readBounded(path.join(dir, "manifest.json")));
  if (m.recording_id !== id || m.processing !== "done" || !m.ended_at)
    throw new Error("Wait until this session has finished analysis.");
  const manifest = Object.fromEntries(
    [
      "recording_id",
      "started_at",
      "ended_at",
      "active_seconds",
      "processing",
      "counts",
      "apps",
    ].map((k) => [k, m[k]]),
  );
  const summary = JSON.parse(
    readBounded(path.join(dir, "processed", "summary.json")),
  );
  const event_log_csv = readBounded(
    path.join(dir, "processed", "event_log.csv"),
  );
  const sections = readSectionEdits(dir);
  const body = JSON.stringify({
    version: 1,
    manifest,
    summary,
    event_log_csv,
    name: String(m.name ?? "").slice(0, 4096),
    summary_text: String(m.summary_text ?? "").slice(0, 4096),
    sections,
  });
  if (Buffer.byteLength(body) > LIMIT)
    throw new Error("Report exceeds the 8 MiB upload limit.");
  return body;
}
// sections.json: what the employee typed over each video section ({id: {name, note, edited_at}}).
export function readSectionEdits(dir) {
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(path.join(dir, "sections.json"), "utf8"));
  } catch {
    return {};
  }
  const out = {};
  for (const [id, e] of Object.entries(raw ?? {})) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(id) || !e || typeof e !== "object") continue;
    out[id] = {
      name: String(e.name ?? "").slice(0, 4096),
      note: String(e.note ?? "").slice(0, 4096),
      ...(e.edited_at ? { edited_at: e.edited_at } : {}),
    };
  }
  return out;
}

export async function cloudRequest(
  config,
  endpoint,
  options = {},
  fetchImpl = fetch,
) {
  const base = workspaceURL(config.url);
  const response = await fetchImpl(`${base}/api${endpoint}`, {
    ...options,
    redirect: "error",
    signal: AbortSignal.timeout(30000),
    headers: {
      Authorization: `Bearer ${config.token}`,
      "Content-Type": "application/json",
    },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "The server did not return a report response. Check the website address.",
    );
  }
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : response.status === 422
          ? "The report format was rejected. Update the recorder and analyze the session again."
          : `Upload failed (${response.status}). Please retry.`,
    );
  return data;
}
export async function uploadReport(config, root, id, fetchImpl = fetch) {
  return cloudRequest(
    config,
    `/deals/${companyID(config.companyId)}/recordings`,
    { method: "POST", body: reportBundle(root, id) },
    fetchImpl,
  );
}

// ---- employee review through the workspace -----------------------------------
// The recorder describes each section (already redacted on this device, never
// keystrokes or screenshots); the backend asks the model and stores what the
// employee decides, so analysts see the same review in the web workspace.
const RECORDING_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function recordingPath(cloudRecordingId) {
  if (!RECORDING_ID.test(String(cloudRecordingId)))
    throw new Error("This session has not been uploaded yet.");
  return `/recordings/${cloudRecordingId}`;
}
export function reviewItems(sections, describe) {
  return sections.map((s) => ({
    id: s.id,
    description: describe(s),
    section: s.whole
      ? { whole: true, seconds: s.seconds ?? 0 }
      : {
          name: s.name ?? "",
          app: s.app ?? "",
          title: s.title ?? "",
          start: s.start,
          end: s.end,
          seconds: s.seconds ?? 0,
          counts: s.counts ?? {},
        },
  }));
}
export function submitSections(config, cloudRecordingId, items, force = false, fetchImpl = fetch) {
  return cloudRequest(
    config,
    `${recordingPath(cloudRecordingId)}/review/sections`,
    { method: "PUT", body: JSON.stringify({ items, force }) },
    fetchImpl,
  );
}
export function fetchReview(config, cloudRecordingId, fetchImpl = fetch) {
  return cloudRequest(config, `${recordingPath(cloudRecordingId)}/review`, {}, fetchImpl);
}
export function sendDecision(config, cloudRecordingId, itemId, action, body = {}, fetchImpl = fetch) {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(String(itemId))) throw new Error("Invalid review item.");
  return cloudRequest(
    config,
    `${recordingPath(cloudRecordingId)}/review/${itemId}`,
    {
      method: "POST",
      body: JSON.stringify({
        action,
        label: body.label ?? "",
        note: body.note ?? "",
        answers: (body.answers ?? []).map((a) => ({ q: String(a.q ?? ""), a: String(a.a ?? "") })),
      }),
    },
    fetchImpl,
  );
}
// Server review → the local review.json shape the dashboard already renders.
export function mergeReview(local, remote) {
  const items = { ...local.items };
  for (const [id, r] of Object.entries(remote.items ?? {})) {
    if (r.status === "pending") continue;
    items[id] = { ...r, id, at: r.at ?? null };
  }
  return {
    ...local,
    source: "cloud",
    threshold: remote.threshold ?? local.threshold,
    model: remote.model ?? local.model,
    generating: !!remote.generating,
    generated_at: remote.generated_at ?? local.generated_at,
    items,
  };
}

// ---- media: everything else in the recording folder ---------------------------
// The workspace signs one PUT URL per file; the recorder streams each file there
// and afterwards the local copy can go. Names are relative to the recording dir.
const MEDIA_TYPES = {
  ".webm": "video/webm",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".jsonl": "application/x-ndjson",
  ".json": "application/json",
  ".csv": "text/csv",
  ".xes": "application/xml",
};
export function mediaFiles(root, id) {
  const dir = path.join(root, id);
  const out = [];
  const walk = (rel) => {
    for (const ent of fs.readdirSync(path.join(dir, rel), { withFileTypes: true })) {
      const r = rel ? `${rel}/${ent.name}` : ent.name;
      if (ent.isSymbolicLink()) continue;
      if (ent.isDirectory()) walk(r);
      else if (ent.isFile()) {
        const type = MEDIA_TYPES[path.extname(ent.name).toLowerCase()];
        if (!type || ent.name.endsWith(".tmp")) continue;
        out.push({ name: r, content_type: type, size_bytes: fs.statSync(path.join(dir, r)).size });
      }
    }
  };
  walk("");
  return out.sort((a, b) => a.name.localeCompare(b.name));
}
export async function uploadMedia(config, root, id, cloudRecordingId, { fetchImpl = fetch, onProgress = () => {}, batch = 200 } = {}) {
  const dir = path.join(root, id);
  const files = mediaFiles(root, id);
  let done = 0;
  for (let i = 0; i < files.length; i += batch) {
    const chunk = files.slice(i, i + batch);
    const { uploads } = await cloudRequest(
      config,
      `${recordingPath(cloudRecordingId)}/media`,
      { method: "POST", body: JSON.stringify({ files: chunk }) },
      fetchImpl,
    );
    const byName = new Map(uploads.map((u) => [u.name, u.url]));
    for (const f of chunk) {
      const url = byName.get(f.name);
      if (!url) throw new Error(`The workspace did not accept ${f.name}.`);
      const res = await fetchImpl(url, {
        method: "PUT",
        headers: { "Content-Type": f.content_type },
        body: fs.readFileSync(path.join(dir, f.name)),
        signal: AbortSignal.timeout(10 * 60 * 1000),
      });
      if (!res.ok) throw new Error(`Uploading ${f.name} failed (${res.status}). Please retry.`);
      done += 1;
      onProgress({ done, total: files.length, name: f.name });
    }
  }
  return files.map((f) => f.name);
}
