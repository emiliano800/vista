import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const html = fs.readFileSync(new URL("../public/account/index.html", import.meta.url), "utf8");
const source = fs.readFileSync(new URL("../public/account/app.js", import.meta.url), "utf8");
const company = "00000000-0000-0000-0000-000000000001";
const other = "00000000-0000-0000-0000-000000000009";
const malicious = "<img src=x onerror=alert(1)>";
const summary = (id, workspace) => ({
  id,
  submission_id: "00000000-0000-0000-0000-00000000000b",
  run_id: null,
  status: "published",
  workspace,
  canonical_company_id: null,
  session: { started_at: "2026-09-19T09:00:00Z", ended_at: "2026-09-19T09:10:00Z", active_seconds: 600 },
  coverage: { note: "Application level only.", excluded: ["window_title", "url"] },
  summary: `${malicious} re-keying between a workbook and a portal`,
  apps: [`${malicious}Excel`, "Portal"],
  switches: 9,
  workflows: 1,
  automation_candidates: 1,
  questions_open: 1,
  questions_total: 2,
  updated_at: "2026-09-22T09:00:00Z",
  published_at: "2026-09-22T10:00:00Z",
});
const full = {
  ...summary("00000000-0000-0000-0000-00000000000a", { id: company, kind: "deal" }),
  observed: {
    events: 40,
    switches: 9,
    session: { started_at: "2026-09-19T09:00:00Z", ended_at: "2026-09-19T09:10:00Z" },
    apps: [
      { app: `${malicious}Excel`, share: 0.6, events: 30, copies: 5, pastes: 0 },
      { app: "Portal", share: 0.4, events: 10, copies: 0, pastes: 5 },
    ],
    transfers: [{ from: `${malicious}Excel`, to: "Portal", count: 5, mean_latency_s: 60 }],
  },
  interpretation: {
    source: "live",
    model: "gpt-6-astra",
    summary: "Re-keying between a workbook and a portal.",
    workflows: [{ name: `Portal entry ${malicious}`, apps: ["Excel", "Portal"], evidence: "5 transfers", confidence: 0.7 }],
    automation_candidates: [{ title: "Bulk upload", rationale: "Five identical transfers.", apps: ["Portal"], confidence: 0.4 }],
    documents: [{ filename: `${malicious}invoice.csv`, summary: { kind: "table", rows: 2 } }],
  },
  questions: [
    { id: "q1", question: "What moves between them?", answer: `Vendor statements ${malicious}`, answered_at: "2026-09-22T09:30:00Z" },
    { id: "q2", question: "Which task?", answer: null, answered_at: null },
  ],
};
async function settle(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail("UI did not reach expected state");
}
function mount({ reportsFail = false } = {}) {
  const state = { requests: [] };
  const dom = new JSDOM(html, { url: "https://vista.test/account/", runScripts: "outside-only" });
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  dom.window.fetch = async (path) => {
    state.requests.push(path);
    if (path === "/api/auth/me") return Response.json({ email: "employee@example.com" });
    if (path === "/api/deals") return Response.json([{ id: company, name: "Recorder Company" }]);
    if (path === `/api/deals/${company}/imports`)
      return Response.json({ role: "viewer", company: { id: "c-1", name: "Meridian", slug: "meridian" }, imports: [], openExceptions: [] });
    if (path === `/api/deals/${company}/import-datasets`) return Response.json({});
    if (path.startsWith("/api/runs")) return Response.json([]);
    if (path.startsWith("/api/findings")) return Response.json([]);
    if (path.startsWith("/api/usage")) return Response.json({ total_cost_usd: 0, total_input_tokens: 0, total_output_tokens: 0, runs: 0, groups: [] });
    if (path === "/api/synthetic/companies") return Response.json([]);
    if (path === "/api/recorder/reports?limit=100") {
      if (reportsFail) return Response.json({ detail: "Not Found" }, { status: 404 });
      return Response.json([full, summary("00000000-0000-0000-0000-00000000000c", { id: other, kind: "deal" })]);
    }
    if (path === `/api/recorder/reports/${full.id}`) return Response.json(full);
    throw new Error("Unexpected URL " + path);
  };
  dom.window.eval(`(async()=>{${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id) };
}

test("published recording reports are listed for the company only, escaped, and opened with facts apart from hypotheses", async () => {
  const ui = mount();
  try {
    await settle(() => ui.$("report-count").textContent === "1");
    ui.dom.window.document.querySelector('[data-view="recordings"]').click();
    await settle(() => /Recordings/.test(ui.$("view-name").textContent));
    const content = ui.$("content");
    assert.equal(content.querySelectorAll("tbody tr").length, 1);
    assert.equal(content.querySelectorAll("img").length, 0);
    assert.match(content.textContent, /1 \/ 2/);
    assert.match(content.textContent, /never leave the employee's computer/);
    content.querySelector("[data-report]").click();
    await settle(() => /Observed/.test(ui.$("report-body").textContent));
    assert.equal(ui.$("report-dialog").open, true);
    const body = ui.$("report-body");
    assert.equal(body.querySelectorAll("img").length, 0);
    assert.match(body.textContent, /60%/);
    assert.match(body.textContent, /Copied from/);
    assert.match(body.textContent, /Agent's reading/);
    assert.match(body.textContent, /hypothesis/);
    assert.match(body.textContent, /Bulk upload/);
    assert.match(body.textContent, /Vendor statements/);
    assert.match(body.textContent, /Not answered/);
    assert.match(body.textContent, /Not observed: window_title, url/);
    ui.$("close-report").click();
    assert.equal(ui.$("report-dialog").open, false);
  } finally {
    ui.dom.window.close();
  }
});

test("the workspace still renders when the reports endpoint is unavailable", async () => {
  const ui = mount({ reportsFail: true });
  try {
    await settle(() => ui.state.requests.includes("/api/recorder/reports?limit=100"));
    ui.dom.window.document.querySelector('[data-view="recordings"]').click();
    await settle(() => /No published recordings yet/.test(ui.$("content").textContent));
    assert.equal(ui.$("report-count").textContent, "0");
  } finally {
    ui.dom.window.close();
  }
});
