import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const html = fs.readFileSync(new URL("../public/account/index.html", import.meta.url), "utf8");
const source = fs.readFileSync(new URL("../public/account/app.js", import.meta.url), "utf8");
const company = "00000000-0000-0000-0000-000000000001";
const runId = "00000000-0000-0000-0000-00000000000a";
const findingId = "00000000-0000-0000-0000-00000000000d";
const workflowId = "00000000-0000-0000-0000-0000000000aa";
const versionId = "00000000-0000-0000-0000-0000000000bb";
const malicious = "<img src=x onerror=alert(1)>";

const definition = {
  goal: `Move the values the employee re-keys from Excel into Portal ${malicious}`,
  required_inputs: ["Excel export or sample", "Portal field list"],
  allowed_tools: ["read_source_records", "map_fields", "write_destination_records", "compare_with_manual_entry"],
  success_criteria: ["Every value written to Portal equals the source value"],
  environment: "sandbox",
  limits: { max_steps: 10, max_runtime_seconds: 300, max_cost_usd: "1.00" },
};
const actions = {
  instructions: [
    `Confirm with the employee what actually moves between Excel and Portal ${malicious}`,
    "Ask for one representative export or sample from Excel.",
    "Draft the workflow from the prefilled definition (button below).",
  ],
  draft_definition: definition,
  question: "What is being re-keyed?",
};
const finding = {
  id: findingId,
  run_id: runId,
  employee_id: null,
  agent_id: null,
  kind: "proposed_automation",
  title: `Automate data transfer: Excel → Portal ${malicious}`,
  detail: "Copied from Excel and pasted into Portal 5 times. Judged a recurring workflow at 95%; mechanical 2.7 of 3.",
  evidence: { refs: ["report:r1", "candidate:c4", `run:${runId}`], candidate: "c4", answer: "Vendor statements", actions },
  status: "open",
  created_at: "2026-09-22T09:00:00Z",
  company: "Recorder Company",
  agent_key: "recording_reviewer",
};
const version = (status, decision = null) => ({
  id: versionId,
  workflow_id: workflowId,
  number: 1,
  definition,
  definition_hash: "a".repeat(64),
  status,
  decision,
  created_by: "00000000-0000-0000-0000-000000000099",
  created_at: "2026-09-22T09:05:00Z",
});
const workflow = (status, decision) => ({
  id: workflowId,
  company_id: company,
  name: finding.title,
  latest_version: version(status, decision),
  created_by: "00000000-0000-0000-0000-000000000099",
  created_at: "2026-09-22T09:05:00Z",
});

