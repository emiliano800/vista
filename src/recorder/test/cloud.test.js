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
  workspaceRecordingURL,
  workspaceRunState,
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
    fs.writeFileSync(
      path.join(dir, "files.json"),
      JSON.stringify({
        version: 1,
        files: [
          { id: "a1b2c3d4e5f6", path: "/Users/PRIVATE/Documents/Q3.xlsx", name: "Q3.xlsx", ext: ".xlsx", folder: "Documents", first_opened: "2026-09-19T10:00:00Z", last_closed: "2026-09-19T10:05:00Z", seconds: 300, intervals: [{ start: "2026-09-19T10:00:00Z", end: "2026-09-19T10:05:00Z", app: "Microsoft Excel" }], used_at: [], sources: ["ax"], snapshot: "files/a1b2c3d4e5f6/Q3.xlsx", include: true },
          { id: "ffffffffffff", path: "/Users/PRIVATE/Downloads/secret.pdf", name: "secret.pdf", ext: ".pdf", folder: "Downloads", first_opened: "2026-09-19T10:00:00Z", last_closed: "2026-09-19T10:00:00Z", intervals: [], used_at: ["2026-09-19T10:00:00Z"], sources: ["download"], include: false },
        ],
      }),
    );
    fs.writeFileSync(
      path.join(dir, "workflows.json"),
      JSON.stringify({
        version: 1,
        generated_at: "2026-09-19T10:02:00Z",
        fallback: false,
        environment: { apps: [{ app: "Microsoft Excel", short: "Excel", role: "spreadsheet", seconds: 300 }], roles: ["spreadsheet"], documents: [{ name: "Q3.xlsx", ext: ".xlsx", app: "Microsoft Excel", edited: true, parsed: false, flags: 0 }], sites: [] },
        workflows: [{ id: "rekey-a-b", title: "Re-key", kind: "data_transfer", apps: ["A", "B"], steps: ["Copy", "Paste"], evidence: { pastes: 3, sources: ["Q3.xlsx"] }, automation: 0.85, sources: ["events"], why: "3 values were copied.", generated: false, secret: "PRIVATE" }],
      }),
    );
    const body = reportBundle(root, "session-1");
    assert.ok(!body.includes("PRIVATE"));
    const parsed = JSON.parse(body);
    assert.equal(parsed.workflows.workflows.length, 1, "workflows.json travels with the report");
    assert.equal(parsed.workflows.workflows[0].id, "rekey-a-b");
    assert.equal(parsed.workflows.workflows[0].secret, undefined, "only known workflow fields are sent");
    assert.equal(parsed.workflows.environment.apps[0].role, "spreadsheet");
    assert.equal(parsed.files.length, 1, "unticked documents stay off the report");
    assert.equal(parsed.files[0].name, "Q3.xlsx");
    assert.equal(parsed.files[0].path, undefined);
    assert.deepEqual(Object.keys(parsed).sort(), [
      "event_log_csv",
      "files",
      "manifest",
      "name",
      "sections",
      "summary",
      "summary_text",
      "version",
      "workflows",
    ]);
    fs.rmSync(path.join(dir, "workflows.json"));
    assert.equal(JSON.parse(reportBundle(root, "session-1")).workflows, null, "no workflows.json means none were built");
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
    run: { id: "r1", status: "running", finished_at: null },
    items: { S1: { status: "approved", label: "Enter bills", final_label: "Enter bills" }, S2: { status: "pending" } },
  });
  assert.equal(merged.source, "cloud");
  assert.deepEqual(merged.run, { id: "r1", status: "running", finished_at: null });
  assert.equal(mergeReview(local, { items: {} }).run, null);
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
    fs.writeFileSync(path.join(dir, "files.json"), JSON.stringify({ version: 1, files: [{ path: "/Users/PRIVATE" }] }));
    fs.mkdirSync(path.join(dir, "files", "a1b2c3d4e5f6"), { recursive: true });
    fs.writeFileSync(path.join(dir, "files", "a1b2c3d4e5f6", "Q3.xlsx"), Buffer.alloc(7, 3));
    fs.writeFileSync(path.join(dir, "files", "a1b2c3d4e5f6", "Q3.exe"), Buffer.alloc(7, 3));
    const files = mediaFiles(root, "session-2");
    assert.deepEqual(
      files.map((f) => [f.name, f.content_type, f.size_bytes]),
      [
        ["events.jsonl", "application/x-ndjson", 3],
        ["files/a1b2c3d4e5f6/Q3.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 7],
        ["manifest.json", "application/json", 2],
        ["processed/summary.json", "application/json", 2],
        ["screen.webm", "video/webm", 10],
        ["shots/000001.jpg", "image/jpeg", 3],
      ],
    );
    const config = { url: "https://bumpsolutions.org", companyId: "00000000-0000-0000-0000-000000000001", token: "secret" };
    const puts = [];
    let failOn = null, completed = 0;
    const fetchImpl = async (url, options) => {
      if (url.endsWith("/media/complete")) {
        assert.equal(options.method, "POST");
        completed += 1;
        return Response.json({ queued: 1 });
      }
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
    assert.deepEqual(progress, [1, 2, 3, 4, 5, 6]);
    assert.deepEqual(puts[4], { url: "https://s3.test/screen.webm", type: "video/webm", bytes: 10 });
    assert.equal(completed, 1, "workspace told once that the package is complete");
    puts.length = 0;
    failOn = "screen.webm";
    await assert.rejects(uploadMedia(config, root, "session-2", "11111111-1111-1111-1111-111111111111", { fetchImpl }), /screen\.webm failed \(500\)/);
    assert.equal(puts.length, 5);
    assert.equal(completed, 1, "a failed package is not marked complete");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("workspaceRunState maps the review's run to a banner/pill state", () => {
  assert.equal(workspaceRunState({ source: "local", run: null }), null, "local explanations have no workspace state");
  assert.deepEqual(workspaceRunState({ source: "cloud", run: null }), { key: "none", label: "Explained on this computer", sub: "No workspace agent run." });
  assert.equal(workspaceRunState({ source: "cloud", run: { status: "queued" } }).key, "queued");
  assert.equal(workspaceRunState({ source: "cloud", run: { status: "running" }, summary: { open: 3 } }).label, "Agent explaining");
  const ok = workspaceRunState({ source: "cloud", run: { status: "succeeded", finished_at: "2026-09-20T03:00:00Z" }, summary: { open: 2 } });
  assert.equal(ok.key, "succeeded");
  assert.match(ok.sub, /Recording Reviewer finished .* · 2 sections still open\.$/);
  assert.match(workspaceRunState({ source: "cloud", run: { status: "succeeded" }, summary: { open: 0 } }).sub, /nothing left to review/);
  const failed = workspaceRunState({ source: "cloud", run: { status: "failed", error: "model timed out" } });
  assert.deepEqual([failed.key, failed.label, failed.sub], ["failed", "Agent run failed", "model timed out."]);
  assert.equal(workspaceRunState({ run: { status: "cancelled" } }).label, "cancelled", "unknown statuses pass through");
});

test("workspaceRecordingURL points at the workspace Runs view for the recording", () => {
  assert.equal(workspaceRecordingURL({ url: "https://vista.example" }, "11111111-1111-1111-1111-111111111111"), "https://vista.example/account/?view=runs&recording=11111111-1111-1111-1111-111111111111");
  assert.equal(workspaceRecordingURL({ url: "https://vista.example" }, undefined), null, "not submitted yet");
  assert.equal(workspaceRecordingURL(null, "x"), null, "not connected");
  assert.throws(() => workspaceRecordingURL({ url: "http://vista.example" }, "x"), /https/i);
});
