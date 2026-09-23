import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM } from "jsdom";

// The company workspace imports through the canonical contract, scoped by its Deal:
// upload -> detect/map -> exceptions -> approve -> canonical records with provenance,
// then the File Reviewer runs over those records. Every server answer is a stub here;
// the test is about what the page asks for and what it shows.
const html = fs.readFileSync(new URL("../public/account/index.html", import.meta.url), "utf8");
const source = fs.readFileSync(new URL("../public/account/app.js", import.meta.url), "utf8");
const deal = "00000000-0000-0000-0000-000000000001";
const jobId = "00000000-0000-0000-0000-000000000002";
const malicious = "<img src=x onerror=alert(1)>";
const DATASETS = {
  invoices: {
    label: "Invoices / Accounts Receivable",
    entity: "invoice",
    fields: {
      source_invoice_number: { label: "Source invoice number", required: true },
      customer_name: { label: "Customer name", required: true },
      amount: { label: "Amount", required: false },
    },
  },
  other: { label: "Other", entity: null, fields: {} },
};
function job(status = "mapping_review", { decided = false } = {}) {
  return {
    id: jobId,
    companyId: "c-1",
    filename: "invoices.csv",
    status,
    dataset: "invoices",
    detection: { dataset: "invoices", confidence: 0.95 },
    sheet: null,
    columns: ["Invoice", "Customer", "Total"],
    recordsDetected: 1,
    recordsImported: status === "completed" ? 1 : 0,
    recordsNeedingReview: 0,
    recordsRejected: 0,
    error: null,
    createdAt: "2026-09-19T12:00:00Z",
    completedAt: status === "completed" ? "2026-09-19T12:05:00Z" : null,
    mappings: [
      { source: "Invoice", example: "INV-1", target: "source_invoice_number", confidence: 0.98, status: "Ready" },
      { source: "Customer", example: malicious, target: "customer_name", confidence: 0.7, status: "Review" },
      { source: "Total", example: "1,850.00", target: "amount", confidence: 0.95, status: "Ready" },
    ],
    exceptions:
      status === "mapping_review"
        ? []
        : [
            {
              id: "X-001",
              uuid: "00000000-0000-0000-0000-000000000009",
              importJobId: jobId,
              type: "possible_duplicate",
              description: "Two rows look like the same invoice.",
              left: malicious,
              right: "INV-1",
              leftRow: 2,
              rightRow: 3,
              dataset: "invoices",
              actions: ["Keep both", "Skip duplicate"],
              confidence: 0.91,
              decision: decided ? "Keep both" : null,
              open: !decided,
            },
          ],
    sample: [{ Invoice: "INV-1", Customer: malicious, Total: "1,850.00" }],
  };
}
const RECORDS = {
  customers: [],
  invoices: [
    {
      id: "i-1",
      companyId: "c-1",
      number: malicious,
      customerName: "Bayside Dental",
      amount: 1850,
      outstanding: 1850,
      status: "open",
      provenance: { file: "invoices.csv", row: 2, original: { Invoice: "INV-1", Customer: malicious }, confidence: 0.98, review: "auto-accepted" },
    },
  ],
};
async function settle(predicate) {
  for (let i = 0; i < 200; i++) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.fail("UI did not reach expected state");
}
function mount({ jobs = [], role = "owner", unlinked = false, signedIn = true } = {}) {
  const state = { jobs, records: jobs.some((j) => j.status === "completed") ? RECORDS : null, requests: [], navigations: [] };
  const dom = new JSDOM(html, { url: "https://vista.test/account/", runScripts: "outside-only" });
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  dom.window.VISTA_NAVIGATE = (url) => state.navigations.push(url);
  const base = `/api/deals/${deal}`;
  dom.window.fetch = async (path, options = {}) => {
    state.requests.push({ path, options, body: options.body ? JSON.parse(options.body) : undefined });
    if (!signedIn) return Response.json({ detail: "Sign in to continue" }, { status: 401 });
    if (path === "/api/auth/me") return Response.json({ email: "demo@meridianrisk.com" });
    if (path === "/api/deals") return Response.json([{ id: deal, name: "Meridian Risk Partners, LLC" }]);
    if (unlinked && path.startsWith(base))
      return Response.json({ detail: "This workspace is not linked to a portfolio company yet; ask your Vista contact to finish onboarding." }, { status: 409 });
    if (path === `${base}/imports` && options.method === "POST") {
      state.jobs = [job()];
      return Response.json(state.jobs[0], { status: 201 });
    }
    if (path === `${base}/imports`)
      return Response.json({
        role,
        company: { id: "c-1", name: "Meridian Risk Partners, LLC", slug: "meridian" },
        imports: state.jobs,
        openExceptions: state.jobs.flatMap((j) => (j.exceptions ?? []).filter((x) => x.open)),
      });
    if (path === `${base}/import-datasets`) return Response.json(DATASETS);
    if (path === `${base}/records`) return Response.json(state.records ?? {});
    if (path === `${base}/imports/${jobId}`) return Response.json(state.jobs[0]);
    if (path === `${base}/imports/${jobId}/mappings/approve`) {
      state.mappings = JSON.parse(options.body).mappings;
      state.jobs = [job("validating")];
      return Response.json(state.jobs[0]);
    }
    if (path === `${base}/imports/${jobId}/exceptions/X-001`) {
      state.jobs = [job("ready_to_import", { decided: true })];
      return Response.json(state.jobs[0]);
    }
    if (path === `${base}/imports/${jobId}/approve`) {
      state.jobs = [job("completed", { decided: true })];
      state.records = RECORDS;
      return Response.json(state.jobs[0]);
    }
    if (path === `${base}/review`) return Response.json({ run_id: "r-1", job_id: "j-1", request_id: "q-1" }, { status: 202 });
    if (path.startsWith("/api/runs")) return Response.json([]);
    if (path.startsWith("/api/findings")) return Response.json([]);
    if (path.startsWith("/api/usage"))
      return Response.json({ total_cost_usd: 0, total_input_tokens: 0, total_output_tokens: 0, runs: 0, groups: [] });
    if (path === "/api/synthetic/companies") return Response.json([]);
    if (path.startsWith("/api/recorder/reports")) return Response.json([]);
    throw new Error("Unexpected URL " + path);
  };
  dom.window.eval(`(async()=>{${source}\n})()`);
  return { dom, state, $: (id) => dom.window.document.getElementById(id), q: (sel) => dom.window.document.querySelector(sel) };
}
async function upload(ui) {
  await settle(() => ui.$("import-top").disabled === false);
  ui.$("import-top").click();
  const input = ui.$("file-input");
  Object.defineProperty(input, "files", {
    value: [new ui.dom.window.File(["Invoice,Customer,Total\nINV-1,Bayside,1850\n"], "invoices.csv", { type: "text/csv" })],
  });
  input.dispatchEvent(new ui.dom.window.Event("change"));
  ui.$("upload-files").click();
  await settle(() => !!ui.$("confirm-mapping"));
}

