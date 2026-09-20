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
  readSectionEdits,
  mediaFiles,
  uploadMedia,
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
    fs.writeFileSync(
      path.join(dir, "sections.json"),
      JSON.stringify({ S1: { name: "Enter bills", note: "from PDFs", edited_at: "2026-09-19T10:01:00Z" }, "../x": { name: "bad" } }),
    );
    const body = reportBundle(root, "session-1");
    assert.ok(!body.includes("PRIVATE"));
    const parsed = JSON.parse(body);
    assert.deepEqual(Object.keys(parsed).sort(), [
      "event_log_csv",
      "manifest",
      "name",
      "sections",
      "summary",
      "summary_text",
      "version",
    ]);
    assert.deepEqual(parsed.sections, { S1: { name: "Enter bills", note: "from PDFs", edited_at: "2026-09-19T10:01:00Z" } });
    assert.deepEqual(readSectionEdits(path.join(root, "missing")), {});
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

test("submit uploads every media file through signed URLs, in order, and stops on the first failure", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "vista-media-"));
  try {
    const dir = path.join(root, "session-2");
    fs.mkdirSync(path.join(dir, "shots"), { recursive: true });
    fs.mkdirSync(path.join(dir, "processed"), { recursive: true });
    fs.writeFileSync(path.join(dir, "manifest.json"), "{}");
    fs.writeFileSync(path.join(dir, "events.jsonl"), "{}\n");
    fs.writeFileSync(path.join(dir, "screen.webm"), Buffer.alloc(10, 1));
    fs.writeFileSync(path.join(dir, "screen.webm.tmp"), "partial");
    fs.writeFileSync(path.join(dir, "shots", "000001.jpg"), Buffer.alloc(3, 2));
    fs.writeFileSync(path.join(dir, "processed", "summary.json"), "{}");
    fs.writeFileSync(path.join(dir, "notes.txt"), "not media");
    const files = mediaFiles(root, "session-2");
    assert.deepEqual(
      files.map((f) => [f.name, f.content_type, f.size_bytes]),
      [
        ["events.jsonl", "application/x-ndjson", 3],
        ["manifest.json", "application/json", 2],
        ["processed/summary.json", "application/json", 2],
        ["screen.webm", "video/webm", 10],
        ["shots/000001.jpg", "image/jpeg", 3],
      ],
    );
    const config = { url: "https://bumpsolutions.org", companyId: "00000000-0000-0000-0000-000000000001", token: "secret" };
    const puts = [];
    let failOn = null;
    const fetchImpl = async (url, options) => {
      if (url.endsWith("/media")) {
        const req = JSON.parse(options.body);
        assert.equal(options.headers.Authorization, "Bearer secret");
        return Response.json({ uploads: req.files.map((f) => ({ name: f.name, url: `https://s3.test/${f.name}` })) });
      }
      assert.equal(options.method, "PUT");
      assert.equal(options.headers.Authorization, undefined);
      puts.push({ url, type: options.headers["Content-Type"], bytes: options.body.length });
      return new Response(null, { status: url.endsWith(failOn ?? "-") ? 500 : 200 });
    };
    const progress = [];
    const names = await uploadMedia(config, root, "session-2", "11111111-1111-1111-1111-111111111111", { fetchImpl, onProgress: (p) => progress.push(p.done) });
    assert.deepEqual(names, files.map((f) => f.name));
    assert.deepEqual(progress, [1, 2, 3, 4, 5]);
    assert.deepEqual(puts[3], { url: "https://s3.test/screen.webm", type: "video/webm", bytes: 10 });
    puts.length = 0;
    failOn = "screen.webm";
    await assert.rejects(uploadMedia(config, root, "session-2", "11111111-1111-1111-1111-111111111111", { fetchImpl }), /screen\.webm failed \(500\)/);
    assert.equal(puts.length, 4);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
