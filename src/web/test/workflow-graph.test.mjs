// The task graph panel on the Workflows view: approved structure, per-move policy and
// statistics, provenance, each run's path, code's promotion proposals — and only the owner
// turning them into the next draft version.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const html = fs.readFileSync(new URL("../public/account/index.html", import.meta.url), "utf8");
const source = fs.readFileSync(new URL("../public/account/app.js", import.meta.url), "utf8");
const graph = JSON.parse(fs.readFileSync(new URL("../../../tests/fixtures/plan_invoice.json", import.meta.url), "utf8"));
const company = "00000000-0000-0000-0000-000000000001";
const workflowId = "00000000-0000-0000-0000-0000000000aa";
const versionId = "00000000-0000-0000-0000-0000000000bb";
const runId = "00000000-0000-0000-0000-0000000000cc";
const malicious = "<img src=x onerror=alert(1)>";

const click = graph.edges.find((e) => e.action_class === "click");
const read = graph.edges.find((e) => e.action_class === "read");
const definition = {
  goal: "Enter the invoice total into the accounting system",
  required_inputs: ["input_1", "Sandbox form URL"],
  allowed_tools: ["read_source_records", "map_fields", "write_destination_records", "compare_with_manual_entry"],
  success_criteria: ["The bill total matches the invoice"],
  environment: "sandbox",
  limits: { max_steps: 10, max_runtime_seconds: 300, max_cost_usd: "1.00" },
  graph,
};
const version = (status, number = 1) => ({
  id: versionId,
  workflow_id: workflowId,
  number,
  definition,
  definition_hash: "a".repeat(64),
  status,
  decision: status === "approved" ? { id: "x", version_id: versionId, definition_hash: "a".repeat(64), decision: "approved", reason: "ok", decided_by: "u", decided_at: "2026-09-22T09:10:00Z" } : null,
  created_by: "00000000-0000-0000-0000-000000000099",
  created_at: "2026-09-22T09:05:00Z",
});
const workflow = (status) => ({ id: workflowId, company_id: company, name: "Invoice total", latest_version: version(status), created_by: "u", created_at: "2026-09-22T09:05:00Z" });
const review = (over = {}) => ({
  version_id: versionId,
  version_number: 1,
  status: "approved",
  graph,
  runs: [
    {
      run_id: runId,
      status: "succeeded",
      mode: "sandbox",
      verified: true,
      finished_at: "2026-09-23T10:00:00Z",
      steps: [
        { step_id: "s1", edge: read.id, seq: 1, effect_seen: true, executed: true, decision: null },
        { step_id: "s2", edge: click.id, seq: 2, effect_seen: false, executed: true, decision: "approve" },
      ],
    },
  ],
  draft: graph,
  changes: [{ edge_id: read.id, kind: "stats", before: read.stats, after: { ...read.stats, executed: read.stats.executed + 5, verified_ok: read.stats.verified_ok + 5 } }],
  proposals: [{ edge_id: click.id, from_policy: "confirm", to_policy: "auto", reason: `5 executed, 5 verified, 0 denied, 0 effect missing ${malicious}` }],
  previous_version_number: null,
  against_previous: [],
  can_draft: true,
  ...over,
});

