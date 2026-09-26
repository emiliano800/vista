import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";
const html = fs.readFileSync(
  new URL("../public/account/index.html", import.meta.url),
  "utf8",
);
import { source } from "./account-source.mjs";
const company = "00000000-0000-0000-0000-000000000001";
const other = "00000000-0000-0000-0000-000000000009";
const runId = "00000000-0000-0000-0000-00000000000a";
const portfolioRun = "00000000-0000-0000-0000-00000000000b";
const foreignRun = "00000000-0000-0000-0000-00000000000c";
const findingId = "00000000-0000-0000-0000-00000000000d";
const malicious = "<img src=x onerror=alert(1)>";
function runs() {
  return [
    {
      id: runId,
      run_type: "synthetic_discovery",
      deal_id: company,
      status: "succeeded",
      created_at: "2026-09-19T12:00:00Z",
      started_at: "2026-09-19T12:00:01Z",
      finished_at: "2026-09-19T12:00:09Z",
      company: "Ridgeway",
      division: "11_billing_ar",
      sector: "industrial_goods",
      agent_key: "file_reviewer",
      error: null,
      events: [],
    },
    {
      id: portfolioRun,
      run_type: "synthetic_analyze",
      deal_id: null,
      status: "failed",
      created_at: "2026-09-18T12:00:00Z",
      started_at: null,
      finished_at: "2026-09-18T12:00:05Z",
      sector: "industrial_goods",
      agent_key: "sector_merger",
      error: "model unavailable",
      events: [],
    },
    {
      id: foreignRun,
      run_type: "synthetic_discovery",
      deal_id: other,
      status: "succeeded",
      created_at: "2026-09-17T12:00:00Z",
      agent_key: "file_reviewer",
      error: null,
      events: [],
    },
  ];
}
const findings = [
  {
    id: findingId,
    run_id: runId,
    kind: "inefficiency",
    title: "Duplicate vendor invoices",
    detail: malicious,
    evidence: { company: "Ridgeway", refs: ["invoices.csv:12"] },
    status: "open",
    created_at: "2026-09-19T12:00:08Z",
    company: "Ridgeway",
    agent_key: "file_reviewer",
  },
  {
    id: "00000000-0000-0000-0000-00000000000e",
    run_id: foreignRun,
    kind: "observed_fact",
    title: "Belongs to another company",
    detail: "hidden",
    evidence: {},
    status: "open",
    created_at: "2026-09-17T12:00:08Z",
    agent_key: "file_reviewer",
  },
];
async function settle(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail("UI did not reach expected state");
}
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
function mount({ role = "owner" } = {}) {
  const state = { requests: [], runs: runs() };
  const dom = new JSDOM(html, {
    url: "https://vista.test/account/",
    runScripts: "outside-only",
  });
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  dom.window.VISTA_NAVIGATE = () => {};
  dom.window.fetch = async (path, options = {}) => {
    state.requests.push({ path, options });
    if (path === "/api/auth/me")
      return Response.json({ email: "cfo@ridgeway.com" });
    if (path === "/api/deals")
      return Response.json([
        { id: company, name: "Ridgeway Fasteners & Supply" },
      ]);
    if (path === `/api/deals/${company}/imports`)
      return Response.json({ role, permissions: permissionsFor(role), company: { id: "c-1", name: "Ridgeway", slug: "ridgeway" }, imports: [], openExceptions: [] });
    if (path === `/api/deals/${company}/import-datasets`) return Response.json({});
    if (path === "/api/runs?limit=100") return Response.json(state.runs);
    if (path === "/api/findings?limit=200") return Response.json(findings);
    if (path.startsWith("/api/usage?deal_id="))
      return Response.json({
        total_input_tokens: 900,
        total_output_tokens: 100,
        total_cost_usd: "0.0034",
        runs: 1,
        groups: [
          {
            key: { agent_key: "file_reviewer" },
            input_tokens: 900,
            output_tokens: 100,
            cost_usd: "0.0034",
            runs: 1,
          },
        ],
      });
    if (path === "/api/synthetic/companies")
      return Response.json([
        {
          slug: "ridgeway_fasteners_and_supply",
          name: "Ridgeway Fasteners & Supply",
          short: "Ridgeway",
          sector: "industrial_goods",
          tier: "low",
          divisions: ["11_billing_ar", "12_purchasing"],
        },
      ]);
    if (path === `/api/runs/${runId}`)
      return Response.json({
        ...state.runs[0],
        events: [
          {
            seq: 1,
            event_type: "model_call",
            data: { model: "gpt-4o-mini", input_tokens: 900 },
            created_at: "2026-09-19T12:00:05Z",
          },
        ],
      });
    if (path === `/api/findings/${findingId}` && options.method === "PATCH")
      return Response.json({
        ...findings[0],
        status: JSON.parse(options.body).status,
      });
    if (path === "/api/synthetic/discovery" && options.method === "POST") {
      const queued = {
        ...state.runs[0],
        id: "00000000-0000-0000-0000-0000000000ff",
        status: "queued",
        finished_at: null,
      };
      state.runs.unshift(queued);
      return Response.json(queued, { status: 201 });
    }
    throw new Error("Unexpected URL " + path);
  };
  dom.window.eval(`(async()=>{${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id) };
}
test("agents, runs and agent findings: scoped to the company, traced, reviewable", async () => {
  const ui = mount();
  const doc = ui.dom.window.document;
  try {
    await settle(() => ui.$("run-count").textContent === "2");
    assert.equal(ui.$("finding-count").textContent, "1");
    doc.querySelector('[data-view="agents"]').click();
    const text = ui.$("content").textContent;
    assert.match(text, /Recording Reviewer/);
    assert.match(text, /Sector Merger/);
    assert.match(text, /\$0\.0034/);
    assert.equal(doc.querySelectorAll("[data-start]").length, 3);
    assert.equal(
      doc.querySelector('[data-division="file_reviewer"]').value,
      "11_billing_ar",
    );

    doc.querySelector('[data-view="runs"]').click();
    assert.equal(doc.querySelectorAll("[data-run]").length, 2);
    assert.match(ui.$("content").textContent, /model unavailable/);
    assert.doesNotMatch(ui.$("content").textContent, /Belongs to another/);
    doc.querySelector(`[data-run="${runId}"]`).click();
    await settle(() => /model_call/.test(ui.$("run-body").textContent));
    assert.equal(ui.$("run-dialog").open, true);
    ui.$("close-run").click();

    doc.querySelector('[data-view="findings"]').click();
    assert.equal(doc.querySelectorAll("[data-agent-finding]").length, 1);
    assert.equal(doc.querySelectorAll("img").length, 0);
    doc
      .querySelector('[data-afilter="kind"][data-value="observed_fact"]')
      .click();
    assert.equal(doc.querySelectorAll("[data-agent-finding]").length, 0);
    doc
      .querySelector('[data-afilter="kind"][data-value="inefficiency"]')
      .click();
    doc.querySelector("[data-agent-finding]").click();
    await settle(() => ui.$("evidence-dialog").open);
    assert.match(ui.$("evidence-body").textContent, /invoices\.csv:12/);
    assert.ok(ui.$("evidence-body").textContent.includes(malicious));
    doc.querySelector('[data-tab="trace"]').click();
    await settle(() => /gpt-4o-mini/.test(ui.$("tab-trace").textContent));
    doc.querySelector('[data-set-status="reviewed"]').click();
    await settle(() => ui.$("finding-count").textContent === "0");
    assert.equal(
      JSON.parse(
        ui.state.requests.find((r) => r.options.method === "PATCH").options
          .body,
      ).status,
      "reviewed",
    );

    doc.querySelector('[data-view="agents"]').click();
    doc.querySelector('[data-start="file_reviewer"]').click();
    await settle(() => ui.$("run-count").textContent === "3");
    const posted = ui.state.requests.find((r) => r.options.method === "POST");
    assert.deepEqual(JSON.parse(posted.options.body), {
      company: "ridgeway_fasteners_and_supply",
      division: "11_billing_ar",
    });
    assert.match(ui.$("content").textContent, /Queued/);
  } finally {
    ui.dom.window.close();
  }
});
test("viewers see agents and runs but cannot start runs or review findings", async () => {
  const ui = mount({ role: "viewer" });
  const doc = ui.dom.window.document;
  try {
    await settle(() => ui.$("run-count").textContent === "2");
    doc.querySelector('[data-view="agents"]').click();
    assert.ok(
      [...doc.querySelectorAll("[data-start]")].every((b) => b.disabled),
    );
    doc.querySelector('[data-view="findings"]').click();
    doc.querySelector("[data-agent-finding]").click();
    await settle(() => ui.$("evidence-dialog").open);
    assert.equal(doc.querySelectorAll("[data-set-status]").length, 0);
  } finally {
    ui.dom.window.close();
  }
});