async function settle(predicate) {
  for (let i = 0; i < 200; i++) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail("UI did not reach expected state");
}
function mount({ role = "owner", existing = [] } = {}) {
  const state = { requests: [], workflows: [...existing] };
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
    if (path === "/api/runs?limit=100")
      return Response.json([
        { id: runId, run_type: "submission_analysis", deal_id: company, status: "succeeded", agent_key: "recording_reviewer", company: "Recorder Company", created_at: "2026-09-22T08:00:00Z", events: [] },
      ]);
    if (path === "/api/findings?limit=200") return Response.json([finding]);
    if (path.startsWith("/api/usage")) return Response.json({ total_cost_usd: 0, total_input_tokens: 0, total_output_tokens: 0, runs: 0, groups: [] });
    if (path === "/api/synthetic/companies") return Response.json([]);
    if (path === "/api/recorder/reports?limit=100") return Response.json([]);
    if (path === "/api/workflows?limit=100") return Response.json(state.workflows);
    if (path === "/api/workflows" && method === "POST") {
      const created = workflow("draft");
      state.workflows = [created];
      return Response.json(created, { status: 201 });
    }
    if (path === `/api/findings/${findingId}` && method === "PATCH") return Response.json({ ...finding, status: "actioned" });
    if (path === `/api/workflows/${workflowId}/versions/${versionId}/decision` && method === "POST")
      return Response.json(version("approved", { id: "x", version_id: versionId, definition_hash: "a".repeat(64), decision: "approved", reason: "ok", decided_by: "u", decided_at: "2026-09-22T09:10:00Z" }));
    throw new Error("Unexpected URL " + method + " " + path);
  };
  dom.window.eval(`(async()=>{${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id) };
}

test("a proposed-automation finding shows what to do and drafts a workflow with one button", async () => {
  const ui = mount();
  try {
    await settle(() => ui.$("finding-count").textContent === "1" && ui.$("workflow-count").textContent === "0");
    ui.dom.window.document.querySelector('[data-view="findings"]').click();
    await settle(() => ui.$("content").querySelector("[data-agent-finding]"));
    ui.$("content").querySelector("[data-agent-finding]").click();
    await settle(() => ui.$("evidence-dialog").open);
    const body = ui.$("evidence-body");
    assert.equal(body.querySelectorAll("img").length, 0);
    assert.match(body.textContent, /What to do/);
    assert.match(body.textContent, /Asked the employee: What is being re-keyed\?/);
    assert.equal(body.querySelectorAll("details.what-to-do ol li").length, 3);
    assert.match(body.querySelector("details.what-to-do ol li").textContent, /Confirm with the employee what actually moves between Excel and Portal/);
    const draft = body.querySelector("[data-draft]");
    assert.ok(draft, "Draft workflow button is offered to an editor");
    draft.click();
    await settle(() => ui.state.requests.some((r) => r.method === "PATCH"));
    const post = ui.state.requests.find((r) => r.method === "POST" && r.path === "/api/workflows");
    assert.deepEqual(post.body, { name: finding.title, definition });
    assert.deepEqual(ui.state.requests.find((r) => r.method === "PATCH").body, { status: "actioned" });
    assert.match(body.querySelector("[data-draft-result]").textContent, /Draft v1 created — awaiting owner approval/);
    assert.equal(draft.disabled, true);
    assert.equal(ui.$("workflow-count").textContent, "1");
  } finally {
    ui.dom.window.close();
  }
});

test("the Workflows view lists versions and lets only the owner approve, version-bound", async () => {
  const ui = mount({ existing: [workflow("draft")] });
  try {
    await settle(() => ui.$("workflow-count").textContent === "1");
    ui.dom.window.document.querySelector('[data-view="workflows"]').click();
    await settle(() => /Workflows/.test(ui.$("view-name").textContent));
    const content = ui.$("content");
    assert.equal(content.querySelectorAll("img").length, 0);
    assert.match(content.textContent, /v1 · draft/);
    assert.match(content.textContent, /read_source_records, map_fields/);
    assert.match(content.textContent, /10 steps · 300 s · \$1\.00 per run · sandbox/);
    assert.match(content.textContent, /awaiting a decision/);
    content.querySelector('[data-decide="approved"]').click();
    await settle(() => /approved · ok/.test(ui.$("content").textContent));
    const decision = ui.state.requests.find((r) => r.method === "POST" && r.path.endsWith("/decision"));
    assert.equal(decision.path, `/api/workflows/${workflowId}/versions/${versionId}/decision`);
    assert.equal(decision.body.decision, "approved");
    assert.equal(ui.$("content").querySelector("[data-decide]"), null, "an approved version has no further decision buttons");
  } finally {
    ui.dom.window.close();
  }
});

test("viewers see instructions but no buttons, and members cannot approve", async () => {
  const viewer = mount({ role: "viewer", existing: [workflow("draft")] });
  try {
    await settle(() => viewer.$("finding-count").textContent === "1");
    viewer.dom.window.document.querySelector('[data-view="findings"]').click();
    await settle(() => viewer.$("content").querySelector("[data-agent-finding]"));
    viewer.$("content").querySelector("[data-agent-finding]").click();
    await settle(() => viewer.$("evidence-dialog").open);
    assert.match(viewer.$("evidence-body").textContent, /What to do/);
    assert.equal(viewer.$("evidence-body").querySelector("[data-draft]"), null);
    viewer.dom.window.document.querySelector('[data-view="workflows"]').click();
    await settle(() => /Workflows/.test(viewer.$("view-name").textContent));
    assert.match(viewer.$("content").textContent, /Only the workspace owner can approve or reject/);
    assert.equal(viewer.$("content").querySelector("[data-decide]"), null);
  } finally {
    viewer.dom.window.close();
  }
  const member = mount({ role: "member", existing: [workflow("draft")] });
  try {
    await settle(() => member.$("workflow-count").textContent === "1");
    member.dom.window.document.querySelector('[data-view="workflows"]').click();
    await settle(() => /Workflows/.test(member.$("view-name").textContent));
    assert.equal(member.$("content").querySelector("[data-decide]"), null);
  } finally {
    member.dom.window.close();
  }
});
