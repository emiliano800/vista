import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const html = fs.readFileSync(new URL("../public/account/index.html", import.meta.url), "utf8");
import { source } from "./account-source.mjs";
const company = "00000000-0000-0000-0000-000000000001";
const workflowId = "00000000-0000-0000-0000-0000000000aa";
const versionId = "00000000-0000-0000-0000-0000000000bb";
const runId = "00000000-0000-0000-0000-0000000000cc";
const agentRunId = "00000000-0000-0000-0000-0000000000dd";
const stepId = "00000000-0000-0000-0000-0000000000ee";
const malicious = "<img src=x onerror=alert(1)>";

const definition = {
  goal: "Key supplier invoices into the sandbox entry form",
  required_inputs: ["supplier_invoices"],
  allowed_tools: ["read_source_records", "write_destination_records"],
  success_criteria: ["Every value written equals the source value"],
  environment: "sandbox",
  limits: { max_steps: 40, max_runtime_seconds: 900, max_cost_usd: "1.00" },
};
const workflow = (status = "approved") => ({
  id: workflowId,
  company_id: company,
  name: "Enter bills",
  latest_version: { id: versionId, workflow_id: workflowId, number: 2, definition, definition_hash: "a".repeat(64), status, decision: status === "approved" ? { decision: "approved", reason: "ok" } : null, created_by: "u", created_at: "2026-09-22T09:05:00Z" },
  created_by: "u",
  created_at: "2026-09-22T09:05:00Z",
});
const run = (status, extra = {}) => ({
  id: runId,
  workflow_id: workflowId,
  workflow_name: "Enter bills",
  version_id: versionId,
  version_number: 2,
  definition_hash: "a".repeat(64),
  agent_run_id: agentRunId,
  mode: "sandbox",
  status,
  steps_used: 3,
  limits: { max_steps: 40, max_runtime_seconds: 900, max_cost_usd: "1.00" },
  cost_usd: "0.0012",
  pending: null,
  harness: null,
  outcome: null,
  error: null,
  requested_by: "u",
  created_at: "2026-09-22T10:00:00Z",
  started_at: "2026-09-22T10:00:01Z",
  finished_at: null,
  ...extra,
});
const pauseRun = () =>
  run("waiting_for_human", {
    pending: {
      kind: "decision",
      step_id: stepId,
      seq: 4,
      harness: "browser",
      action: "submit",
      description: `Submit the invoice ${malicious}`,
      target: { label: `button: Submit invoice ${malicious}`, role: "button", id: 17 },
      risk: { irreversible: 0.91 },
      candidates: [
        { id: 17, label: `button: Submit invoice ${malicious}`, role: "button", p: 0.83 },
        { id: 18, label: "button: Clear", role: "button", p: 0.05 },
        { id: 19, label: "link: Export CSV", role: "link", p: 0.02 },
      ],
      chosen: 17,
      value_from: "supplier_invoices",
      reason: "irreversible",
    },
    harness: { device_id: "dev", user_id: "e", kinds: ["browser"], connected: true, screenshots: false },
  });
const trace = () => ({
  id: agentRunId,
  run_type: "workflow_execution",
  agent_key: "computer_use",
  status: "waiting",
  deal_id: null,
  created_at: "2026-09-22T10:00:00Z",
  events: [
    { seq: 1, event_type: "step", data: { message: "started" }, created_at: "2026-09-22T10:00:01Z" },
    { seq: 2, event_type: "tool_call", data: { tool: "observe", harness: "browser", candidates: 12 }, created_at: "2026-09-22T10:00:02Z" },
    { seq: 3, event_type: "tool_call", data: { tool: "type_value", harness: "browser", target: "textbox: Supplier name", value_input: "supplier_invoices", executed: true, ok: true, description: "Typed 14 characters" }, created_at: "2026-09-22T10:00:03Z" },
    { seq: 4, event_type: "tool_call", data: { tool: "click", harness: "browser", target: "button: Next", executed: true, ok: false, error: "target_not_found", description: "Clicked" }, created_at: "2026-09-22T10:00:04Z" },
  ],
});