test("import flow: upload, confirm mapping, decide exception, approve, browse provenance, run the reviewer", async () => {
  const ui = mount();
  try {
    await upload(ui);
    const sent = ui.state.requests.find((r) => r.options.method === "POST" && r.path.endsWith("/imports"));
    assert.equal(sent.body.name, "invoices.csv");
    assert.equal(Buffer.from(sent.body.content, "base64").toString(), "Invoice,Customer,Total\nINV-1,Bayside,1850\n");
    assert.equal(ui.dom.window.document.querySelectorAll("img").length, 0, "example values are escaped");

    // A mapping under the auto threshold blocks confirmation until the employee decides it.
    ui.$("confirm-mapping").click();
    assert.match(ui.$("import-error").textContent, /marked Review/);
    ui.q("[data-confirm]").click();
    await settle(() => !!ui.$("confirm-mapping") && !ui.q("[data-confirm]"));
    ui.$("confirm-mapping").click();
    await settle(() => !!ui.$("approve-import"));
    const approved = ui.state.mappings.find((m) => m.source === "Customer");
    assert.equal(approved.confirmed, true);
    assert.equal(approved.target, "customer_name");

    // Exceptions are decided before (or after) approval; the decision goes to the API.
    assert.match(ui.$("import-body").textContent, /1 record still need/);
    ui.q('[data-decide="Keep both"]').click();
    await settle(() => ui.$("import-body").textContent.includes("Every ambiguous record has a decision"));
    ui.$("approve-import").click();
    await settle(() => !ui.$("import-dialog").open);
    assert.ok(ui.state.requests.some((r) => r.path.endsWith("/approve") && r.options.method === "POST"));
    assert.match(ui.$("message").textContent, /Import complete/);
    assert.equal(ui.$("source-count").textContent, "1");
    assert.match(ui.$("content").textContent, /1 imported records/);
    assert.equal(ui.dom.window.document.querySelectorAll("img").length, 0);

    // Provenance is one click from every canonical record.
    ui.q('[data-view="sources"]').click();
    ui.q("[data-source]").click();
    assert.equal(ui.$("source-dialog").open, true);
    assert.ok(ui.$("source-body").textContent.includes(malicious));
    ui.q("#source-body [data-row]").click();
    assert.match(ui.$("source-row").textContent, /invoices\.csv/);
    assert.equal(ui.$("source-body").querySelectorAll("img").length, 0);
    assert.equal(ui.dom.window.localStorage.length, 0, "nothing authoritative lives in the browser");

    // The File Reviewer runs over the imported records through the deal-scoped route.
    ui.q('[data-view="overview"]').click();
    ui.q("[data-review]").click();
    await settle(() => ui.state.requests.some((r) => r.path.endsWith("/review")));
    assert.equal(ui.state.requests.find((r) => r.path.endsWith("/review")).options.method, "POST");
    await settle(() => ui.$("view-name").textContent === "Runs");
  } finally {
    ui.dom.window.close();
  }
});

test("a pending import resumes at the mapping step; a viewer can browse but not import or review", async () => {
  const pending = mount({ jobs: [job()] });
  try {
    await settle(() => !!pending.q("[data-resume]"));
    pending.q("[data-resume]").click();
    await settle(() => !!pending.$("confirm-mapping"));
    assert.equal(pending.$("import-dialog").open, true);
  } finally {
    pending.dom.window.close();
  }
  const viewer = mount({ jobs: [job("completed", { decided: true })], role: "viewer" });
  try {
    await settle(() => viewer.$("content").textContent.includes("Your business"));
    assert.equal(viewer.$("import-top").disabled, true);
    assert.equal(viewer.dom.window.document.querySelectorAll("[data-review]").length, 0);
    viewer.q('[data-view="findings"]').click();
    assert.equal(viewer.dom.window.document.querySelectorAll("[data-exception]").length, 0);
  } finally {
    viewer.dom.window.close();
  }
});

test("signed out and unlinked workspaces have explicit states", async () => {
  const out = mount({ signedIn: false });
  try {
    await settle(() => out.state.navigations.includes("/signin/"));
  } finally {
    out.dom.window.close();
  }
  const unlinked = mount({ unlinked: true });
  try {
    await settle(() => !unlinked.$("message").hidden);
    assert.match(unlinked.$("message").textContent, /not linked to a portfolio company/);
    assert.equal(unlinked.$("import-top").disabled, true);
  } finally {
    unlinked.dom.window.close();
  }
});
