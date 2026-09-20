import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  FileTracker,
  axDocuments,
  isDocument,
  lsofDocuments,
  publicFile,
  readFiles,
  safeName,
  snapshotFiles,
  spotlightSweep,
  writeFiles,
} from "../src/files.js";

const T0 = Date.parse("2026-09-19T10:00:00Z");
const s = (n) => T0 + n * 1000;

test("only allowlisted document types outside system folders count", () => {
  assert.ok(isDocument("/Users/a/Documents/Q3 budget.xlsx"));
  assert.ok(isDocument("/Users/a/Downloads/deck.PPTX"));
  assert.ok(!isDocument("/Users/a/Library/Containers/x.xlsx"));
  assert.ok(!isDocument("/Users/a/Vista/recordings/abc/files/x/y.csv"));
  assert.ok(!isDocument("/Users/a/Documents/~$Q3.xlsx"));
  assert.ok(!isDocument("/Applications/Excel.app"));
  assert.ok(!isDocument(null));
  assert.equal(safeName("Q3 budget (final) ✓.xlsx"), "Q3_budget_final_.xlsx");
  assert.equal(safeName(".hidden"), "hidden");
  const long = safeName(`${"a".repeat(150)}.xlsx`);
  assert.ok(long.endsWith(".xlsx") && long.length === 120);
  assert.equal(safeName("###"), "file");
});

test("tracker: open/close intervals follow what the probe sees, pauses are cut, flicker is dropped", () => {
  const tr = new FileTracker({ minSeconds: 3 });
  tr.observe(["/Users/a/Documents/Q3.xlsx"], s(0), { app: "Microsoft Excel" });
  tr.observe(["/Users/a/Documents/Q3.xlsx"], s(5), { app: "Microsoft Excel" });
  tr.observe(["/Users/a/Documents/invoice.pdf"], s(60), { app: "Preview" }); // Q3 closes here
  tr.observe(["/Users/a/Documents/invoice.pdf", "/Users/a/Desktop/blip.csv"], s(70), { app: "Preview" });
  tr.observe(["/Users/a/Documents/invoice.pdf"], s(71), { app: "Preview" }); // blip open 1 s: noise
  tr.observe(["/Users/a/Documents/Q3.xlsx"], s(100), { app: "Microsoft Excel" }); // reopened
  tr.closeAll(s(130)); // pause
  tr.observe(["/Users/a/Documents/Q3.xlsx"], s(200), { app: "Microsoft Excel" });
  tr.touch("/Users/a/Downloads/statement.csv", s(150), { source: "download" });
  tr.touch("/Users/a/Downloads/old.csv", s(-500)); // before the session
  tr.closeAll(s(260));
  const files = tr.finish({ t0: T0, t1: s(260), pauses: [{ start: new Date(s(130)).toISOString(), end: new Date(s(200)).toISOString() }] });
  assert.deepEqual(
    files.map((f) => f.name),
    ["Q3.xlsx", "invoice.pdf", "statement.csv"],
  );
  const q3 = files[0];
  assert.equal(q3.intervals.length, 3);
  assert.deepEqual(
    q3.intervals.map((iv) => [iv.start.slice(11, 19), iv.end.slice(11, 19), iv.app]),
    [
      ["10:00:00", "10:01:00", "Microsoft Excel"],
      ["10:01:40", "10:02:10", "Microsoft Excel"],
      ["10:03:20", "10:04:20", "Microsoft Excel"],
    ],
  );
  assert.equal(q3.first_opened, "2026-09-19T10:00:00.000Z");
  assert.equal(q3.last_closed, "2026-09-19T10:04:20.000Z");
  assert.equal(q3.seconds, 60 + 30 + 60);
  assert.equal(q3.folder, "Documents");
  assert.match(q3.id, /^[a-f0-9]{12}$/);
  assert.deepEqual(q3.sources, ["ax"]);
  const dl = files[2];
  assert.deepEqual(dl.intervals, []);
  assert.deepEqual(dl.used_at, ["2026-09-19T10:02:30.000Z"]);
  assert.deepEqual(dl.sources, ["download"]);
  assert.equal(dl.first_opened, dl.last_closed);
  assert.ok(files.every((f) => f.include === true));
});