async function settle(predicate) {
  for (let i = 0; i < 300; i++) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail("UI did not reach expected state");
}
const windows = [];
test.afterEach(() => {
  while (windows.length) windows.pop().close();
});
// The server derives these from the tenant and deal roles; the tests map the one
// `role` they set the way a workspace owner (tenant admin), member and viewer come out.
function permissionsFor(role) {
  const edits = role === "owner" || role === "member";
  return {
    import: edits,
    run_agents: edits,
    draft_workflow: edits,
    decide_workflow: role === "owner",
    run_workflow: role === "owner",
  };
}
function mount({ role = "owner", workflows = [workflow()], eligibility = null, runs = [], runView = null } = {}) {
  const state = { requests: [], runs: [...runs], runView };
  const dom = new JSDOM(html, { url: "https://vista.test/account/?view=workflows", runScripts: "outside-only" });
  windows.push(dom.window);
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
    if (path === `/api/deals/${company}/imports`) return Response.json({ role, permissions: permissionsFor(role), imports: [] });
    if (path === `/api/deals/${company}/import-datasets`) return Response.json({});
    if (path === "/api/runs?limit=100") return Response.json([]);
    if (path === "/api/findings?limit=200") return Response.json([]);
    if (path.startsWith("/api/usage")) return Response.json({ total_cost_usd: 0, total_input_tokens: 0, total_output_tokens: 0, runs: 0, groups: [] });
    if (path === "/api/synthetic/companies") return Response.json([]);
    if (path === "/api/recorder/reports?limit=100") return Response.json([]);
    if (path === "/api/workflows?limit=100") return Response.json(workflows);
    if (path === `/api/workflows/${workflowId}/versions/${versionId}/eligibility`)
      return Response.json(eligibility ?? { version_id: versionId, eligible: true, reasons: [], execution_available: true, availability: { available: true, reasons: [], harnesses: { documents: true, browser: true }, unmapped_tools: [], device: { device_id: "dev" } } });
    if (path === `/api/workflows/${workflowId}/runs?limit=20`) return Response.json(state.runs);
    if (path === `/api/workflows/${workflowId}/versions/${versionId}/runs` && method === "POST") {
      const created = run("waiting_for_harness", { pending: { kind: "offer", harness_kinds: ["browser"], harness: { connected: true } }, steps_used: 0 });
      state.runs = [created];
      state.runView = created;
      return Response.json(created, { status: 201 });
    }
    if (path === `/api/workflow-runs/${runId}`) return Response.json(state.runView);
    if (path === `/api/runs/${agentRunId}`) return Response.json(trace());
    if (path === `/api/workflow-runs/${runId}/decision` && method === "POST") {
      state.runView = run("running");
      return Response.json(state.runView);
    }
    if (path === `/api/workflow-runs/${runId}/stop` && method === "POST") {
      state.runView = run("stopped", { error: "Stopped from the company workspace", finished_at: "2026-09-22T10:05:00Z" });
      return Response.json(state.runView);
    }
    throw new Error("Unexpected URL " + method + " " + path);
  };
  dom.window.eval(`(async()=>{${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id) };
}

test("the owner can run an approved, available version: two-click confirm posts to the version route and opens the run", async () => {
  const { dom, state, $ } = mount();
  await settle(() => dom.window.document.querySelector("[data-run-workflow]"));
  const text = () => $("content").textContent;
  assert.ok(text().includes("Run in sandbox"));
  dom.window.document.querySelector("[data-run-workflow]").click();
  await settle(() => dom.window.document.querySelector("[data-run-confirm]"));
  assert.ok(text().includes("at most 40 steps"));
  assert.ok(text().includes("supplier_invoices"));
  dom.window.document.querySelector("[data-run-confirm]").click();
  await settle(() => state.requests.some((r) => r.method === "POST" && r.path.endsWith("/runs")));
  const post = state.requests.find((r) => r.method === "POST" && r.path.endsWith("/runs"));
  assert.equal(post.path, `/api/workflows/${workflowId}/versions/${versionId}/runs`);
  assert.deepEqual(post.body, { mode: "sandbox" });
  await settle(() => $("run-dialog").open && $("run-body").textContent.includes("Waiting for an employee's recorder"));
  assert.ok($("run-body").textContent.includes("A recorder is connected"));
  assert.ok($("content").textContent.includes("Waiting for recorder"), "the run list shows the new run");
  $("close-run").click();
});

test("when no recorder is connected there is no Run button and the reason is spelled out; members and viewers never see it", async () => {
  const unavailable = { version_id: versionId, eligible: true, reasons: [], execution_available: false, availability: { available: false, reasons: ["harness_not_connected"], harnesses: { browser: false }, unmapped_tools: [], device: null } };
  const { dom, $ } = mount({ eligibility: unavailable });
  await settle(() => $("content").textContent.includes("Cannot run now"));
  assert.equal(dom.window.document.querySelector("[data-run-workflow]"), null);
  assert.ok($("content").textContent.includes("No employee's recorder is connected"));
  for (const role of ["member", "viewer"]) {
    const m = mount({ role });
    await settle(() => m.$("content").textContent.includes("Only a workspace admin can run a workflow."));
    assert.equal(m.dom.window.document.querySelector("[data-run-workflow]"), null);
  }
  const draft = mount({ workflows: [workflow("draft")] });
  await settle(() => draft.$("content").textContent.includes("Enter bills"));
  assert.equal(draft.dom.window.document.querySelector("[data-run-workflow]"), null);
  assert.equal(draft.state.requests.some((r) => r.path.includes("/eligibility")), false, "no eligibility check for a draft");
});

test("a paused run shows what the agent wants to do, the candidates it saw with probabilities, and the input name — never the value; Approve posts the decision", async () => {
  const { dom, state, $ } = mount({ runs: [pauseRun()], runView: pauseRun() });
  await settle(() => dom.window.document.querySelector("[data-workflow-run]"));
  assert.ok($("content").textContent.includes("Needs your decision"));
  dom.window.document.querySelector("[data-workflow-run]").click();
  await settle(() => $("run-body").textContent.includes("The agent wants to"));
  const body = $("run-body");
  assert.ok(body.textContent.includes("irreversible 91%"));
  assert.ok(body.textContent.includes("value from input supplier_invoices"));
  assert.equal(body.querySelectorAll(".candidates li").length, 3);
  assert.ok(body.querySelector(".candidates li.chosen").textContent.includes("83%"));
  assert.ok(body.textContent.includes("button: Clear 5%"));
  assert.equal(body.querySelector("img"), null, "labels are escaped");
  assert.ok(body.innerHTML.includes("&lt;img"));
  assert.ok(body.textContent.includes("Screenshots stay on the employee's computer"));
  // the execution trace is rendered as a step list: harness, target, input name, error
  const steps = body.querySelectorAll(".step-list li");
  assert.equal(steps.length, 3);
  assert.ok(steps[1].textContent.includes("textbox: Supplier name"));
  assert.ok(steps[1].textContent.includes("value from input supplier_invoices"));
  assert.ok(steps[2].querySelector(".tag.danger"));
  body.querySelector('[data-decide-step="approve"]').click();
  await settle(() => state.requests.some((r) => r.path.endsWith("/decision")));
  const decision = state.requests.find((r) => r.path.endsWith("/decision"));
  assert.equal(decision.method, "POST");
  assert.equal(decision.path, `/api/workflow-runs/${runId}/decision`);
  assert.equal(decision.body.step_id, stepId);
  assert.equal(decision.body.decision, "approve");
  await settle(() => $("run-body").textContent.includes("Running") && !$("run-body").textContent.includes("The agent wants to"));
  $("close-run").click();
});

test("Stop run posts to the stop route and the dialog shows the stopped outcome; a member sees the pause but cannot decide", async () => {
  const { dom, state, $ } = mount({ runs: [pauseRun()], runView: pauseRun() });
  await settle(() => dom.window.document.querySelector("[data-workflow-run]"));
  dom.window.document.querySelector("[data-workflow-run]").click();
  await settle(() => $("run-body").querySelector("[data-stop-run]"));
  $("run-body").querySelector("[data-stop-run]").click();
  await settle(() => state.requests.some((r) => r.path.endsWith("/stop")));
  assert.equal(state.requests.find((r) => r.path.endsWith("/stop")).method, "POST");
  await settle(() => $("run-body").textContent.includes("Stopped"));
  $("close-run").click();
  const m = mount({ role: "member", runs: [pauseRun()], runView: pauseRun() });
  await settle(() => m.dom.window.document.querySelector("[data-workflow-run]"));
  m.dom.window.document.querySelector("[data-workflow-run]").click();
  await settle(() => m.$("run-body").textContent.includes("The agent wants to"));
  assert.equal(m.$("run-body").querySelector("[data-decide-step]"), null);
  assert.ok(m.$("run-body").textContent.includes("Only a workspace admin can decide"));
  m.$("close-run").click();
});

test("a finished run shows the independent verification outcome", async () => {
  const done = run("succeeded", { finished_at: "2026-09-22T10:06:00Z", steps_used: 9, outcome: { verified: true, goal_met: true, p_goal: 0.93, criteria: [{ criterion: "Every value written equals the source value", met: true, p: 0.9 }], matched: 1, checked: 1 } });
  const { dom, $ } = mount({ runs: [done], runView: done });
  await settle(() => dom.window.document.querySelector("[data-workflow-run]"));
  dom.window.document.querySelector("[data-workflow-run]").click();
  await settle(() => $("run-body").textContent.includes("Verified: 1 of 1 criteria met"));
  assert.ok($("run-body").textContent.includes("goal met 93%"));
  assert.ok($("run-body").textContent.includes("✓ Every value written"));
  $("close-run").click();
});

test("the Agents view lists the Computer Use Agent as a fifth, execution-layer agent that starts only from Workflows", async () => {
  const { dom, $ } = mount();
  await settle(() => $("content").textContent.includes("Enter bills"));
  dom.window.document.querySelector('[data-view="agents"]').click();
  await settle(() => $("content").textContent.includes("Computer Use Agent"));
  assert.ok($("content").textContent.includes("Five agents"));
  assert.equal($("content").querySelectorAll(".panel").length, 5);
  assert.ok($("content").textContent.includes("Starts only from Workflows"));
  assert.equal($("content").querySelector('[data-start="computer_use"]'), null);
});
