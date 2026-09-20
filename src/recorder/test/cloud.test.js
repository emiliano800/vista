import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  workspaceURL,
  reportBundle,
  uploadReport,
  cloudRequest,
  reviewItems,
  submitSections,
  fetchReview,
  sendDecision,
  mergeReview,
} from "../src/cloud.js";

test("workspace URLs require HTTPS or local HTTP and never accept credentials/paths", () => {
  assert.equal(
    workspaceURL("https://bumpsolutions.org/"),
    "https://bumpsolutions.org",
  );
  assert.equal(workspaceURL("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
  for (const url of [
    "http://public.example",
    "https://user:pass@public.example",
    "https://public.example/api",
    "file:///tmp/key",
    "https://example.com?x=1",
  ])
    assert.throws(() => workspaceURL(url));
});
test("only completed portable reports are uploaded; retries use the same source ID", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "vista-upload-"));
  try {
    const dir = path.join(root, "session-1");
    fs.mkdirSync(path.join(dir, "processed"), { recursive: true });
    const manifest = {
      recording_id: "session-1",
      started_at: "2026-09-19T09:00:00Z",
      ended_at: "2026-09-19T10:00:00Z",
      active_seconds: 3600,
      processing: "done",
      user: "PRIVATE",
      files: { video: "/private/screen.webm" },
      settings: { secret: "PRIVATE" },
    };
    fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest));
    fs.writeFileSync(path.join(dir, "processed", "summary.json"), "{}");
    fs.writeFileSync(path.join(dir, "processed", "event_log.csv"), "evidence");
    fs.writeFileSync(path.join(dir, "events.jsonl"), "PRIVATE_RAW");
    const body = reportBundle(root, "session-1");
    assert.ok(!body.includes("PRIVATE"));
    assert.deepEqual(Object.keys(JSON.parse(body)).sort(), [
      "event_log_csv",
      "manifest",
      "summary",
      "version",
    ]);
    assert.throws(() => reportBundle(root, "../other"));
    const config = {
      url: "https://bumpsolutions.org",
      companyId: "00000000-0000-0000-0000-000000000001",
      token: "secret",
    };
    const calls = [];
    const fetchImpl = async (url, options) => {
      calls.push({ url, options });
      return Response.json({ id: "same" });
    };
    await uploadReport(config, root, "session-1", fetchImpl);
    await uploadReport(config, root, "session-1", fetchImpl);
    assert.equal(calls[0].options.body, calls[1].options.body);
    assert.equal(calls[0].options.redirect, "error");
    assert.equal(calls[0].options.headers.Authorization, "Bearer secret");
    assert.ok(calls[0].url.endsWith("/recordings"));
    manifest.processing = "running";
    fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest));
    assert.throws(() => reportBundle(root, "session-1"), /finished analysis/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
test("upload reports actionable server failures without following redirects", async () => {
  await assert.rejects(
    cloudRequest(
      { url: "https://example.com", token: "key" },
      "/auth/me",
      {},
      async () =>
        Response.json({ detail: "Invalid access key" }, { status: 401 }),
    ),
    /Invalid access key/,
  );
});
test("review calls: sections described on-device, decisions posted, pending items stay local", async () => {
  const config = { url: "https://example.com", token: "key" };
  const rid = "11111111-2222-4333-8444-555555555555";
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return Response.json({ ok: true });
  };
  const sections = [
    { id: "S1", name: "QuickBooks", app: "QuickBooks", title: "Bills", start: "t0", end: "t1", seconds: 300, counts: { clicks: 4 } },
    { id: "session", whole: true, seconds: 900 },
  ];
  const items = reviewItems(sections, (s) => `desc ${s.id}`);
  assert.deepEqual(items[1], { id: "session", description: "desc session", section: { whole: true, seconds: 900 } });
  assert.equal(items[0].section.counts.clicks, 4);
  await submitSections(config, rid, items, false, fetchImpl);
  await fetchReview(config, rid, fetchImpl);
  await sendDecision(config, rid, "S1", "fix", { label: "Pay bills", answers: [{ q: "Why?", a: "Month end" }] }, fetchImpl);
  assert.equal(calls[0].options.method, "PUT");
  assert.ok(calls[0].url.endsWith(`/recordings/${rid}/review/sections`));
  assert.ok(calls[1].url.endsWith(`/recordings/${rid}/review`));
  assert.deepEqual(JSON.parse(calls[2].options.body), { action: "fix", label: "Pay bills", note: "", answers: [{ q: "Why?", a: "Month end" }] });
  assert.throws(() => submitSections(config, "not-uploaded", items, false, fetchImpl), /not been uploaded/);
  assert.throws(() => sendDecision(config, rid, "../x", "approve", {}, fetchImpl), /Invalid review item/);

  const local = { threshold: 0.88, items: { S1: { id: "S1", status: "proposed", label: "old" } } };
  const merged = mergeReview(local, {
    threshold: 0.88,
    model: "gpt",
    generating: true,
    items: { S1: { status: "approved", label: "Enter bills", final_label: "Enter bills" }, S2: { status: "pending" } },
  });
  assert.equal(merged.source, "cloud");
  assert.equal(merged.items.S1.status, "approved");
  assert.equal(merged.items.S2, undefined);
  assert.equal(merged.generating, true);
});