async function settle(predicate) {
  for (let i = 0; i < 200; i++) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail("UI did not reach expected state");
}
function mount({ role = "owner", status = "approved", graphReview = review() } = {}) {
  const state = { requests: [], workflows: [workflow(status)] };
  const dom = new JSDOM(html, { url: "https://vista.test/account/", runScripts: "outside-only" });
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  dom.window.fetch = async (path, options = {}) => {
    const method = options.method ?? "GET";
    state.requests.push({ method, path, body: options.body ? JSON.parse(options.body) : null });
    if (path === "/api/auth/me") return Response.json({ email: "owner@example.com" });
    if (path === "/api/deals") return Response.json([{ id: company, name: "Recorder Company" }]);
    if (path === `/api/deals/${company}/imports`) return Response.json({ role, imports: [] });
    if (path === `/api/deals/${company}/import-datasets`) return Response.json({});
    if (path === "/api/runs?limit=100") return Response.json([]);
    if (path === "/api/findings?limit=200") return Response.json([]);
    if (path.startsWith("/api/usage")) return Response.json({ total_cost_usd: 0, total_input_tokens: 0, total_output_tokens: 0, runs: 0, groups: [] });
    if (path === "/api/synthetic/companies") return Response.json([]);
    if (path === "/api/recorder/reports?limit=100") return Response.json([]);
    if (path === "/api/workflows?limit=100") return Response.json(state.workflows);
    if (path === `/api/workflows/${workflowId}/versions/${versionId}/eligibility`) return Response.json({ version_id: versionId, eligible: false, reasons: ["harness_not_connected"], execution_available: false });
    if (path === `/api/workflows/${workflowId}/runs?limit=20`) return Response.json([]);
    if (path === `/api/workflows/${workflowId}/versions/${versionId}/graph`) return Response.json(graphReview);
    if (path === `/api/workflows/${workflowId}/versions/${versionId}/graph/draft` && method === "POST") {
      const created = { ...version("draft", 2), id: "00000000-0000-0000-0000-0000000000dd" };
      state.workflows = [{ ...state.workflows[0], latest_version: created }];
      return Response.json(created, { status: 201 });
    }
    throw new Error("Unexpected URL " + method + " " + path);
  };
  dom.window.eval(`(async()=>{${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id) };
}
async function openGraph(ui) {
  await settle(() => ui.$("workflow-count").textContent === "1");
  ui.dom.window.document.querySelector('[data-view="workflows"]').click();
  await settle(() => /Workflows/.test(ui.$("view-name").textContent));
  const details = ui.$("content").querySelector("[data-graph]");
  assert.ok(details, "an approved version with a graph shows the task graph panel");
  assert.match(details.querySelector("summary").textContent, /5 states · 7 moves/);
  assert.equal(ui.state.requests.some((r) => r.path.endsWith("/graph")), false, "the graph is fetched only when opened");
  details.open = true;
  details.dispatchEvent(new ui.dom.window.Event("toggle"));
  await settle(() => /Moves/.test(details.textContent));
  return details;
}

test("the owner sees structure, policies, statistics, provenance, the run's path and code's proposals — and drafts the next version", async () => {
  const ui = mount();
  try {
    const details = await openGraph(ui);
    const text = details.textContent;
    assert.equal(details.querySelectorAll("img").length, 0);
    assert.match(text, /compiled from 1 recorded pass/);
    assert.match(text, /accounting · /);
    assert.match(text, /goal/);
    assert.match(text, /always_ask/);
    assert.match(text, /recording rec-invoice-1/);
    assert.match(text, /Runs of this version \(1\)/);
    assert.match(text, /effect not seen/);
    assert.match(text, /approved by a person/);
    assert.match(text, /Since approval/);
    assert.match(text, /→ 6 run · 6 verified|→ \d+ seen/);
    assert.match(text, /Code proposes these promotions/);
    const box = details.querySelector(`[data-promote="${click.id}"]`);
    assert.ok(box && !box.disabled);
    box.checked = true;
    details.querySelector("[data-draft-runs]").click();
    await settle(() => /v2 · draft/.test(ui.$("content").textContent));
    const post = ui.state.requests.find((r) => r.method === "POST");
    assert.equal(post.path, `/api/workflows/${workflowId}/versions/${versionId}/graph/draft`);
    assert.deepEqual(post.body, { expected_version: 1, promote: [click.id] });
    assert.ok(ui.$("content").querySelector('[data-decide="approved"]'), "the new draft awaits the ordinary approval");
  } finally {
    ui.dom.window.close();
  }
});

test("members read the graph but cannot draft, and a version without runs offers nothing to draft", async () => {
  const member = mount({ role: "member" });
  try {
    const details = await openGraph(member);
    assert.equal(details.querySelector("[data-draft-runs]"), null);
    assert.equal(details.querySelector("[data-promote]").disabled, true);
    assert.match(details.textContent, /Only the workspace owner can draft/);
  } finally {
    member.dom.window.close();
  }
  const quiet = mount({ graphReview: review({ runs: [], changes: [], proposals: [], draft: null, can_draft: false }) });
  try {
    const details = await openGraph(quiet);
    assert.match(details.textContent, /No finished run of this version yet/);
    assert.doesNotMatch(details.textContent, /Since approval/);
    assert.equal(details.querySelector("[data-draft-runs]"), null);
  } finally {
    quiet.dom.window.close();
  }
});

test("a draft version shows what approving it changes against the previous approved version", async () => {
  const ui = mount({
    status: "draft",
    graphReview: review({
      status: "draft",
      version_number: 2,
      runs: [],
      changes: [],
      proposals: [],
      draft: null,
      can_draft: false,
      previous_version_number: 1,
      against_previous: [{ edge_id: click.id, kind: "policy", before: "confirm", after: "auto", reason: "" }],
    }),
  });
  try {
    const details = await openGraph(ui);
    assert.match(details.textContent, /What approving v2 changes against v1/);
    assert.match(details.textContent, /click: confirm → auto/);
  } finally {
    ui.dom.window.close();
  }
});