test("snapshot copies the last version once, flags missing/oversized files, and files.json round-trips without paths in public form", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "vista-files-"));
  try {
    const src = path.join(root, "Q3 budget.xlsx");
    fs.writeFileSync(src, "v1");
    fs.writeFileSync(src, "v2-final");
    const big = path.join(root, "big.csv");
    fs.writeFileSync(big, "x".repeat(200));
    const tr = new FileTracker();
    tr.observe([src, big, path.join(root, "gone.docx")], s(0), { app: "Excel" });
    tr.closeAll(s(30));
    const files = tr.finish({ t0: T0, t1: s(30) });
    files.find((f) => f.name === "gone.docx").include = true;
    const dir = path.join(root, "rec");
    fs.mkdirSync(dir);
    snapshotFiles(dir, files, { startedAt: new Date(s(-10)).toISOString(), maxBytes: 100 });
    const q3 = files.find((f) => f.name === "Q3 budget.xlsx");
    assert.equal(q3.snapshot, `files/${q3.id}/Q3_budget.xlsx`);
    assert.equal(fs.readFileSync(path.join(dir, q3.snapshot), "utf8"), "v2-final");
    assert.equal(q3.size_bytes, 8);
    assert.equal(q3.edited, true);
    assert.match(q3.sha256, /^[a-f0-9]{64}$/);
    assert.equal(q3.content_type, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    const b = files.find((f) => f.name === "big.csv");
    assert.equal(b.snapshot, null);
    assert.match(b.snapshot_error, /larger than/);
    const g = files.find((f) => f.name === "gone.docx");
    assert.equal(g.snapshot, null);
    assert.match(g.snapshot_error, /ENOENT/);
    writeFiles(dir, files);
    const back = readFiles(dir);
    assert.equal(back.length, 3);
    assert.equal(back[0].path.startsWith(root), true);
    assert.equal(publicFile(back[0]).path, undefined);
    assert.deepEqual(readFiles(path.join(root, "nope")), []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("macOS probes parse osascript/lsof/mdfind output and are no-ops elsewhere", async () => {
  const mac = process.platform === "darwin";
  const exec = async (cmd, args) => {
    if (cmd === "osascript") return { stdout: "file:///Users/a/Documents/Q3%20budget.xlsx\nfile:///Users/a/Library/x.pdf\n\n" };
    if (cmd === "lsof") return { stdout: "p123\nn/Users/a/Desktop/notes.docx\nn/dev/null\nn/Users/a/img.png\n" };
    if (cmd === "mdfind") return { stdout: args.some((a) => a.includes("kMDItemDateAdded")) ? "/Users/a/Downloads/statement.csv\n" : "/Users/a/Documents/Q3 budget.xlsx\n" };
    throw new Error(cmd);
  };
  const ax = await axDocuments(exec);
  const ls = await lsofDocuments(123, exec);
  const sweep = await spotlightSweep({ start: "2026-09-19T10:00:00Z", end: "2026-09-19T10:05:00Z" }, exec, "/Users/a");
  if (!mac) {
    assert.deepEqual([ax, ls, sweep], [[], [], []]);
    return;
  }
  assert.deepEqual(ax, ["/Users/a/Documents/Q3 budget.xlsx"]);
  assert.deepEqual(ls, ["/Users/a/Desktop/notes.docx"]);
  assert.ok(sweep.some((h) => h.path === "/Users/a/Downloads/statement.csv" && h.source === "download"));
  assert.ok(sweep.some((h) => h.path === "/Users/a/Documents/Q3 budget.xlsx" && h.source === "spotlight"));
  assert.deepEqual(await axDocuments(async () => { throw new Error("no permission"); }), []);
});
