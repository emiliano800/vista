// Report-only uploads. No raw events, desktop paths, screenshots or video.
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
  const body = JSON.stringify({ version: 1, manifest, summary, event_log_csv });
  if (Buffer.byteLength(body) > LIMIT)
    throw new Error("Report exceeds the 8 MiB upload limit.");
  return body;
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
